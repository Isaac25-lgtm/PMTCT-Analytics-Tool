"""
Report generation API routes.

Do not change Prompt 2 or Prompt 3 interfaces here. These routes consume the
existing session-backed calculator exactly as already defined and add only the
Prompt 5 HTML partial layer for HTMX.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession
from app.indicators.models import IndicatorCategory, IndicatorResult

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

DEFAULT_PERIODICITY = "monthly"
DEFAULT_HISTORY_DEPTH = "12m"
FULL_HISTORY_START_DATE = date(2000, 1, 1)
PERIODICITY_OPTIONS = [
    {"id": "monthly", "label": "Monthly"},
    {"id": "weekly", "label": "Weekly"},
]
HISTORY_DEPTH_OPTIONS = [
    {"id": "3m", "label": "3 months"},
    {"id": "12m", "label": "12 months"},
    {"id": "36m", "label": "36 months"},
    {"id": "full", "label": "Full history"},
]


class ScoreCardRequest(BaseModel):
    """Request body for WHO validation scorecard generation."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    org_unit_name: Optional[str] = None
    expected_pregnancies: Optional[int] = None


class ScoreCardIndicator(BaseModel):
    """Single indicator entry in the WHO validation scorecard."""

    id: str
    name: str
    value: Optional[float]
    formatted_value: str
    target: Optional[float]
    meets_target: Optional[bool]
    status: str


class ScoreCardResponse(BaseModel):
    """WHO validation scorecard response."""

    org_unit: str
    org_unit_name: Optional[str]
    period: str
    generated_at: datetime
    indicators: list[ScoreCardIndicator]
    summary: dict[str, float | int]


class CascadeRequest(BaseModel):
    """Request body for cascade report generation."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    cascade_type: str = Field(..., description="Cascade type: hiv, hbv, or syphilis")
    org_unit_name: Optional[str] = None


class CascadeStep(BaseModel):
    """Single step in a cascade response."""

    indicator_id: str
    name: str
    count: Optional[float]
    percentage: Optional[float]
    formatted_value: str


class CascadeResponse(BaseModel):
    """Cascade report response."""

    org_unit: str
    org_unit_name: Optional[str]
    period: str
    cascade_type: str
    generated_at: datetime
    steps: list[CascadeStep]


class SupplyStatusRequest(BaseModel):
    """Request body for supply chain status generation."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    org_unit_name: Optional[str] = None


class CommodityStatus(BaseModel):
    """Status of a single commodity."""

    commodity: str
    consumed: Optional[float]
    stockout_days: Optional[float]
    stock_on_hand: Optional[float]
    days_of_use: Optional[float]
    status: str


class SupplyStatusResponse(BaseModel):
    """Supply chain status response."""

    org_unit: str
    org_unit_name: Optional[str]
    period: str
    generated_at: datetime
    commodities: list[CommodityStatus]
    # Enriched optional fields (Prompt 16)
    summary: Optional[dict] = None
    unmapped_commodities: Optional[list[dict]] = None
    alerts: Optional[list[dict]] = None
    validation: Optional[list[dict]] = None
    forecasts: Optional[list[dict]] = None


def is_htmx_request(request: Request) -> bool:
    """Return True when the current request comes from HTMX."""
    return request.headers.get("HX-Request", "").lower() == "true"


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
    """Resolve the organisation unit name from the session when not supplied."""
    if supplied_name:
        return supplied_name

    for org_unit_item in session.credentials.org_units:
        if org_unit_item.get("id") == org_unit:
            return org_unit_item.get("name")
    return None


async def parse_model_request(model_cls: type[BaseModel], request: Request) -> BaseModel:
    """Validate JSON or form data into a pydantic request model."""
    payload = await get_request_payload(request)
    if payload.get("expected_pregnancies") in {"", None}:
        payload.pop("expected_pregnancies", None)
    return model_cls.model_validate(payload)


def get_status_color(result: IndicatorResult) -> str:
    """Return scorecard traffic-light status based on target gap."""
    if not result.is_valid or result.target is None or result.result_value is None:
        return "unknown"

    gap = result.target - result.result_value
    if gap <= 0:
        return "success"
    if gap <= 10:
        return "warning"
    return "danger"


def get_supply_status(days_of_use: Optional[float]) -> str:
    """Return stock status for a commodity."""
    if days_of_use is None:
        return "unknown"
    if days_of_use <= 0:
        return "stockout"
    if days_of_use < 14:
        return "critical"
    if days_of_use < 30:
        return "low"
    return "ok"


