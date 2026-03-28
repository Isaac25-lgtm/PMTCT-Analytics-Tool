"""
Prompt 11 AI insights API and HTMX routes.
"""

from __future__ import annotations

from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import Calculator, CurrentSession, SessCache, rate_limited, require_permission
from app.api.routes.alerts import get_alert_engine as get_session_alert_engine
from app.auth.audit import get_audit_logger
from app.auth.permissions import Permission
from app.auth.rate_limit import RateLimitOperation
from app.core.cache_keys import CacheKeys, get_cache_ttl
from app.services.ai_insights import AIInsightsEngine
from app.services.data_quality import DataQualityEngine
from app.services.llm_provider import get_llm_provider
from app.services.trends import TrendService

router = APIRouter(
    dependencies=[
        Depends(require_permission(Permission.USE_AI_INSIGHTS)),
        Depends(rate_limited(RateLimitOperation.AI_INSIGHTS)),
    ]
)
templates = Jinja2Templates(directory="app/templates")


class InsightRequest(BaseModel):
    """Request body for a single-indicator insight."""

    indicator_id: str = Field(..., description="Indicator ID")
    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")
    include_trend: bool = Field(default=True)
    history_depth: str = Field(default="12m", description="3m, 12m, 36m, or full")


class CascadeInsightRequest(BaseModel):
    """Request body for cascade analysis."""

    cascade: str = Field(..., description="hiv, syphilis, or hbv")
    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")


class PeriodInsightRequest(BaseModel):
    """Request body for period-wide insight generation."""

    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")


class QARequest(BaseModel):
    """Request body for grounded Q&A."""

    question: str = Field(..., description="Question to answer")
    org_unit: str = Field(..., description="Organisation unit UID")
    period: str = Field(..., description="DHIS2 period")


class InsightPayloadResponse(BaseModel):
    """JSON-safe insight payload."""

    insight_id: str
    insight_type: str
    content: str
    org_unit: str
    period: str
    status: str
    error_message: str | None = None
    created_at: str
    model_used: str | None = None
    tokens_used: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InsightResponseModel(BaseModel):
    """Unified JSON insight response."""

    insight: InsightPayloadResponse


async def get_insights_engine(
    session: CurrentSession,
    calculator: Calculator,
) -> AsyncIterator[AIInsightsEngine]:
    """
    Build a request-scoped Prompt 11 insights engine.

    The engine reuses the live Prompt 10 session-scoped alert engine so
    acknowledged alerts stay consistent across the alerts page and insights.
    """
    engine = AIInsightsEngine(
        calculator=calculator,
        dq_engine=DataQualityEngine(calculator=calculator),
        alert_engine=get_session_alert_engine(calculator, session),
        trend_service=TrendService(),
        llm_provider=get_llm_provider(),
    )
    try:
        yield engine
    finally:
        await engine.close()


InsightsEngine = Annotated[AIInsightsEngine, Depends(get_insights_engine)]


async def _run_with_audit(
    session: CurrentSession,
    insight_type: str,
    org_unit: str,
    generator,
):
    """Run an insight generator and emit success/error audit logs."""
    audit = get_audit_logger()
    credentials = session.credentials
    try:
        response = await generator()
    except Exception as exc:
        audit.log_ai_insight(
            insight_type=insight_type,
            user_id=credentials.user_id if credentials and credentials.user_id else "unknown",
            username=credentials.user_name if credentials and credentials.user_name else "unknown",
            org_unit_uid=org_unit,
            success=False,
            error_message=str(exc),
        )
        raise

    audit.log_ai_insight(
        insight_type=insight_type,
        user_id=credentials.user_id if credentials and credentials.user_id else "unknown",
        username=credentials.user_name if credentials and credentials.user_name else "unknown",
        org_unit_uid=org_unit,
        success=True,
    )
    return response


async def _cached_insight_response(
    session_cache: SessCache,
    cache_key: str,
    generator,
):
    """Cache session-scoped AI insights to avoid repeated expensive generation."""
    return await session_cache.get_or_set_async(
        cache_key,
        generator,
        ttl=get_cache_ttl("insights"),
    )


