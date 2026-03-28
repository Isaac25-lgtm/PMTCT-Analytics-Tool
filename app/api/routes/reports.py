"""
Report generation API routes.

Do not change Prompt 2 or Prompt 3 interfaces here. These routes consume the
existing session-backed calculator exactly as already defined and add only the
Prompt 5 HTML partial layer for HTMX.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession
from app.connectors.cached_connector import build_cached_connector
from app.core.config import load_yaml_config
from app.indicators.models import IndicatorCategory, IndicatorResult, ResultType
from app.indicators.registry import get_indicator_registry

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
    period: Optional[str] = Field(None, description="DHIS2 period (legacy single-period)")
    period_start: Optional[str] = Field(None, description="Range start period (YYYYMM)")
    period_end: Optional[str] = Field(None, description="Range end period (YYYYMM)")
    org_unit_name: Optional[str] = None
    expected_pregnancies: Optional[int] = None
    annual_population: Optional[int] = None
    compare_children: Optional[bool] = None
    comparison_mode: str = Field(default="single")


class ScoreCardIndicator(BaseModel):
    """Single indicator entry in the WHO validation scorecard."""

    id: str
    name: str
    value: Optional[float]
    formatted_value: str
    numerator_value: Optional[float] = None
    denominator_value: Optional[float] = None
    target: Optional[float]
    meets_target: Optional[bool]
    status: str
    description: Optional[str] = None
    numerator_label: Optional[str] = None
    denominator_label: Optional[str] = None
    notes: Optional[str] = None
    formula_text: Optional[str] = None
    interpretation: Optional[str] = None
    numerator_formula: Optional[str] = None
    denominator_formula: Optional[str] = None
    numerator_math: Optional[str] = None
    denominator_math: Optional[str] = None
    formula_math: Optional[str] = None


class ScoreCardResponse(BaseModel):
    """WHO validation scorecard response."""

    org_unit: str
    org_unit_name: Optional[str]
    period: str
    generated_at: datetime
    indicators: list[ScoreCardIndicator]
    summary: dict[str, float | int]
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    period_label: Optional[str] = None
    comparison_mode: Optional[str] = None
    comparison_rows: Optional[list[dict[str, Any]]] = None


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
    period: Optional[str] = Field(None, description="DHIS2 period")
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    periodicity: Optional[str] = None
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
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    period_label: Optional[str] = None
    periodicity: Optional[str] = None
    # Enriched optional fields (Prompt 16)
    summary: Optional[dict] = None
    enriched_commodities: Optional[list[dict]] = None
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
    if payload.get("period") in {"", None} and payload.get("period_end") not in {"", None}:
        payload["period"] = payload.get("period_end")
    return model_cls.model_validate(payload)


def parse_checkbox(value: Any) -> bool:
    """Convert mixed checkbox payloads into booleans."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


async def parse_scorecard_request(
    request: Request,
) -> tuple[ScoreCardRequest, dict[str, int]]:
    """Parse scorecard requests, including dynamic comparison population fields."""
    payload = await get_request_payload(request)
    population_overrides: dict[str, int] = {}

    for key in list(payload.keys()):
        if not key.startswith("population_"):
            continue
        raw_value = payload.pop(key)
        if raw_value in {"", None}:
            continue
        try:
            population_overrides[key.removeprefix("population_")] = int(raw_value)
        except (TypeError, ValueError):
            continue

    if payload.get("expected_pregnancies") in {"", None}:
        payload.pop("expected_pregnancies", None)
    if payload.get("annual_population") in {"", None}:
        payload.pop("annual_population", None)

    payload["compare_children"] = parse_checkbox(payload.get("compare_children"))
    comparison_mode = str(payload.get("comparison_mode") or "").strip().lower()
    if not comparison_mode:
        comparison_mode = "children" if payload.get("compare_children") else "single"
    payload["comparison_mode"] = comparison_mode

    return ScoreCardRequest.model_validate(payload), population_overrides


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


