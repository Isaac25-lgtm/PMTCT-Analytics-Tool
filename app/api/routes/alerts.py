"""
Alert API and HTMX routes.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession, require_permission
from app.auth.permissions import Permission, check_permission
from app.auth.roles import get_role_from_session
from app.api.routes.reports import get_request_payload, is_htmx_request
from app.core.session import get_session_manager
from app.services.alert_engine import AlertEngine, AlertResult, AlertSummary, AlertThresholdLoader
from app.services.alert_rules import AlertCategory, AlertSeverity

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_permission(Permission.VIEW_ALERTS))])
templates = Jinja2Templates(directory="app/templates")

_alert_engines: dict[str, AlertEngine] = {}


def render_error_card(message: str, *, status_code: int = 500) -> HTMLResponse:
    """Render a lightweight styled error block for HTMX callers."""
    safe_message = html.escape(message)
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<div class='card-panel border border-rose-200 bg-rose-50 text-rose-800'>"
            "<h2 class='text-lg font-semibold'>Alert evaluation failed</h2>"
            f"<p class='mt-2 text-sm'>{safe_message}</p>"
            "</div>"
        ),
    )


def cleanup_stale_alert_engines() -> None:
    """
    Remove cached session-scoped alert engines whose sessions no longer exist.

    This keeps the in-memory acknowledgement cache aligned with session expiry
    and logout without introducing persistence.
    """
    session_manager = get_session_manager()
    stale_session_ids = [
        session_id
        for session_id in list(_alert_engines.keys())
        if session_manager.get_session(session_id) is None
    ]
    for session_id in stale_session_ids:
        _alert_engines.pop(session_id, None)


def get_alert_engine(calculator: Calculator, session: CurrentSession) -> AlertEngine:
    """Return the per-session alert engine used for acknowledgement state."""
    cleanup_stale_alert_engines()
    engine = _alert_engines.get(session.session_id)
    if engine is None:
        engine = AlertEngine(calculator=calculator)
        _alert_engines[session.session_id] = engine
    return engine


def parse_bool(value: Any, default: bool = True) -> bool:
    """Parse a bool-like query or form value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_severity_filter(severity: str | None) -> AlertSeverity | None:
    """Parse the severity filter value."""
    if not severity:
        return None
    return AlertSeverity(severity)


def parse_category_filter(category: str | None) -> AlertCategory | None:
    """Parse the category filter value."""
    if not category:
        return None
    return AlertCategory(category)


def build_alert_context(result: AlertResult, *, can_manage_alerts: bool) -> dict[str, Any]:
    """Build the template context for the alert list partial."""
    return {
        "result": result,
        "can_manage_alerts": can_manage_alerts,
        "alerts_by_severity": {
            "critical": [alert for alert in result.alerts if alert.severity == AlertSeverity.CRITICAL],
            "warning": [alert for alert in result.alerts if alert.severity == AlertSeverity.WARNING],
            "info": [alert for alert in result.alerts if alert.severity == AlertSeverity.INFO],
        },
    }


def _can_manage_alerts(session: CurrentSession) -> bool:
    """Return True when the current session can acknowledge or manage alerts."""
    role_info = get_role_from_session(session)
    if not role_info:
        return False
    return check_permission(role_info, Permission.MANAGE_ALERTS).granted


class AlertResponse(BaseModel):
    """Single alert record in API responses."""

    alert_id: str
    alert_type: str
    severity: str
    category: str
    title: str
    message: str
    org_unit: str
    period: str
    indicator_id: Optional[str]
    current_value: Optional[float]
    threshold_value: Optional[float]
    target_value: Optional[float]
    created_at: str
    acknowledged: bool
    acknowledged_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertSummaryResponse(BaseModel):
    """Alert summary payload."""

    org_unit: str
    period: str
    total_alerts: int
    critical_count: int
    warning_count: int
    info_count: int
    acknowledged_count: int
    unacknowledged_count: int
    by_category: dict[str, int]


class AlertListResponse(BaseModel):
    """Full alert list response."""

    org_unit: str
    period: str
    evaluated_at: str
    alerts: list[AlertResponse]
    summary: AlertSummaryResponse


class AcknowledgeResponse(BaseModel):
    """JSON acknowledgement response."""

    alert_id: str
    acknowledged: bool


