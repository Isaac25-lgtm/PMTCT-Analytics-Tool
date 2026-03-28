"""
Indicator calculation API routes.

Static routes are defined before parameterized routes to avoid accidental path
capture. HTMX callers receive HTML partials; non-HTMX callers receive JSON.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession, Registry, require_permission
from app.auth.permissions import Permission
from app.indicators.models import (
    IndicatorCategory,
    IndicatorResult,
    IndicatorResultSet,
    Periodicity,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


class IndicatorListItem(BaseModel):
    """Summary model for listing indicator definitions."""

    id: str
    name: str
    category: IndicatorCategory
    description: str
    result_type: str
    target: Optional[float] = None
    periodicity: Periodicity


class IndicatorListResponse(BaseModel):
    """Response model for indicator listings."""

    total: int
    indicators: list[IndicatorListItem]


class CalculationRequest(BaseModel):
    """Request body for indicator calculations."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    org_unit_name: Optional[str] = Field(default=None)
    include_children: bool = Field(default=False)
    categories: Optional[list[IndicatorCategory]] = Field(default=None)
    indicator_ids: Optional[list[str]] = Field(default=None)
    expected_pregnancies: Optional[int] = Field(default=None)


class CalculationResponse(BaseModel):
    """Response model for indicator calculations."""

    org_unit_uid: str
    org_unit_name: Optional[str]
    period: str
    total_indicators: int
    valid_indicators: int
    indicators_meeting_target: int
    results: list[IndicatorResult]


def is_htmx_request(request: Request) -> bool:
    """Return True when the current request comes from HTMX."""
    return request.headers.get("HX-Request", "").lower() == "true"


