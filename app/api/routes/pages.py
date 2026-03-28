"""
HTML page routes for the frontend pages.

These routes render full Jinja templates. Data fragments continue to come from
the existing /api routes, which return TemplateResponse partials for HTMX
requests and JSON for non-HTMX callers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import get_optional_session
from app.api.middleware import get_csrf_token
from app.api.routes.reports import (
    DEFAULT_HISTORY_DEPTH,
    DEFAULT_PERIODICITY,
    HISTORY_DEPTH_OPTIONS,
    PERIODICITY_OPTIONS,
    build_periods,
)
from app.auth.permissions import Permission, check_permission, get_user_permissions
from app.auth.roles import get_role_from_session
from app.core.config import get_settings, load_yaml_config
from app.indicators.models import IndicatorCategory, Periodicity
from app.indicators.registry import get_indicator_registry
from app.services.trends import TrendService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
trend_service = TrendService()

# Range period list covers 36 months for From/To selectors
_RANGE_PERIOD_COUNT = 36


def _get_fertility_rate() -> float:
    """Load fertility rate from populations.yaml, default 0.05."""
    try:
        config = load_yaml_config("populations.yaml")
        return float(config.get("fertility_rate", 0.05))
    except Exception:
        return 0.05


def build_page_context(request: Request, session: Any) -> dict[str, Any]:
    """Build the shared template context for authenticated pages."""
    credentials = session.credentials
    role_info = get_role_from_session(session)
    permissions = sorted(
        permission.value for permission in get_user_permissions(role_info)
    ) if role_info else []

    return {
        "request": request,
        "authenticated_page": True,
        "user_name": credentials.user_name,
        "org_units": credentials.org_units,
        "csrf_token": get_csrf_token(request),
        "user_role": role_info.role.value if role_info else None,
        "user_permissions": permissions,
        "is_admin": bool(role_info and (role_info.is_super_admin or role_info.role.value == "admin")),
        "periodicity_options": PERIODICITY_OPTIONS,
        "history_depth_options": HISTORY_DEPTH_OPTIONS,
        "default_periodicity": DEFAULT_PERIODICITY,
        "default_history_depth": DEFAULT_HISTORY_DEPTH,
        "periods": build_periods(
            periodicity=DEFAULT_PERIODICITY,
            history_depth="36m",
        ),
        "fertility_rate": _get_fertility_rate(),
    }


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> RedirectResponse:
    """Redirect visitors to the dashboard or login page."""
    session = await get_optional_session(request)
    if session:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session_expired: str | None = None) -> HTMLResponse:
    """Render the DHIS2 login page."""
    session = await get_optional_session(request)
    if session:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "authenticated_page": False,
            "show_nav": False,
            "session_expired": session_expired == "1",
            "default_dhis2_url": get_settings().dhis2_base_url,
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """Render the WHO validation dashboard page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "dashboard"
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/indicators", response_class=HTMLResponse)
async def indicators_page(request: Request) -> HTMLResponse:
    """Render the indicator calculator page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "indicators"
    context["categories"] = [
        {"id": category.value, "name": category.name.replace("_", " ").title()}
        for category in IndicatorCategory
    ]
    return templates.TemplateResponse(request, "indicators.html", context)


@router.get("/cascade/{cascade_type}", response_class=HTMLResponse)
async def cascade_page(request: Request, cascade_type: str) -> HTMLResponse:
    """Render a cascade page for a supported cascade type."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    if cascade_type not in {"hiv", "hbv", "syphilis"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown cascade type",
        )

    context = build_page_context(request, session)
    context["active_page"] = "cascade"
    context["cascade_type"] = cascade_type
    return templates.TemplateResponse(request, "cascade.html", context)