def serialise_summary(summary: AlertSummary) -> AlertSummaryResponse:
    """Convert an AlertSummary dataclass to the route response model."""
    return AlertSummaryResponse(**summary.to_dict())


def serialise_result(result: AlertResult) -> AlertListResponse:
    """Convert an AlertResult dataclass to the route response model."""
    return AlertListResponse(
        org_unit=result.org_unit,
        period=result.period,
        evaluated_at=result.evaluated_at.isoformat(),
        alerts=[AlertResponse(**alert.to_dict()) for alert in result.alerts],
        summary=serialise_summary(result.summary),
    )


@router.get(
    "",
    response_model=AlertListResponse,
    summary="Get monthly alerts",
    description="Get threshold-based alerts for an org unit and monthly DHIS2 period (YYYYMM).",
)
async def get_alerts(
    _session: CurrentSession,
    calculator: Calculator,
    org_unit: str = Query(..., description="Organisation unit UID"),
    period: str = Query(..., description="Monthly DHIS2 period in YYYYMM format"),
    severity: str | None = Query(default=None, description="Optional alert severity filter"),
    category: str | None = Query(default=None, description="Optional alert category filter"),
    include_acknowledged: bool = Query(
        default=True,
        description="Include alerts already acknowledged in the active browser session",
    ),
) -> AlertListResponse:
    """Return filtered monthly alerts as JSON."""
    engine = get_alert_engine(calculator, _session)
    try:
        evaluated = await engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        filtered = evaluated.filtered(
            severity=parse_severity_filter(severity),
            category=parse_category_filter(category),
            include_acknowledged=include_acknowledged,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid alert request",
        ) from exc
    except Exception as exc:
        logger.exception("Alert evaluation error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Alert evaluation failed",
        ) from exc

    return serialise_result(filtered)


@router.get(
    "/summary",
    response_model=AlertSummaryResponse,
    summary="Get monthly alert summary",
    description="Get alert counts for an org unit and monthly DHIS2 period (YYYYMM).",
)
async def get_alert_summary(
    _session: CurrentSession,
    calculator: Calculator,
    org_unit: str = Query(..., description="Organisation unit UID"),
    period: str = Query(..., description="Monthly DHIS2 period in YYYYMM format"),
    severity: str | None = Query(default=None, description="Optional alert severity filter"),
    category: str | None = Query(default=None, description="Optional alert category filter"),
    include_acknowledged: bool = Query(default=True),
) -> AlertSummaryResponse:
    """Return the filtered alert summary as JSON."""
    engine = get_alert_engine(calculator, _session)
    try:
        evaluated = await engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        filtered = evaluated.filtered(
            severity=parse_severity_filter(severity),
            category=parse_category_filter(category),
            include_acknowledged=include_acknowledged,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid alert summary request",
        ) from exc
    except Exception as exc:
        logger.exception("Alert summary error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Alert summary generation failed",
        ) from exc

    return serialise_summary(filtered.summary)


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AcknowledgeResponse,
    summary="Acknowledge an alert",
    description="Mark an alert as acknowledged for the active browser session.",
    dependencies=[Depends(require_permission(Permission.MANAGE_ALERTS))],
)
async def acknowledge_alert(
    request: Request,
    alert_id: str,
    _session: CurrentSession,
    calculator: Calculator,
) -> HTMLResponse | AcknowledgeResponse:
    """
    Acknowledge an alert.

    HTMX callers receive a refreshed list partial and a trigger to refresh the
    badge. JSON callers receive a minimal acknowledgement payload.
    """
    engine = get_alert_engine(calculator, _session)
    acknowledged = engine.acknowledge_alert(alert_id)

    if not is_htmx_request(request):
        return AcknowledgeResponse(alert_id=alert_id, acknowledged=acknowledged)

    payload = await get_request_payload(request)
    org_unit = payload.get("org_unit") or request.query_params.get("org_unit")
    period = payload.get("period") or request.query_params.get("period")
    severity = payload.get("severity") or request.query_params.get("severity")
    category = payload.get("category") or request.query_params.get("category")
    include_acknowledged = parse_bool(
        payload.get("include_acknowledged", request.query_params.get("include_acknowledged")),
        default=True,
    )

    if not org_unit or not period:
        return render_error_card(
            "org_unit and period are required to refresh the alert list.",
            status_code=400,
        )

    try:
        evaluated = await engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        filtered = evaluated.filtered(
            severity=parse_severity_filter(severity),
            category=parse_category_filter(category),
            include_acknowledged=include_acknowledged,
        )
    except ValueError as exc:
        return render_error_card(str(exc), status_code=422)
    except Exception as exc:
        logger.error("HTMX alert acknowledgement error: %s", exc)
        return render_error_card(f"Error refreshing alerts: {exc}")

    response = templates.TemplateResponse(
        request,
        "components/alert_list.html",
        {
            "request": request,
            **build_alert_context(filtered, can_manage_alerts=_can_manage_alerts(_session)),
        },
    )
    response.headers["HX-Trigger"] = "refresh-alert-badge"
    return response