def parse_checkbox(value: Any) -> bool:
    """Convert form checkbox values into booleans."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def normalize_list(value: Any) -> list[str] | None:
    """Normalize repeated form values and JSON arrays into a clean list."""
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None
    if isinstance(value, tuple):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None

    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        items = [item.strip() for item in text.split(",") if item.strip()]
        return items or None
    return [text]


async def get_request_payload(request: Request) -> dict[str, Any]:
    """Read either JSON or form data without changing the API contract."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()

    if (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        payload: dict[str, Any] = {}
        for key in form.keys():
            values = form.getlist(key)
            payload[key] = values if len(values) > 1 else values[0]
        return payload

    return {}


def resolve_org_unit_name(
    session: CurrentSession,
    org_unit: str,
    supplied_name: str | None,
) -> str | None:
    """Resolve the organisation unit name from the session when not provided."""
    if supplied_name:
        return supplied_name

    for org_unit_item in session.credentials.org_units:
        if org_unit_item.get("id") == org_unit:
            return org_unit_item.get("name")
    return None


async def parse_calculation_request(request: Request) -> CalculationRequest:
    """Build the calculation request from JSON or HTMX form data."""
    payload = await get_request_payload(request)

    payload["include_children"] = parse_checkbox(payload.get("include_children"))

    categories = normalize_list(payload.get("categories"))
    if categories is None:
        payload.pop("categories", None)
    else:
        payload["categories"] = categories

    indicator_ids = normalize_list(payload.get("indicator_ids"))
    if indicator_ids is None:
        payload.pop("indicator_ids", None)
    else:
        payload["indicator_ids"] = indicator_ids

    if payload.get("expected_pregnancies") in {"", None}:
        payload.pop("expected_pregnancies", None)

    return CalculationRequest.model_validate(payload)


def group_results_by_category(
    results: list[IndicatorResult],
) -> list[dict[str, Any]]:
    """Group results by display category for the indicator partial template."""
    grouped: "OrderedDict[str, list[IndicatorResult]]" = OrderedDict()
    for result in results:
        category_name = result.category.name.replace("_", " ").title()
        grouped.setdefault(category_name, []).append(result)

    return [
        {"name": category_name, "results": category_results}
        for category_name, category_results in grouped.items()
    ]


@router.get("/", response_model=IndicatorListResponse)
async def list_indicators(
    registry: Registry,
    category: Optional[IndicatorCategory] = Query(default=None),
) -> IndicatorListResponse:
    """List available indicator definitions."""
    indicators = registry.get_by_category(category) if category else registry.get_all()

    return IndicatorListResponse(
        total=len(indicators),
        indicators=[
            IndicatorListItem(
                id=indicator.id,
                name=indicator.name,
                category=indicator.category,
                description=indicator.description,
                result_type=indicator.result_type.value,
                target=indicator.target,
                periodicity=indicator.periodicity,
            )
            for indicator in indicators
        ],
    )


@router.get("/categories")
async def list_categories() -> dict[str, list[dict[str, str]]]:
    """List supported indicator categories."""
    return {
        "categories": [
            {"id": category.value, "name": category.name.replace("_", " ").title()}
            for category in IndicatorCategory
        ]
    }


@router.post(
    "/calculate",
    response_model=CalculationResponse,
    dependencies=[Depends(require_permission(Permission.VIEW_INDICATORS))],
)
async def calculate_indicators(
    request: Request,
    calculator: Calculator,
    session: CurrentSession,
) -> Response | CalculationResponse:
    """Calculate indicators for an organisation unit and DHIS2 period."""
    calc_request = await parse_calculation_request(request)
    org_unit_name = resolve_org_unit_name(
        session,
        calc_request.org_unit,
        calc_request.org_unit_name,
    )

    if calc_request.expected_pregnancies is not None:
        calculator.set_expected_pregnancies(
            calc_request.org_unit,
            calc_request.expected_pregnancies,
        )

    try:
        if calc_request.indicator_ids:
            result_set = IndicatorResultSet(
                org_unit_uid=calc_request.org_unit,
                org_unit_name=org_unit_name,
                period=calc_request.period,
            )
            for indicator_id in calc_request.indicator_ids:
                result = await calculator.calculate_single(
                    indicator_id=indicator_id,
                    org_unit=calc_request.org_unit,
                    period=calc_request.period,
                    org_unit_name=org_unit_name,
                    include_children=calc_request.include_children,
                )
                result_set.add_result(result)
        else:
            result_set = await calculator.calculate_all(
                org_unit=calc_request.org_unit,
                period=calc_request.period,
                org_unit_name=org_unit_name,
                include_children=calc_request.include_children,
                categories=calc_request.categories,
            )
    except Exception as exc:
        logger.exception("Calculation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Calculation failed: {exc}",
        ) from exc

    if is_htmx_request(request):
        return templates.TemplateResponse(
            request,
            "components/indicator_results.html",
            {
                "request": request,
                "org_unit_uid": result_set.org_unit_uid,
                "org_unit_name": result_set.org_unit_name,
                "period": result_set.period,
                "total_indicators": result_set.total_indicators,
                "valid_indicators": result_set.valid_indicators,
                "indicators_meeting_target": result_set.indicators_meeting_target,
                "grouped_results": group_results_by_category(result_set.results),
            },
        )

    return CalculationResponse(
        org_unit_uid=result_set.org_unit_uid,
        org_unit_name=result_set.org_unit_name,
        period=result_set.period,
        total_indicators=result_set.total_indicators,
        valid_indicators=result_set.valid_indicators,
        indicators_meeting_target=result_set.indicators_meeting_target,
        results=result_set.results,
    )


@router.get(
    "/calculate/{indicator_id}",
    dependencies=[Depends(require_permission(Permission.VIEW_INDICATORS))],
)
async def calculate_single_indicator(
    indicator_id: str,
    calculator: Calculator,
    session: CurrentSession,
    org_unit: str = Query(..., description="Organisation unit UID"),
    period: str = Query(..., description="DHIS2 period"),
    org_unit_name: Optional[str] = Query(default=None),
    include_children: bool = Query(default=False),
    expected_pregnancies: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    """Calculate a single indicator via query parameters."""
    resolved_name = resolve_org_unit_name(session, org_unit, org_unit_name)

    if expected_pregnancies is not None:
        calculator.set_expected_pregnancies(org_unit, expected_pregnancies)

    try:
        result = await calculator.calculate_single(
            indicator_id=indicator_id,
            org_unit=org_unit,
            period=period,
            org_unit_name=resolved_name,
            include_children=include_children,
        )
    except Exception as exc:
        logger.exception("Single indicator calculation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Calculation failed: {exc}",
        ) from exc

    return result.model_dump()


@router.get("/{indicator_id}")
async def get_indicator(indicator_id: str, registry: Registry) -> dict[str, Any]:
    """Return a single indicator definition."""
    indicator = registry.get(indicator_id)
    if not indicator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Indicator not found: {indicator_id}",
        )

    return {
        "id": indicator.id,
        "name": indicator.name,
        "category": indicator.category.value,
        "description": indicator.description,
        "numerator": indicator.numerator.model_dump() if indicator.numerator else None,
        "denominator": indicator.denominator.model_dump() if indicator.denominator else None,
        "result_type": indicator.result_type.value,
        "target": indicator.target,
        "periodicity": indicator.periodicity.value,
        "notes": indicator.notes,
    }