@router.post("/indicator", response_model=InsightResponseModel)
async def post_indicator_insight(
    request_body: InsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate a single-indicator insight as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight(
                "indicator",
                request_body.org_unit,
                request_body.period,
                indicator_id=request_body.indicator_id,
                history_depth=request_body.history_depth,
            ),
            lambda: _run_with_audit(
                session,
                "indicator",
                request_body.org_unit,
                lambda: engine.generate_indicator_insight(
                    indicator_id=request_body.indicator_id,
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                    include_trend=request_body.include_trend,
                    history_depth=request_body.history_depth,
                ),
            ),
        )
    ).to_dict()


@router.post("/cascade", response_model=InsightResponseModel)
async def post_cascade_insight(
    request_body: CascadeInsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate cascade analysis as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight(
                "cascade",
                request_body.org_unit,
                request_body.period,
                cascade=request_body.cascade,
            ),
            lambda: _run_with_audit(
                session,
                f"cascade:{request_body.cascade}",
                request_body.org_unit,
                lambda: engine.generate_cascade_insight(
                    cascade=request_body.cascade,
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.post("/alerts", response_model=InsightResponseModel)
async def post_alert_insight(
    request_body: PeriodInsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate alert synthesis as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight("alerts", request_body.org_unit, request_body.period),
            lambda: _run_with_audit(
                session,
                "alerts",
                request_body.org_unit,
                lambda: engine.generate_alert_insight(
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.post("/data-quality", response_model=InsightResponseModel)
async def post_dq_insight(
    request_body: PeriodInsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate a DQ explanation as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight("data_quality", request_body.org_unit, request_body.period),
            lambda: _run_with_audit(
                session,
                "data_quality",
                request_body.org_unit,
                lambda: engine.generate_dq_insight(
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.post("/executive-summary", response_model=InsightResponseModel)
async def post_executive_summary(
    request_body: PeriodInsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate an executive summary as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight("executive_summary", request_body.org_unit, request_body.period),
            lambda: _run_with_audit(
                session,
                "executive_summary",
                request_body.org_unit,
                lambda: engine.generate_executive_summary(
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.post("/recommendations", response_model=InsightResponseModel)
async def post_recommendations(
    request_body: InsightRequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Generate indicator recommendations as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight(
                "recommendations",
                request_body.org_unit,
                request_body.period,
                indicator_id=request_body.indicator_id,
            ),
            lambda: _run_with_audit(
                session,
                "recommendations",
                request_body.org_unit,
                lambda: engine.generate_recommendations(
                    indicator_id=request_body.indicator_id,
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.post("/qa", response_model=InsightResponseModel)
async def post_qa(
    request_body: QARequest,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
) -> dict[str, Any]:
    """Answer a current-session question as JSON."""
    return (
        await _cached_insight_response(
            session_cache,
            CacheKeys.ai_insight(
                "qa",
                request_body.org_unit,
                request_body.period,
                question=request_body.question,
            ),
            lambda: _run_with_audit(
                session,
                "qa",
                request_body.org_unit,
                lambda: engine.generate_qa_response(
                    question=request_body.question,
                    org_unit=request_body.org_unit,
                    period=request_body.period,
                ),
            ),
        )
    ).to_dict()


@router.get("/htmx/indicator-card", response_class=HTMLResponse, include_in_schema=False)
async def htmx_indicator_card(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    indicator_id: str = Query(...),
    org_unit: str = Query(...),
    period: str = Query(...),
    include_trend: bool = Query(default=True),
    history_depth: str = Query(default="12m"),
) -> HTMLResponse:
    """Render the indicator insight card partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight(
            "indicator",
            org_unit,
            period,
            indicator_id=indicator_id,
            history_depth=history_depth,
        ),
        lambda: _run_with_audit(
            session,
            "indicator",
            org_unit,
            lambda: engine.generate_indicator_insight(
                indicator_id=indicator_id,
                org_unit=org_unit,
                period=period,
                include_trend=include_trend,
                history_depth=history_depth,
            ),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_card.html",
        {
            "request": request,
            "insight": response.insight,
            "insight_title": "Indicator insight",
        },
    )


@router.get("/htmx/cascade-card", response_class=HTMLResponse, include_in_schema=False)
async def htmx_cascade_card(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    cascade: str = Query(...),
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the cascade insight card partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight("cascade", org_unit, period, cascade=cascade),
        lambda: _run_with_audit(
            session,
            f"cascade:{cascade}",
            org_unit,
            lambda: engine.generate_cascade_insight(
                cascade=cascade,
                org_unit=org_unit,
                period=period,
            ),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_card.html",
        {
            "request": request,
            "insight": response.insight,
            "insight_title": f"{cascade.upper()} cascade analysis",
        },
    )


@router.get("/htmx/alerts", response_class=HTMLResponse, include_in_schema=False)
async def htmx_alerts(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the alert synthesis card partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight("alerts", org_unit, period),
        lambda: _run_with_audit(
            session,
            "alerts",
            org_unit,
            lambda: engine.generate_alert_insight(org_unit=org_unit, period=period),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_card.html",
        {
            "request": request,
            "insight": response.insight,
            "insight_title": "Alert synthesis",
        },
    )


@router.get("/htmx/data-quality", response_class=HTMLResponse, include_in_schema=False)
async def htmx_data_quality(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the DQ insight card partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight("data_quality", org_unit, period),
        lambda: _run_with_audit(
            session,
            "data_quality",
            org_unit,
            lambda: engine.generate_dq_insight(org_unit=org_unit, period=period),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_card.html",
        {
            "request": request,
            "insight": response.insight,
            "insight_title": "Data-quality explanation",
        },
    )


@router.get("/htmx/recommendations", response_class=HTMLResponse, include_in_schema=False)
async def htmx_recommendations(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    indicator_id: str = Query(...),
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the recommendation partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight(
            "recommendations",
            org_unit,
            period,
            indicator_id=indicator_id,
        ),
        lambda: _run_with_audit(
            session,
            "recommendations",
            org_unit,
            lambda: engine.generate_recommendations(
                indicator_id=indicator_id,
                org_unit=org_unit,
                period=period,
            ),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_recommendations.html",
        {
            "request": request,
            "insight": response.insight,
        },
    )


@router.get("/htmx/executive-summary", response_class=HTMLResponse, include_in_schema=False)
async def htmx_executive_summary(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    org_unit: str = Query(...),
    period: str = Query(...),
) -> HTMLResponse:
    """Render the executive summary card partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight("executive_summary", org_unit, period),
        lambda: _run_with_audit(
            session,
            "executive_summary",
            org_unit,
            lambda: engine.generate_executive_summary(org_unit=org_unit, period=period),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/insight_card.html",
        {
            "request": request,
            "insight": response.insight,
            "insight_title": "Executive summary",
        },
    )


@router.post("/htmx/qa", response_class=HTMLResponse, include_in_schema=False)
async def htmx_qa(
    request: Request,
    engine: InsightsEngine,
    session: CurrentSession,
    session_cache: SessCache,
    question: str = Form(...),
    org_unit: str = Form(...),
    period: str = Form(...),
) -> HTMLResponse:
    """Render the Q&A response partial."""
    response = await _cached_insight_response(
        session_cache,
        CacheKeys.ai_insight("qa", org_unit, period, question=question),
        lambda: _run_with_audit(
            session,
            "qa",
            org_unit,
            lambda: engine.generate_qa_response(
                question=question,
                org_unit=org_unit,
                period=period,
            ),
        ),
    )
    return templates.TemplateResponse(
        request,
        "components/qa_response.html",
        {
            "request": request,
            "insight": response.insight,
            "question": question,
        },
    )
