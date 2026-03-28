"""
Trend analysis routes for monthly multi-period comparison.

HTMX requests receive HTML partials.
Non-HTMX requests receive JSON only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.api.deps import Calculator, CurrentSession, Registry, SessCache, require_permission
from app.auth.permissions import Permission
from app.api.routes.indicators import get_request_payload, normalize_list, resolve_org_unit_name
from app.core.cache_keys import CacheKeys, get_cache_ttl
from app.indicators.models import IndicatorResult, Periodicity
from app.services.trends import IndicatorTrend, TrendService

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_permission(Permission.VIEW_TRENDS))])
templates = Jinja2Templates(directory="app/templates")
trend_service = TrendService()


class TrendRequest(BaseModel):
    """Request payload for monthly trend analysis."""

    indicator_ids: list[str] = Field(..., min_length=1, max_length=10)
    org_unit: str = Field(..., description="Organisation unit UID")
    end_period: str = Field(..., description="Monthly DHIS2 period in YYYYMM format")
    num_periods: int = Field(default=6, ge=3, le=12)
    org_unit_name: Optional[str] = None

    @field_validator("indicator_ids")
    @classmethod
    def validate_indicator_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Duplicate indicator IDs are not allowed")
        if not cleaned:
            raise ValueError("At least one indicator must be selected")
        return cleaned

    @field_validator("end_period")
    @classmethod
    def validate_end_period(cls, value: str) -> str:
        return TrendService.validate_monthly_period(value)


class PeriodValueResponse(BaseModel):
    """One indicator value for one period."""

    period: str
    period_label: str
    value: Optional[float]
    numerator: Optional[float]
    denominator: Optional[float]
    is_valid: bool


class TrendSummaryResponse(BaseModel):
    """Summary statistics for one indicator trend."""

    direction: str
    start_value: Optional[float]
    end_value: Optional[float]
    absolute_change: Optional[float]
    percent_change: Optional[float]
    average: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    valid_periods: int
    total_periods: int
    meets_target_count: int
    target: Optional[float]


class IndicatorTrendResponse(BaseModel):
    """Trend response for one indicator."""

    indicator_id: str
    indicator_name: str
    category: str
    target: Optional[float]
    result_type: str
    values: list[PeriodValueResponse]
    summary: TrendSummaryResponse


class TrendResponse(BaseModel):
    """Trend analysis response payload."""

    org_unit: str
    org_unit_name: Optional[str]
    periods: list[str]
    period_labels: list[str]
    trends: list[IndicatorTrendResponse]
    generated_at: str


def is_htmx_request(request: Request) -> bool:
    """Return True when the current request comes from HTMX."""
    return request.headers.get("HX-Request", "").lower() == "true"


async def parse_trend_request(request: Request) -> TrendRequest:
    """Parse JSON or form payloads into a validated trend request."""
    payload = await get_request_payload(request)

    indicator_ids = normalize_list(payload.get("indicator_ids"))
    if indicator_ids is None:
        payload["indicator_ids"] = []
    else:
        payload["indicator_ids"] = indicator_ids

    if payload.get("org_unit_name") in {"", None}:
        payload.pop("org_unit_name", None)

    try:
        return TrendRequest.model_validate(payload)
    except ValidationError as exc:
        detail = [
            {
                "loc": error.get("loc", []),
                "msg": error.get("msg", "Invalid request"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        ) from exc


def trend_to_response(trend: IndicatorTrend) -> IndicatorTrendResponse:
    """Convert a service trend object into the API response model."""
    return IndicatorTrendResponse(
        indicator_id=trend.indicator_id,
        indicator_name=trend.indicator_name,
        category=trend.category,
        target=trend.target,
        result_type=trend.result_type.value,
        values=[
            PeriodValueResponse(
                period=value.period,
                period_label=value.period_label,
                value=value.value,
                numerator=value.numerator,
                denominator=value.denominator,
                is_valid=value.is_valid,
            )
            for value in trend.values
        ],
        summary=TrendSummaryResponse(
            direction=trend.summary.direction.value,
            start_value=trend.summary.start_value,
            end_value=trend.summary.end_value,
            absolute_change=trend.summary.absolute_change,
            percent_change=trend.summary.percent_change,
            average=trend.summary.average,
            minimum=trend.summary.minimum,
            maximum=trend.summary.maximum,
            valid_periods=trend.summary.valid_periods,
            total_periods=trend.summary.total_periods,
            meets_target_count=trend.summary.meets_target_count,
            target=trend.summary.target,
        ),
    )


@router.post("/analyze", response_model=TrendResponse)
async def analyze_trends(
    request: Request,
    calculator: Calculator,
    registry: Registry,
    session: CurrentSession,
    session_cache: SessCache,
) -> Response | TrendResponse:
    """Analyze selected monthly indicators across multiple periods."""
    trend_request = await parse_trend_request(request)
    org_unit_name = resolve_org_unit_name(
        session,
        trend_request.org_unit,
        trend_request.org_unit_name,
    )

    definitions: dict[str, Any] = {}
    weekly_indicators: list[str] = []
    for indicator_id in trend_request.indicator_ids:
        definition = registry.get(indicator_id)
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown indicator: {indicator_id}",
            )
        if definition.periodicity == Periodicity.WEEKLY:
            weekly_indicators.append(indicator_id)
        elif definition.periodicity != Periodicity.MONTHLY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Only monthly indicators are supported in trends: {indicator_id}",
            )
        definitions[indicator_id] = definition

    if weekly_indicators:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Weekly indicators cannot be used in trends: {', '.join(weekly_indicators)}",
        )

    periods = trend_service.generate_monthly_periods(
        end_period=trend_request.end_period,
        num_periods=trend_request.num_periods,
    )
    period_labels = [trend_service.format_period_label(period) for period in periods]

    cache_key = CacheKeys.trend_analysis(
        org_unit=trend_request.org_unit,
        end_period=trend_request.end_period,
        num_periods=trend_request.num_periods,
        indicator_ids=trend_request.indicator_ids,
    )

    async def build_trend_response() -> dict[str, Any]:
        trends: list[IndicatorTrend] = []
        for indicator_id in trend_request.indicator_ids:
            definition = definitions[indicator_id]
            period_results: list[tuple[str, IndicatorResult | None]] = []

            for period in periods:
                try:
                    result = await calculator.calculate_single(
                        indicator_id=indicator_id,
                        org_unit=trend_request.org_unit,
                        period=period,
                        org_unit_name=org_unit_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Trend calculation failed for %s in %s: %s",
                        indicator_id,
                        period,
                        exc,
                    )
                    result = None

                period_results.append((period, result))

            trends.append(
                trend_service.build_indicator_trend(
                    indicator_id=definition.id,
                    indicator_name=definition.name,
                    category=definition.category.value,
                    target=definition.target,
                    result_type=definition.result_type,
                    period_results=period_results,
                )
            )

        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        response_data = TrendResponse(
            org_unit=trend_request.org_unit,
            org_unit_name=org_unit_name,
            periods=periods,
            period_labels=period_labels,
            trends=[trend_to_response(trend) for trend in trends],
            generated_at=generated_at,
        )
        return response_data.model_dump()

    payload = await session_cache.get_or_set_async(
        cache_key,
        build_trend_response,
        ttl=get_cache_ttl("trends"),
    )
    response_data = TrendResponse.model_validate(payload).model_copy(
        update={"org_unit_name": org_unit_name}
    )

    if is_htmx_request(request):
        trend_payloads = [trend.model_dump() for trend in response_data.trends]
        chart_data = [
            {
                "indicator_id": trend.indicator_id,
                "indicator_name": trend.indicator_name,
                "result_type": trend.result_type,
                "target": trend.target,
                "values": [value.value for value in trend.values],
            }
            for trend in response_data.trends
        ]

        return templates.TemplateResponse(
            request,
            "components/trend_results.html",
            {
                "request": request,
                "org_unit": trend_request.org_unit,
                "org_unit_name": org_unit_name,
                "periods": periods,
                "period_labels": period_labels,
                "trends": trend_payloads,
                "chart_data_json": json.dumps(chart_data),
                "period_labels_json": json.dumps(period_labels),
                "generated_at": response_data.generated_at,
            },
        )

    return response_data


@router.get("/periods")
async def get_available_periods(
    count: int = Query(default=12, ge=3, le=12),
) -> dict[str, list[dict[str, str]]]:
    """Return recent monthly periods for the trends UI dropdown."""
    return {"periods": trend_service.build_monthly_period_options(count=count)}