@router.get("/supply", response_class=HTMLResponse)
async def supply_page(request: Request) -> HTMLResponse:
    """Render the supply chain page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "supply"
    return templates.TemplateResponse(request, "supply.html", context)


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(request: Request) -> HTMLResponse:
    """Render the monthly trends analysis page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "trends"
    context["periods"] = trend_service.build_monthly_period_options(count=12)
    context["org_units"] = [
        {"id": org_unit.get("id"), "name": org_unit.get("name")}
        for org_unit in context.get("org_units", [])
        if org_unit.get("id") and org_unit.get("name")
    ]

    registry = get_indicator_registry()
    indicators = [
        {
            "id": indicator.id,
            "name": indicator.name,
            "category": indicator.category.name.replace("_", " ").title(),
        }
        for indicator in registry.get_all()
        if indicator.periodicity != Periodicity.WEEKLY
    ]
    context["indicators"] = sorted(indicators, key=lambda item: (item["category"], item["id"]))

    return templates.TemplateResponse(request, "trends.html", context)


@router.get("/data-quality", response_class=HTMLResponse)
async def data_quality_page(
    request: Request,
    org_unit: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> HTMLResponse:
    """Render the data-quality dashboard page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "data_quality"
    context["periods"] = build_periods(
        periodicity=DEFAULT_PERIODICITY,
        history_depth=DEFAULT_HISTORY_DEPTH,
    )
    context["selected_org_unit"] = org_unit or (
        context["org_units"][0].get("id") if context.get("org_units") else None
    )
    context["selected_period"] = period or (
        context["periods"][0]["id"] if context.get("periods") else None
    )
    return templates.TemplateResponse(request, "data_quality.html", context)


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    org_unit: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> HTMLResponse:
    """Render the monthly alerts dashboard page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "alerts"
    context["periods"] = build_periods(
        periodicity="monthly",
        history_depth=DEFAULT_HISTORY_DEPTH,
    )
    context["selected_org_unit"] = org_unit or (
        context["org_units"][0].get("id") if context.get("org_units") else None
    )
    context["selected_period"] = period or (
        context["periods"][0]["id"] if context.get("periods") else None
    )
    return templates.TemplateResponse(request, "alerts.html", context)


@router.get("/insights", response_class=HTMLResponse)
async def insights_page(
    request: Request,
    org_unit: str | None = Query(default=None),
    period: str | None = Query(default=None),
    history_depth: str | None = Query(default=None),
    indicator_id: str | None = Query(default=None),
) -> HTMLResponse:
    """Render the Prompt 11 AI insights page."""
    session = await get_optional_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    role_info = get_role_from_session(session)
    if not role_info or not check_permission(role_info, Permission.USE_AI_INSIGHTS).granted:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    context = build_page_context(request, session)
    context["active_page"] = "insights"
    context["periods"] = build_periods(
        periodicity="monthly",
        history_depth=DEFAULT_HISTORY_DEPTH,
    )

    valid_history_depths = {option["id"] for option in context["history_depth_options"]}
    context["selected_history_depth"] = (
        history_depth if history_depth in valid_history_depths else context["default_history_depth"]
    )
    context["selected_org_unit"] = org_unit or (
        context["org_units"][0].get("id") if context.get("org_units") else None
    )
    context["selected_period"] = period or (
        context["periods"][0]["id"] if context.get("periods") else None
    )

    registry = get_indicator_registry()
    indicators = [
        {
            "id": indicator.id,
            "name": indicator.name,
            "category": indicator.category.value,
        }
        for indicator in registry.get_all()
        if indicator.periodicity == Periodicity.MONTHLY
    ]
    indicators = sorted(indicators, key=lambda item: (item["category"], item["id"]))
    context["indicators"] = indicators
    context["selected_indicator"] = indicator_id or (
        indicators[0]["id"] if indicators else None
    )
    context["cascades"] = [
        {"value": "hiv", "label": "HIV"},
        {"value": "syphilis", "label": "Syphilis"},
        {"value": "hbv", "label": "HBV"},
    ]

    return templates.TemplateResponse(request, "insights.html", context)