def safe_get_result_value(
    result_map: dict[str, IndicatorResult],
    indicator_id: str,
) -> Optional[float]:
    """Safely return an indicator's calculated result value."""
    result = result_map.get(indicator_id)
    return result.result_value if result else None


def safe_get_numerator(
    result_map: dict[str, IndicatorResult],
    indicator_id: str,
) -> Optional[float]:
    """Safely return an indicator's numerator value."""
    result = result_map.get(indicator_id)
    return result.numerator_value if result else None


def normalize_periodicity(periodicity: str) -> str:
    """Normalize and validate periodicity values."""
    normalized = periodicity.strip().lower()
    if normalized not in {"monthly", "weekly", "quarterly"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown periodicity: {periodicity}",
        )
    return normalized


def resolve_period_count(
    periodicity: str,
    history_depth: str,
    today: date,
) -> int:
    """Map a history-depth selection to a count of periods."""
    periodicity = normalize_periodicity(periodicity)
    history_depth = history_depth.strip().lower()
    if history_depth not in {"3m", "12m", "36m", "full"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown history depth: {history_depth}",
        )

    if history_depth == "full":
        if periodicity == "monthly":
            return max(
                1,
                (today.year - FULL_HISTORY_START_DATE.year) * 12
                + (today.month - FULL_HISTORY_START_DATE.month)
                + 1,
            )
        if periodicity == "weekly":
            return max(1, ceil((today - FULL_HISTORY_START_DATE).days / 7))
        return max(
            1,
            (today.year - FULL_HISTORY_START_DATE.year) * 4
            + (((today.month - 1) // 3) + 1)
            - (((FULL_HISTORY_START_DATE.month - 1) // 3) + 1)
            + 1,
        )

    monthly_map = {"3m": 3, "12m": 12, "36m": 36}
    if periodicity == "monthly":
        return monthly_map[history_depth]
    if periodicity == "weekly":
        return {"3m": 13, "12m": 52, "36m": 156}[history_depth]
    return {"3m": 1, "12m": 4, "36m": 12}[history_depth]


def build_periods(
    periodicity: str = DEFAULT_PERIODICITY,
    history_depth: str = DEFAULT_HISTORY_DEPTH,
    *,
    count: int | None = None,
    today: date | None = None,
) -> list[dict[str, str]]:
    """Generate DHIS2 period choices for the UI."""
    today = today or date.today()
    periodicity = normalize_periodicity(periodicity)
    count = count or resolve_period_count(periodicity, history_depth, today)

    periods: list[dict[str, str]] = []

    if periodicity == "monthly":
        for index in range(count):
            year = today.year
            month = today.month - index
            while month <= 0:
                month += 12
                year -= 1
            periods.append(
                {
                    "id": f"{year}{month:02d}",
                    "name": date(year, month, 1).strftime("%B %Y"),
                }
            )
        return periods

    if periodicity == "weekly":
        current = today - timedelta(days=today.weekday())
        for index in range(count):
            week_start = current - timedelta(weeks=index)
            iso_cal = week_start.isocalendar()
            periods.append(
                {
                    "id": f"{iso_cal.year}W{iso_cal.week:02d}",
                    "name": f"Week {iso_cal.week}, {iso_cal.year}",
                }
            )
        return periods

    current_quarter = (today.month - 1) // 3 + 1
    current_year = today.year
    for index in range(count):
        quarter = current_quarter - index
        year = current_year
        while quarter <= 0:
            quarter += 4
            year -= 1
        periods.append({"id": f"{year}Q{quarter}", "name": f"Q{quarter} {year}"})
    return periods


def render_period_options(periods: list[dict[str, str]]) -> str:
    """Render a lightweight HTML option list for HTMX callers."""
    return "".join(
        f'<option value="{period["id"]}">{period["name"]}</option>' for period in periods
    )


@router.post("/scorecard", response_model=ScoreCardResponse)
async def generate_scorecard(
    request: Request,
    calculator: Calculator,
    session: CurrentSession,
) -> Response | ScoreCardResponse:
    """Generate the six WHO validation indicators as a scorecard."""
    scorecard_request = ScoreCardRequest.model_validate(
        (await parse_model_request(ScoreCardRequest, request)).model_dump()
    )
    org_unit_name = resolve_org_unit_name(
        session,
        scorecard_request.org_unit,
        scorecard_request.org_unit_name,
    )

    if scorecard_request.expected_pregnancies is not None:
        calculator.set_expected_pregnancies(
            scorecard_request.org_unit,
            scorecard_request.expected_pregnancies,
        )

    result_set = await calculator.calculate_all(
        org_unit=scorecard_request.org_unit,
        period=scorecard_request.period,
        org_unit_name=org_unit_name,
        categories=[IndicatorCategory.WHO_VALIDATION],
    )

    indicators: list[ScoreCardIndicator] = []
    meeting_target = 0
    total_with_target = 0

    for result in result_set.results:
        indicators.append(
            ScoreCardIndicator(
                id=result.indicator_id,
                name=result.indicator_name,
                value=result.result_value,
                formatted_value=result.formatted_result,
                target=result.target,
                meets_target=result.meets_target,
                status=get_status_color(result),
            )
        )
        if result.target is not None:
            total_with_target += 1
            if result.meets_target:
                meeting_target += 1

    summary = {
        "total": len(indicators),
        "meeting_target": meeting_target,
        "total_with_target": total_with_target,
        "score_pct": (meeting_target / total_with_target * 100) if total_with_target else 0,
    }
    generated_at = datetime.now(timezone.utc)

    if is_htmx_request(request):
        return templates.TemplateResponse(
            request,
            "components/scorecard_results.html",
            {
                "request": request,
                "indicators": [indicator.model_dump() for indicator in indicators],
                "summary": summary,
                "org_unit": scorecard_request.org_unit,
                "org_unit_name": org_unit_name,
                "period": scorecard_request.period,
                "expected_pregnancies": scorecard_request.expected_pregnancies,
                "generated_at": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
            },
        )

    return ScoreCardResponse(
        org_unit=scorecard_request.org_unit,
        org_unit_name=org_unit_name,
        period=scorecard_request.period,
        generated_at=generated_at,
        indicators=indicators,
        summary=summary,
    )


@router.post("/cascade", response_model=CascadeResponse)
async def generate_cascade(
    request: Request,
    calculator: Calculator,
    session: CurrentSession,
) -> Response | CascadeResponse:
    """Generate an HIV, HBV, or syphilis cascade report."""
    cascade_request = CascadeRequest.model_validate(
        (await parse_model_request(CascadeRequest, request)).model_dump()
    )
    org_unit_name = resolve_org_unit_name(
        session,
        cascade_request.org_unit,
        cascade_request.org_unit_name,
    )

    cascade_configs = {
        "hiv": {
            "category": IndicatorCategory.HIV_CASCADE,
            "order": [
                "HIV-01",
                "HIV-02",
                "HIV-03",
                "HIV-04",
                "HIV-05",
                "HIV-06",
                "HIV-07",
                "HIV-08",
                "HIV-09",
                "HIV-10",
            ],
        },
        "hbv": {
            "category": IndicatorCategory.HBV_CASCADE,
            "order": ["HBV-01", "HBV-02", "HBV-05", "HBV-07", "HBV-08"],
        },
        "syphilis": {
            "category": IndicatorCategory.SYPHILIS,
            "order": ["SYP-01"],
            "include_who": ["VAL-04", "VAL-05"],
        },
    }

    config = cascade_configs.get(cascade_request.cascade_type.lower())
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown cascade type: {cascade_request.cascade_type}",
        )

    categories = [config["category"]]
    if cascade_request.cascade_type.lower() == "syphilis":
        categories.append(IndicatorCategory.WHO_VALIDATION)

    result_set = await calculator.calculate_all(
        org_unit=cascade_request.org_unit,
        period=cascade_request.period,
        org_unit_name=org_unit_name,
        categories=categories,
    )

    order = list(config["order"])
    if "include_who" in config:
        order = list(config["include_who"]) + order

    result_map = {result.indicator_id: result for result in result_set.results}
    steps = [
        CascadeStep(
            indicator_id=result_map[indicator_id].indicator_id,
            name=result_map[indicator_id].indicator_name,
            count=result_map[indicator_id].numerator_value,
            percentage=result_map[indicator_id].result_value,
            formatted_value=result_map[indicator_id].formatted_result,
        )
        for indicator_id in order
        if indicator_id in result_map
    ]
    generated_at = datetime.now(timezone.utc)

    if is_htmx_request(request):
        return templates.TemplateResponse(
            request,
            "components/cascade_results.html",
            {
                "request": request,
                "cascade_type": cascade_request.cascade_type,
                "org_unit": cascade_request.org_unit,
                "org_unit_name": org_unit_name,
                "period": cascade_request.period,
                "steps": [step.model_dump() for step in steps],
                "steps_json": json.dumps([step.model_dump() for step in steps]),
                "generated_at": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
            },
        )

    return CascadeResponse(
        org_unit=cascade_request.org_unit,
        org_unit_name=org_unit_name,
        period=cascade_request.period,
        cascade_type=cascade_request.cascade_type,
        generated_at=generated_at,
        steps=steps,
    )


@router.post("/supply-status", response_model=SupplyStatusResponse)
async def generate_supply_status(
    request: Request,
    calculator: Calculator,
    session: CurrentSession,
) -> Response | SupplyStatusResponse:
    """Generate the PMTCT commodity stock status report.

    Uses the Prompt 16 SupplyService for enriched data while keeping the
    existing JSON/HTMX response shape fully backward-compatible.
    """
    supply_request = SupplyStatusRequest.model_validate(
        (await parse_model_request(SupplyStatusRequest, request)).model_dump()
    )
    org_unit_name = resolve_org_unit_name(
        session,
        supply_request.org_unit,
        supply_request.org_unit_name,
    )

    from app.supply.service import SupplyService

    supply_svc = SupplyService(session=session, calculator=calculator)
    report = await supply_svc.get_supply_report(
        org_unit=supply_request.org_unit,
        period=supply_request.period,
        org_unit_name=org_unit_name,
    )

    # Legacy-compatible commodity dicts for existing templates/tests
    legacy_commodities = report.to_legacy_commodities()
    commodities = [CommodityStatus(**row) for row in legacy_commodities]
    generated_at = report.generated_at
    generated_at_str = generated_at.strftime("%Y-%m-%d %H:%M UTC")

    # Build enriched per-commodity dicts (shared by HTMX and JSON)
    enriched_rows = []
    all_alerts: list[dict] = []
    all_validation: list[dict] = []
    all_forecasts: list[dict] = []
    for ec in report.commodities:
        commodity_alerts = [
            {"severity": a.severity.value, "alert_type": a.alert_type, "message": a.message}
            for a in ec.alerts
        ]
        commodity_validation = [
            {"severity": f.severity.value, "field": f.field_name, "message": f.message}
            for f in ec.validation
        ]
        forecast_dict = {
            "commodity": ec.commodity.name,
            "horizons": ec.forecast.horizons,
            "reorder_needed": ec.forecast.reorder_needed,
            "reorder_quantity": ec.forecast.reorder_quantity,
            "confidence": ec.forecast.confidence,
        }
        enriched_rows.append({
            "commodity": ec.commodity.name,
            "consumed": ec.snapshot.consumed,
            "stockout_days": ec.snapshot.stockout_days,
            "stock_on_hand": ec.snapshot.stock_on_hand,
            "days_of_use": ec.metrics.days_of_use,
            "status": ec.metrics.status.value,
            "expired": ec.snapshot.expired,
            "months_of_stock": ec.metrics.months_of_stock,
            "adjusted_adc": ec.metrics.adjusted_adc,
            "forecast": forecast_dict,
            "alerts": commodity_alerts,
            "validation": commodity_validation,
        })
        all_alerts.extend(commodity_alerts)
        all_validation.extend(commodity_validation)
        all_forecasts.append(forecast_dict)

    unmapped_list = [{"name": c.name, "unit": c.unit} for c in report.unmapped_commodities]

    if is_htmx_request(request):
        return templates.TemplateResponse(
            request,
            "components/supply_results.html",
            {
                "request": request,
                "org_unit": supply_request.org_unit,
                "org_unit_name": org_unit_name,
                "period": supply_request.period,
                "commodities": enriched_rows,
                "unmapped_commodities": unmapped_list,
                "summary": report.summary,
                "generated_at": generated_at_str,
            },
        )

    return SupplyStatusResponse(
        org_unit=supply_request.org_unit,
        org_unit_name=org_unit_name,
        period=supply_request.period,
        generated_at=generated_at,
        commodities=commodities,
        summary=report.summary,
        unmapped_commodities=unmapped_list,
        alerts=all_alerts,
        validation=all_validation,
        forecasts=all_forecasts,
    )


@router.get("/org-units")
async def get_user_org_units(session: CurrentSession) -> dict[str, list[dict[str, Any]]]:
    """Return the organisation units assigned to the logged-in user."""
    return {"org_units": session.credentials.org_units if session.credentials else []}


@router.get("/periods", response_model=None)
async def get_available_periods(
    request: Request,
    periodicity: str = Query(default=DEFAULT_PERIODICITY),
    history_depth: str = Query(default=DEFAULT_HISTORY_DEPTH),
    count: int | None = Query(default=None, ge=1, le=5000),
) -> Any:
    """Generate DHIS2 period selections for the UI."""
    periods = build_periods(
        periodicity=periodicity,
        history_depth=history_depth,
        count=count,
    )

    if is_htmx_request(request):
        return HTMLResponse(content=render_period_options(periods))

    return {"periods": periods}