@router.get(
    "/thresholds",
    summary="Get configured alert thresholds",
    description="Return the active alert thresholds used for monthly evaluation.",
    dependencies=[Depends(require_permission(Permission.MANAGE_ALERTS))],
)
async def get_alert_thresholds(
    _session: CurrentSession,
    enabled_only: bool = Query(default=True, description="Only include enabled thresholds"),
) -> dict[str, list[dict[str, Any]]]:
    """Return configured alert thresholds using the public loader interface."""
    loader = AlertThresholdLoader()
    thresholds = loader.get_enabled_thresholds() if enabled_only else loader.get_all_thresholds()
    return {
        "thresholds": [
            {
                "threshold_id": threshold.threshold_id,
                "name": threshold.name,
                "description": threshold.description,
                "indicator_ids": threshold.indicator_ids,
                "alert_type": threshold.alert_type.value,
                "severity": threshold.severity.value,
                "category": threshold.category.value,
                "operator": threshold.operator,
                "value": threshold.value,
                "use_target": threshold.use_target,
                "target_multiplier": threshold.target_multiplier,
                "value_source": threshold.value_source,
                "enabled": threshold.enabled,
            }
            for threshold in thresholds
        ]
    }


@router.get("/htmx/list", response_class=HTMLResponse, include_in_schema=False)
async def htmx_alert_list(
    request: Request,
    _session: CurrentSession,
    calculator: Calculator,
    org_unit: str = Query(...),
    period: str = Query(..., description="Monthly DHIS2 period in YYYYMM format"),
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
    include_acknowledged: bool = Query(default=True),
) -> HTMLResponse:
    """Render the filtered monthly alert list partial for HTMX."""
    engine = get_alert_engine(calculator, _session)
    try:
        evaluated = await engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        filtered = evaluated.filtered(
            severity=parse_severity_filter(severity),
            category=parse_category_filter(category),
            include_acknowledged=include_acknowledged,
        )
    except ValueError as exc:
        return render_error_card(str(exc), status_code=422)
    except Exception as exc:
        logger.error("HTMX alert list error: %s", exc)
        return render_error_card(f"Error loading alerts: {exc}")

    return templates.TemplateResponse(
        request,
        "components/alert_list.html",
        {
            "request": request,
            **build_alert_context(filtered, can_manage_alerts=_can_manage_alerts(_session)),
        },
    )


@router.get("/htmx/badge", response_class=HTMLResponse, include_in_schema=False)
async def htmx_alert_badge(
    request: Request,
    _session: CurrentSession,
    calculator: Calculator,
    org_unit: str = Query(...),
    period: str = Query(..., description="Monthly DHIS2 period in YYYYMM format"),
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
    include_acknowledged: bool = Query(default=True),
) -> HTMLResponse:
    """Render the compact monthly alert badge partial for HTMX."""
    engine = get_alert_engine(calculator, _session)
    try:
        evaluated = await engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        filtered = evaluated.filtered(
            severity=parse_severity_filter(severity),
            category=parse_category_filter(category),
            include_acknowledged=include_acknowledged,
        )
    except ValueError as exc:
        return render_error_card(str(exc), status_code=422)
    except Exception as exc:
        logger.error("HTMX alert badge error: %s", exc)
        return HTMLResponse(
            status_code=200,
            content="<span class='text-sm text-slate-400'>--</span>",
        )

    return templates.TemplateResponse(
        request,
        "components/alert_badge.html",
        {
            "request": request,
            "summary": filtered.summary,
        },
    )