def build_month_period_range(
    period: str | None,
    period_start: str | None,
    period_end: str | None,
) -> list[str]:
    """Resolve a legacy period or inclusive monthly range into DHIS2 month IDs."""
    if period and not period_start and not period_end:
        return [period]

    start = period_start or period_end or period
    end = period_end or period_start or period
    if not start or not end:
        raise HTTPException(status_code=400, detail="A period or period range is required")
    if len(start) != 6 or len(end) != 6:
        raise HTTPException(status_code=400, detail="Monthly ranges must use YYYYMM periods")

    try:
        start_year, start_month = int(start[:4]), int(start[4:6])
        end_year, end_month = int(end[:4]), int(end[4:6])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid monthly period format") from exc

    if (start_year, start_month) > (end_year, end_month):
        raise HTTPException(status_code=400, detail="period_start must be before period_end")

    periods: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year}{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods


def build_period_label(periods: list[str]) -> str:
    """Create a human-readable label for a monthly analysis window."""
    if not periods:
        return ""
    if len(periods) == 1:
        return periods[0]
    start = datetime.strptime(periods[0], "%Y%m")
    end = datetime.strptime(periods[-1], "%Y%m")
    return f"{start.strftime('%b %Y')} to {end.strftime('%b %Y')}"


def get_fertility_rate() -> float:
    """Load the configured fertility rate used for derived pregnancies."""
    try:
        config = load_yaml_config("populations.yaml") or {}
        return float(config.get("fertility_rate", 0.05))
    except Exception:
        return 0.05


def build_generic_period_label(
    period: str,
    period_start: str | None = None,
    period_end: str | None = None,
    periodicity: str | None = None,
) -> str:
    """Create a display label for monthly or weekly single/range selections."""
    if not period_start and not period_end:
        return period

    if periodicity == "weekly":
        start = period_start or period
        end = period_end or period
        return f"{start} to {end}" if start != end else start

    try:
        periods = build_month_period_range(period, period_start, period_end)
    except HTTPException:
        start = period_start or period
        end = period_end or period
        return f"{start} to {end}" if start != end else start

    return build_period_label(periods) or period


def derive_expected_pregnancies(
    annual_population: int | None,
    periods: list[str],
    fertility_rate: float = 0.05,
) -> int | None:
    """Derive expected pregnancies from annual population and range fraction."""
    if annual_population is None:
        return None

    months = max(1, len(periods))
    fraction = min(months, 12) / 12.0
    return max(1, round(annual_population * fertility_rate * fraction))


def build_indicator_formula_text(indicator_definition: Any) -> str | None:
    """Build the actual configured formula expression for an indicator."""
    if indicator_definition is None:
        return None
    numerator_formula = (
        indicator_definition.numerator.formula
        if indicator_definition.numerator and indicator_definition.numerator.formula
        else None
    )
    denominator_formula = (
        indicator_definition.denominator.formula
        if indicator_definition.denominator and indicator_definition.denominator.formula
        else None
    )
    if (
        indicator_definition.result_type == ResultType.PERCENTAGE
        and numerator_formula
        and denominator_formula
    ):
        return f"({numerator_formula}) / ({denominator_formula}) x 100"
    if indicator_definition.result_type == ResultType.COUNT and numerator_formula:
        return numerator_formula
    if indicator_definition.result_type == ResultType.DAYS:
        stock_on_hand = getattr(indicator_definition, "stock_on_hand", None)
        consumption = getattr(indicator_definition, "consumption", None)
        if stock_on_hand and consumption:
            return f"{stock_on_hand} / ({consumption} / period_days)"
        return "stock_on_hand / average_daily_consumption"
    return indicator_definition.notes if indicator_definition else None


def format_math_value(value: Any) -> str:
    """Format numeric values consistently for formula explanations."""
    if value is None:
        return "missing"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def substitute_formula_values(
    formula: str | None,
    values: dict[str, Any],
) -> str | None:
    """Replace configured formula tokens with the numeric values used at runtime."""
    if not formula:
        return None

    rendered = formula
    # Keep token extraction aligned with the indicator model pattern.
    from app.indicators.models import IndicatorDefinition

    for code in sorted(IndicatorDefinition._extract_codes(formula), key=len, reverse=True):
        rendered = re.sub(
            rf"\b{re.escape(code)}\b",
            format_math_value(values.get(code)),
            rendered,
        )

    if "expected_pregnancies" in formula:
        rendered = rendered.replace(
            "expected_pregnancies",
            format_math_value(values.get("expected_pregnancies")),
        )

    return rendered


def build_component_math(
    formula: str | None,
    values: dict[str, Any],
    component_value: float | None,
) -> str | None:
    """Build a numeric explanation for one numerator or denominator formula."""
    if not formula:
        return None
    substituted = substitute_formula_values(formula, values)
    if not substituted or substituted == formula:
        return f"{formula} = {format_math_value(component_value)}"
    return f"{formula} = {substituted} = {format_math_value(component_value)}"


def build_formula_math(
    result: IndicatorResult,
) -> str | None:
    """Build the full result equation using computed numerator/denominator values."""
    if result.result_value is None:
        return None
    if result.result_type == ResultType.PERCENTAGE:
        if result.numerator_value is None or result.denominator_value is None:
            return None
        return (
            f"({format_math_value(result.numerator_value)} / "
            f"{format_math_value(result.denominator_value)}) x 100 = "
            f"{format_math_value(result.result_value)}%"
        )
    if result.result_type == ResultType.COUNT:
        return format_math_value(result.result_value)
    if result.result_type == ResultType.DAYS:
        if result.numerator_value is None or result.denominator_value is None:
            return f"{format_math_value(result.result_value)} days"
        return (
            f"{format_math_value(result.numerator_value)} / "
            f"{format_math_value(result.denominator_value)} = "
            f"{format_math_value(result.result_value)} days"
        )
    return format_math_value(result.result_value)


def build_indicator_interpretation(result: IndicatorResult) -> str:
    """Build a plain-language interpretation for one scorecard result."""
    if result.result_value is None:
        return "No usable data was available for this indicator in the selected window."
    if result.target is None:
        return f"The current result is {result.formatted_result}."
    if result.meets_target:
        return f"The current result is {result.formatted_result}, which meets the {result.target:.0f}% target."
    return f"The current result is {result.formatted_result}, below the {result.target:.0f}% target."


def build_scorecard_indicator(
    result: IndicatorResult,
    registry: Any,
) -> ScoreCardIndicator:
    """Convert a calculated indicator result into the scorecard view model."""
    definition = registry.get(result.indicator_id)
    numerator_formula = definition.numerator.formula if definition and definition.numerator else None
    denominator_formula = definition.denominator.formula if definition and definition.denominator else None
    return ScoreCardIndicator(
        id=result.indicator_id,
        name=result.indicator_name,
        value=result.result_value,
        formatted_value=result.formatted_result,
        numerator_value=result.numerator_value,
        denominator_value=result.denominator_value,
        target=result.target,
        meets_target=result.meets_target,
        status=get_status_color(result),
        description=definition.description if definition else None,
        numerator_label=definition.numerator.label if definition and definition.numerator else None,
        denominator_label=definition.denominator.label if definition and definition.denominator else None,
        notes=definition.notes if definition else None,
        formula_text=build_indicator_formula_text(definition),
        interpretation=build_indicator_interpretation(result),
        numerator_formula=numerator_formula,
        denominator_formula=denominator_formula,
        numerator_math=build_component_math(
            numerator_formula,
            result.data_elements_used,
            result.numerator_value,
        ),
        denominator_math=build_component_math(
            denominator_formula,
            result.data_elements_used,
            result.denominator_value,
        ),
        formula_math=build_formula_math(result),
    )


def aggregate_indicator_window(
    indicator_definition: Any,
    series: list[IndicatorResult],
    org_unit: str,
    org_unit_name: str | None,
    period_label: str,
) -> IndicatorResult:
    """Aggregate one indicator across multiple monthly results."""
    valid = [result for result in series if result.is_valid]
    aggregated_elements: dict[str, float] = defaultdict(float)
    for result in valid:
        for code, value in (result.data_elements_used or {}).items():
            if value is None:
                continue
            aggregated_elements[code] += float(value)

    if not valid:
        return IndicatorResult(
            indicator_id=indicator_definition.id,
            indicator_name=indicator_definition.name,
            category=indicator_definition.category,
            org_unit_uid=org_unit,
            org_unit_name=org_unit_name,
            period=period_label,
            result_type=indicator_definition.result_type,
            target=indicator_definition.target,
            is_valid=False,
            error_message=series[0].error_message if series else "No data",
            data_elements_used={},
        )

    numerator_value: float | None = None
    denominator_value: float | None = None
    result_value: float | None = None

    if indicator_definition.result_type == ResultType.PERCENTAGE:
        numerator_value = sum(result.numerator_value or 0 for result in valid)
        denominator_value = sum(result.denominator_value or 0 for result in valid)
        result_value = (numerator_value / denominator_value * 100.0) if denominator_value else None
    elif indicator_definition.result_type == ResultType.COUNT:
        values = [
            result.result_value
            if result.result_value is not None
            else result.numerator_value
            for result in valid
        ]
        filtered_values = [float(value) for value in values if value is not None]
        result_value = sum(filtered_values) if filtered_values else None
        numerator_value = result_value
    elif indicator_definition.result_type == ResultType.DAYS:
        values = [result.result_value for result in valid if result.result_value is not None]
        result_value = (sum(values) / len(values)) if values else None
    else:
        values = [result.result_value for result in valid if result.result_value is not None]
        result_value = sum(values) if values else None

    meets_target = None
    if indicator_definition.target is not None and result_value is not None:
        meets_target = result_value >= indicator_definition.target

    return IndicatorResult(
        indicator_id=indicator_definition.id,
        indicator_name=indicator_definition.name,
        category=indicator_definition.category,
        org_unit_uid=org_unit,
        org_unit_name=org_unit_name,
        period=period_label,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        result_value=result_value,
        result_type=indicator_definition.result_type,
        target=indicator_definition.target,
        is_valid=result_value is not None,
        meets_target=meets_target,
        data_elements_used=dict(aggregated_elements),
    )


def build_scorecard_summary(indicators: list[ScoreCardIndicator]) -> dict[str, float | int]:
    """Build scorecard summary metrics from the rendered indicator list."""
    meeting_target = 0
    total_with_target = 0
    for indicator in indicators:
        if indicator.target is None:
            continue
        total_with_target += 1
        if indicator.meets_target:
            meeting_target += 1

    return {
        "total": len(indicators),
        "meeting_target": meeting_target,
        "total_with_target": total_with_target,
        "score_pct": (meeting_target / total_with_target * 100) if total_with_target else 0,
    }


async def calculate_scorecard_indicators(
    calculator: Calculator,
    org_unit: str,
    org_unit_name: str | None,
    periods: list[str],
) -> list[ScoreCardIndicator]:
    """Calculate or aggregate the WHO validation indicators for one org unit."""
    registry = get_indicator_registry()
    if len(periods) == 1:
        result_set = await calculator.calculate_all(
            org_unit=org_unit,
            period=periods[0],
            org_unit_name=org_unit_name,
            categories=[IndicatorCategory.WHO_VALIDATION],
        )
        return [build_scorecard_indicator(result, registry) for result in result_set.results]

    series_by_indicator: dict[str, list[IndicatorResult]] = defaultdict(list)
    for period in periods:
        result_set = await calculator.calculate_all(
            org_unit=org_unit,
            period=period,
            org_unit_name=org_unit_name,
            categories=[IndicatorCategory.WHO_VALIDATION],
        )
        for result in result_set.results:
            series_by_indicator[result.indicator_id].append(result)

    aggregated: list[ScoreCardIndicator] = []
    period_label = build_period_label(periods)
    for definition in registry.get_by_category(IndicatorCategory.WHO_VALIDATION):
        aggregated_result = aggregate_indicator_window(
            definition,
            series_by_indicator.get(definition.id, []),
            org_unit,
            org_unit_name,
            period_label,
        )
        aggregated.append(build_scorecard_indicator(aggregated_result, registry))
    return aggregated


async def resolve_comparison_units(
    session: CurrentSession,
    org_unit: str,
    comparison_mode: str,
) -> list[dict[str, Any]]:
    """Resolve comparison targets from the selected root org unit."""
    if comparison_mode == "single":
        return []

    level_targets = {
        "region_districts": 3,
        "district_facilities": 5,
        "children": None,
    }
    if comparison_mode not in level_targets:
        raise HTTPException(status_code=400, detail=f"Unknown comparison mode: {comparison_mode}")

    async with build_cached_connector(session) as connector:
        hierarchy = await connector.get_org_unit_hierarchy(org_unit)
        root = await connector.get_org_unit(org_unit)

    target_level = level_targets[comparison_mode]
    comparison_units: list[dict[str, Any]] = []
    for unit in hierarchy:
        if unit.uid == org_unit:
            continue
        if target_level is not None and unit.level != target_level:
            continue
        comparison_units.append({"uid": unit.uid, "name": unit.name, "level": unit.level})

    if comparison_mode == "children":
        comparison_units = [
            unit for unit in comparison_units if unit["level"] == (root.level or 0) + 1
        ]

    comparison_units.sort(key=lambda item: item["name"].lower())
    return comparison_units


@router.post("/scorecard", response_model=ScoreCardResponse)
async def generate_scorecard(
    request: Request,
    calculator: Calculator,
    session: CurrentSession,
) -> Response | ScoreCardResponse:
    """Generate the six WHO validation indicators as a scorecard."""
    scorecard_request, population_overrides = await parse_scorecard_request(request)
    org_unit_name = resolve_org_unit_name(
        session,
        scorecard_request.org_unit,
        scorecard_request.org_unit_name,
    )
    periods = build_month_period_range(
        scorecard_request.period,
        scorecard_request.period_start,
        scorecard_request.period_end,
    )
    period = periods[-1]
    period_label = build_period_label(periods)

    comparison_mode = scorecard_request.comparison_mode
    if comparison_mode not in {"single", "district_facilities", "region_districts", "children"}:
        comparison_mode = "single"

    expected_pregnancies = scorecard_request.expected_pregnancies
    if scorecard_request.annual_population is not None:
        expected_pregnancies = derive_expected_pregnancies(
            scorecard_request.annual_population,
            periods,
            get_fertility_rate(),
        )

    comparison_rows: list[dict[str, Any]] | None = None
    if comparison_mode == "single":
        if expected_pregnancies is not None:
            calculator.set_expected_pregnancies(scorecard_request.org_unit, expected_pregnancies)
        else:
            calculator.clear_expected_pregnancies(scorecard_request.org_unit)
        indicators = await calculate_scorecard_indicators(
            calculator,
            scorecard_request.org_unit,
            org_unit_name,
            periods,
        )
        summary = build_scorecard_summary(indicators)
    else:
        comparison_units = await resolve_comparison_units(
            session,
            scorecard_request.org_unit,
            comparison_mode,
        )
        if not comparison_units:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No comparison units were found for the selected organisation unit.",
            )

        comparison_rows = []
        for unit in comparison_units:
            unit_population = population_overrides.get(unit["uid"])
            unit_expected = (
                derive_expected_pregnancies(unit_population, periods, get_fertility_rate())
                if unit_population
                else None
            )
            if unit_expected is not None:
                calculator.set_expected_pregnancies(unit["uid"], unit_expected)
            else:
                calculator.clear_expected_pregnancies(unit["uid"])

            unit_indicators = await calculate_scorecard_indicators(
                calculator,
                unit["uid"],
                unit["name"],
                periods,
            )
            unit_summary = build_scorecard_summary(unit_indicators)
            comparison_rows.append(
                {
                    "org_unit_uid": unit["uid"],
                    "org_unit_name": unit["name"],
                    "level": unit["level"],
                    "annual_population": unit_population,
                    "expected_pregnancies": unit_expected,
                    "score_pct": unit_summary["score_pct"],
                    "meeting_target": unit_summary["meeting_target"],
                    "total_with_target": unit_summary["total_with_target"],
                    "indicators": [indicator.model_dump() for indicator in unit_indicators],
                }
            )

        indicators = await calculate_scorecard_indicators(
            calculator,
            scorecard_request.org_unit,
            org_unit_name,
            periods,
        )
        scores = [float(row["score_pct"]) for row in comparison_rows]
        summary = {
            "total": len(comparison_rows),
            "meeting_target": len([score for score in scores if score >= 80]),
            "total_with_target": len(comparison_rows),
            "score_pct": (sum(scores) / len(scores)) if scores else 0,
            "best_score": max(scores) if scores else 0,
            "worst_score": min(scores) if scores else 0,
        }

    generated_at = datetime.now(timezone.utc)

    if is_htmx_request(request):
        indicators_json = json.dumps([ind.model_dump() for ind in indicators])
        fertility_rate = get_fertility_rate()
        return templates.TemplateResponse(
            request,
            "components/scorecard_results.html",
            {
                "request": request,
                "indicators": [indicator.model_dump() for indicator in indicators],
                "indicators_json": indicators_json,
                "summary": summary,
                "org_unit": scorecard_request.org_unit,
                "org_unit_name": org_unit_name,
                "period": period,
                "period_start": periods[0],
                "period_end": periods[-1],
                "period_label": period_label,
                "comparison_mode": comparison_mode,
                "comparison_rows": comparison_rows,
                "annual_population": scorecard_request.annual_population,
                "expected_pregnancies": expected_pregnancies,
                "fertility_rate": fertility_rate,
                "selected_months": len(periods),
                "generated_at": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
            },
        )

    return ScoreCardResponse(
        org_unit=scorecard_request.org_unit,
        org_unit_name=org_unit_name,
        period=period,
        generated_at=generated_at,
        indicators=indicators,
        summary=summary,
        period_start=periods[0],
        period_end=periods[-1],
        period_label=period_label,
        comparison_mode=comparison_mode,
        comparison_rows=comparison_rows,
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
    period_label = build_generic_period_label(
        supply_request.period,
        supply_request.period_start,
        supply_request.period_end,
        supply_request.periodicity,
    )

    # Build enriched per-commodity dicts (shared by HTMX and JSON)
    enriched_rows = []
    all_alerts: list[dict] = []
    all_validation: list[dict] = []
    all_forecasts: list[dict] = []
    for ec in report.commodities:
        commodity_alerts = [
            {
                "commodity_id": ec.commodity.id,
                "commodity": ec.commodity.name,
                "severity": a.severity.value,
                "alert_type": a.alert_type,
                "message": a.message,
                "current_value": a.current_value,
                "threshold_value": a.threshold_value,
            }
            for a in ec.alerts
        ]
        commodity_validation = [
            {
                "commodity_id": ec.commodity.id,
                "commodity": ec.commodity.name,
                "severity": f.severity.value,
                "field": f.field_name,
                "message": f.message,
            }
            for f in ec.validation
        ]
        forecast_dict = {
            "commodity_id": ec.commodity.id,
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

    unmapped_list = [
        {
            "id": c.id,
            "name": c.name,
            "unit": c.unit,
            "mapping_status": c.mapping_status.value,
        }
        for c in report.unmapped_commodities
    ]

    if is_htmx_request(request):
        chart_commodities = json.dumps([
            {"commodity": r["commodity"], "days_of_use": r["days_of_use"], "stockout_days": r["stockout_days"]}
            for r in enriched_rows
        ])
        return templates.TemplateResponse(
            request,
            "components/supply_results.html",
            {
                "request": request,
                "org_unit": supply_request.org_unit,
                "org_unit_name": org_unit_name,
                "period": supply_request.period,
                "period_start": supply_request.period_start,
                "period_end": supply_request.period_end,
                "periodicity": supply_request.periodicity,
                "period_label": period_label,
                "commodities": enriched_rows,
                "commodities_json": chart_commodities,
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
        period_start=supply_request.period_start,
        period_end=supply_request.period_end,
        period_label=period_label,
        periodicity=supply_request.periodicity,
        summary=report.summary,
        enriched_commodities=enriched_rows,
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
