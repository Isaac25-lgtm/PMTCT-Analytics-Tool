"""
Data-quality API and HTMX routes.
"""

from __future__ import annotations

import html
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession, SessCache, require_permission
from app.auth.permissions import Permission
from app.core.cache_keys import CacheKeys, get_cache_ttl
from app.services.data_quality import DataQualityEngine, DQRuleLoader

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_permission(Permission.VIEW_DATA_QUALITY))])
templates = Jinja2Templates(directory="app/templates")


def get_dq_engine(calculator: Calculator) -> DataQualityEngine:
    """Build the DQ engine from the current calculator dependency."""
    return DataQualityEngine(calculator=calculator)


def render_error_card(message: str, *, status_code: int = 500) -> HTMLResponse:
    """Render a lightweight styled error block for HTMX callers."""
    safe_message = html.escape(message)
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<div class='card-panel border border-rose-200 bg-rose-50 text-rose-800'>"
            "<h2 class='text-lg font-semibold'>Data-quality check failed</h2>"
            f"<p class='mt-2 text-sm'>{safe_message}</p>"
            "</div>"
        ),
    )


class DQCheckRequest(BaseModel):
    """Request body for DQ checks."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    indicator_ids: Optional[List[str]] = Field(default=None)
    include_historical: bool = Field(default=True)
    historical_periods: int = Field(default=6, ge=1, le=24)


class DQFindingResponse(BaseModel):
    """Single finding in the DQ response."""

    rule_id: str
    rule_name: str
    severity: str
    category: str
    message: str
    org_unit: str
    period: str
    indicator_id: Optional[str]
    data_element: Optional[str]
    current_value: Optional[float]
    expected_range: Optional[str]
    recommendation: Optional[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DQResultResponse(BaseModel):
    """Complete DQ result response."""

    org_unit: str
    period: str
    checked_at: str
    summary: dict[str, Any]
    findings: List[DQFindingResponse]
    indicators_checked: List[str]


class DQScoreResponse(BaseModel):
    """DQ score response."""

    org_unit: str
    period: str
    score: float
    grade: str
    grade_label: str
    summary: dict[str, Any]


@router.post("/check", response_model=DQResultResponse)
async def run_dq_checks(
    request_body: DQCheckRequest,
    _session: CurrentSession,
    calculator: Calculator,
    session_cache: SessCache,
) -> DQResultResponse:
    """Run DQ checks and return a JSON response."""
    dq_engine = get_dq_engine(calculator)
    try:
        cache_key = CacheKeys.data_quality(
            org_unit=request_body.org_unit,
            period=request_body.period,
            indicator_ids=request_body.indicator_ids,
            include_historical=request_body.include_historical,
            historical_periods=request_body.historical_periods,
        )
        payload = await session_cache.get_or_set_async(
            cache_key,
            lambda: dq_engine.run_checks(
                org_unit=request_body.org_unit,
                period=request_body.period,
                indicator_ids=request_body.indicator_ids,
                include_historical=request_body.include_historical,
                historical_periods=request_body.historical_periods,
            ),
            ttl=get_cache_ttl("data_quality"),
        )
        result = payload
    except Exception as exc:
        logger.exception("DQ check error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data quality check failed",
        ) from exc

    return DQResultResponse(
        org_unit=result.org_unit,
        period=result.period,
        checked_at=result.checked_at.isoformat(),
        summary=result.summary.to_dict(),
        findings=[DQFindingResponse(**finding.to_dict()) for finding in result.findings],
        indicators_checked=result.indicators_checked,
    )


@router.get("/score", response_model=DQScoreResponse)
async def get_dq_score(
    _session: CurrentSession,
    calculator: Calculator,
    session_cache: SessCache,
    org_unit: str = Query(..., description="Organisation unit UID"),
    period: str = Query(..., description="DHIS2 period"),
) -> DQScoreResponse:
    """Return an overall DQ score for the selection."""
    dq_engine = get_dq_engine(calculator)
    try:
        score_data = await session_cache.get_or_set_async(
            CacheKeys.data_quality_score(org_unit, period),
            lambda: dq_engine.get_dq_score(org_unit=org_unit, period=period),
            ttl=get_cache_ttl("data_quality"),
        )
    except Exception as exc:
        logger.exception("DQ score error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data quality score calculation failed",
        ) from exc

    return DQScoreResponse(**score_data)


@router.get("/rules")
async def get_dq_rules(
    _session: CurrentSession,
    enabled_only: bool = Query(default=True, description="Only return enabled rules"),
) -> dict[str, list[dict[str, Any]]]:
    """Return configured DQ rules."""
    loader = DQRuleLoader()
    rules = loader.get_enabled_rules() if enabled_only else loader.get_all_rules()
    return {
        "rules": [
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "description": rule.description,
                "severity": rule.severity.value,
                "category": rule.category.value,
                "enabled": rule.enabled,
                "params": rule.params,
                "applies_to": rule.applies_to,
            }
            for rule in rules
        ]
    }


@router.get("/htmx/results", response_class=HTMLResponse, include_in_schema=False)
async def htmx_dq_results(
    request: Request,
    _session: CurrentSession,
    calculator: Calculator,
    session_cache: SessCache,
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the detailed DQ results partial for HTMX."""
    dq_engine = get_dq_engine(calculator)
    try:
        result = await session_cache.get_or_set_async(
            CacheKeys.data_quality(
                org_unit=org_unit,
                period=period,
                indicator_ids=None,
                include_historical=True,
                historical_periods=6,
            ),
            lambda: dq_engine.run_checks(org_unit=org_unit, period=period, include_historical=True),
            ttl=get_cache_ttl("data_quality"),
        )
    except Exception as exc:
        logger.error("HTMX DQ results error: %s", exc)
        return render_error_card(f"Error running DQ checks: {exc}")

    return templates.TemplateResponse(
        request,
        "components/dq_results.html",
        {
            "request": request,
            "result": result,
        },
    )


@router.get("/htmx/score-card", response_class=HTMLResponse, include_in_schema=False)
async def htmx_dq_score_card(
    request: Request,
    _session: CurrentSession,
    calculator: Calculator,
    session_cache: SessCache,
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the compact DQ score card partial for HTMX."""
    dq_engine = get_dq_engine(calculator)
    try:
        score_data = await session_cache.get_or_set_async(
            CacheKeys.data_quality_score(org_unit, period),
            lambda: dq_engine.get_dq_score(org_unit=org_unit, period=period),
            ttl=get_cache_ttl("data_quality"),
        )
    except Exception as exc:
        logger.error("HTMX DQ score error: %s", exc)
        return render_error_card(f"Error calculating DQ score: {exc}")

    return templates.TemplateResponse(
        request,
        "components/dq_score_card.html",
        {
            "request": request,
            "score": score_data,
        },
    )
