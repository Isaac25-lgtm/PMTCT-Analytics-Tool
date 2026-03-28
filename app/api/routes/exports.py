"""
Export routes for PDF, XLSX, and CSV downloads.

Exports are triggered via plain JavaScript fetch calls, not HTMX swaps.
Each request calculates fresh results, builds the file in memory, and streams it
back without writing anything to disk.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import Audit, Calculator, CurrentSession, RBAC, get_client_ip
from app.auth.permissions import Permission
from app.auth.rbac import PermissionDeniedError
from app.auth.rate_limit import RateLimitOperation, get_rate_limiter
from app.api.routes.reports import (
    build_month_period_range,
    build_period_label,
    calculate_scorecard_indicators,
    derive_expected_pregnancies,
    get_fertility_rate,
    resolve_org_unit_name,
)
from app.core.config import get_settings
from app.indicators.models import IndicatorCategory
from app.services.export import ExportDependencyError, ExportService

logger = logging.getLogger(__name__)
router = APIRouter()
export_service = ExportService()

CASCADE_CONFIGS = {
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


class ExportScorecardRequest(BaseModel):
    """Request body for scorecard export."""

    format: str = Field(..., pattern="^(pdf|xlsx|csv)$")
    org_unit: str
    period: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    org_unit_name: Optional[str] = None
    expected_pregnancies: Optional[int] = Field(default=None, ge=0)
    annual_population: Optional[int] = Field(default=None, ge=0)


class ExportCascadeRequest(BaseModel):
    """Request body for cascade export."""

    format: str = Field(..., pattern="^(pdf|xlsx|csv)$")
    cascade_type: str = Field(..., pattern="^(hiv|hbv|syphilis)$")
    org_unit: str
    period: str
    org_unit_name: Optional[str] = None


class ExportSupplyRequest(BaseModel):
    """Request body for supply export."""

    format: str = Field(..., pattern="^(pdf|xlsx|csv)$")
    org_unit: str
    period: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    periodicity: Optional[str] = None
    org_unit_name: Optional[str] = None


def resolve_selected_period(
    period: str | None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    """Resolve the concrete period value to use for calculations/exports."""
    resolved = period or period_end or period_start
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A period or period range is required.",
        )
    return resolved


def build_export_period_label(
    period: str,
    period_start: str | None = None,
    period_end: str | None = None,
    periodicity: str | None = None,
) -> str:
    """Build a human-readable period label for export metadata."""
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


def download_response(file_bytes: bytes, filename: str, media_type: str) -> StreamingResponse:
    """Create a memory-backed download response."""
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def enforce_export_access(
    *,
    request_format: str,
    session: CurrentSession,
    rbac: RBAC,
    audit: Audit,
    request_ip: str | None = None,
) -> None:
    """Apply Prompt 13 permission and rate-limit checks for export requests."""
    settings = get_settings()
    try:
        rbac.require(Permission.EXPORT_REPORTS)
        if request_format.lower() == "pdf":
            rbac.require(Permission.EXPORT_PDF)
    except PermissionDeniedError as exc:
        audit.log_permission_denied(
            user_id=rbac.user_id,
            username=rbac.username,
            permission=exc.permission.value,
            role=rbac.role.value,
            resource_type="export",
            resource_id=request_format.lower(),
            ip_address=request_ip,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if not settings.rate_limit_enabled:
        return

    limiter = get_rate_limiter()
    operation = RateLimitOperation.PDF_EXPORT if request_format.lower() == "pdf" else RateLimitOperation.EXCEL_EXPORT
    result = limiter.check(
        operation,
        session_id=session.session_id,
        user_id=session.credentials.user_id if session.credentials else None,
        ip_address=request_ip,
    )
    if result.allowed:
        return

    audit.log_rate_limit_exceeded(
        user_id=rbac.user_id,
        username=rbac.username,
        operation=operation.value,
        current_count=result.current_count,
        limit=result.limit,
        window_seconds=result.reset_seconds,
        ip_address=request_ip,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded. Retry after {result.reset_seconds} seconds.",
        headers={"Retry-After": str(result.reset_seconds)},
    )


@router.post("/scorecard")
async def export_scorecard(
    http_request: Request,
    request_body: ExportScorecardRequest,
    calculator: Calculator,
    session: CurrentSession,
    rbac: RBAC,
    audit: Audit,
) -> StreamingResponse:
    """Export the WHO validation scorecard."""
    enforce_export_access(
        request_format=request_body.format,
        session=session,
        rbac=rbac,
        audit=audit,
        request_ip=get_client_ip(http_request),
    )
    org_unit_name = resolve_org_unit_name(session, request_body.org_unit, request_body.org_unit_name)
    resolved_period = resolve_selected_period(
        request_body.period,
        request_body.period_start,
        request_body.period_end,
    )
    periods = build_month_period_range(
        resolved_period,
        request_body.period_start,
        request_body.period_end,
    )
    period_label = build_period_label(periods)
    expected_pregnancies = request_body.expected_pregnancies
    if request_body.annual_population is not None:
        expected_pregnancies = derive_expected_pregnancies(
            request_body.annual_population,
            periods,
            get_fertility_rate(),
        )

    if expected_pregnancies is not None:
        calculator.set_expected_pregnancies(request_body.org_unit, expected_pregnancies)
    else:
        calculator.clear_expected_pregnancies(request_body.org_unit)

    indicators = [
        indicator.model_dump()
        for indicator in await calculate_scorecard_indicators(
            calculator,
            request_body.org_unit,
            org_unit_name,
            periods,
        )
    ]
    total_with_target = sum(1 for indicator in indicators if indicator.get("target") is not None)
    meeting_target = sum(1 for indicator in indicators if indicator.get("meets_target"))
    summary = {
        "total": len(indicators),
        "meeting_target": meeting_target,
        "total_with_target": total_with_target,
        "score_pct": (meeting_target / total_with_target * 100) if total_with_target else 0,
    }

    try:
        file_bytes = export_service.export_scorecard(
            request_body.format,
            indicators,
            summary,
            request_body.org_unit,
            org_unit_name,
            period_label or resolved_period,
        )
    except ExportDependencyError as exc:
        logger.error("Export dependency error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Scorecard export failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate scorecard export",
        ) from exc

    audit.log_export(
        export_type=request_body.format,
        user_id=rbac.user_id,
        username=rbac.username,
        org_unit_uid=request_body.org_unit,
        period=period_label or resolved_period,
        indicators=[indicator["id"] for indicator in indicators],
    )

    return download_response(
        file_bytes,
        export_service.get_filename("scorecard", org_unit_name or request_body.org_unit, periods[-1], request_body.format),
        export_service.get_content_type(request_body.format),
    )


@router.post("/cascade")
async def export_cascade(
    http_request: Request,
    request_body: ExportCascadeRequest,
    calculator: Calculator,
    session: CurrentSession,
    rbac: RBAC,
    audit: Audit,
) -> StreamingResponse:
    """Export an HIV, HBV, or syphilis cascade report."""
    enforce_export_access(
        request_format=request_body.format,
        session=session,
        rbac=rbac,
        audit=audit,
        request_ip=get_client_ip(http_request),
    )
    org_unit_name = resolve_org_unit_name(session, request_body.org_unit, request_body.org_unit_name)
    config = CASCADE_CONFIGS[request_body.cascade_type]

    categories = [config["category"]]
    if request_body.cascade_type == "syphilis":
        categories.append(IndicatorCategory.WHO_VALIDATION)

    result_set = await calculator.calculate_all(
        org_unit=request_body.org_unit,
        period=request_body.period,
        org_unit_name=org_unit_name,
        categories=categories,
    )

    order = list(config["order"])
    if "include_who" in config:
        order = list(config["include_who"]) + order

    result_map = {result.indicator_id: result for result in result_set.results}
    steps: list[dict[str, object]] = []
    for indicator_id in order:
        result = result_map.get(indicator_id)
        if result is None:
            steps.append(
                {
                    "indicator_id": indicator_id,
                    "name": indicator_id,
                    "count": None,
                    "percentage": None,
                    "formatted_value": "N/A",
                }
            )
            continue

        steps.append(
            {
                "indicator_id": result.indicator_id,
                "name": result.indicator_name,
                "count": result.numerator_value,
                "percentage": result.result_value,
                "formatted_value": result.formatted_result,
            }
        )

    try:
        file_bytes = export_service.export_cascade(
            request_body.format,
            request_body.cascade_type,
            steps,
            request_body.org_unit,
            org_unit_name,
            request_body.period,
        )
    except ExportDependencyError as exc:
        logger.error("Export dependency error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Cascade export failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate cascade export",
        ) from exc

    audit.log_export(
        export_type=request_body.format,
        user_id=rbac.user_id,
        username=rbac.username,
        org_unit_uid=request_body.org_unit,
        period=request_body.period,
        indicators=order,
    )

    return download_response(
        file_bytes,
        export_service.get_filename(
            f"{request_body.cascade_type}_cascade",
            org_unit_name or request_body.org_unit,
            request_body.period,
            request_body.format,
        ),
        export_service.get_content_type(request_body.format),
    )


@router.post("/supply")
async def export_supply(
    http_request: Request,
    request_body: ExportSupplyRequest,
    calculator: Calculator,
    session: CurrentSession,
    rbac: RBAC,
    audit: Audit,
) -> StreamingResponse:
    """Export the supply chain status report."""
    enforce_export_access(
        request_format=request_body.format,
        session=session,
        rbac=rbac,
        audit=audit,
        request_ip=get_client_ip(http_request),
    )
    org_unit_name = resolve_org_unit_name(session, request_body.org_unit, request_body.org_unit_name)
    resolved_period = resolve_selected_period(
        request_body.period,
        request_body.period_start,
        request_body.period_end,
    )
    period_label = build_export_period_label(
        resolved_period,
        request_body.period_start,
        request_body.period_end,
        request_body.periodicity,
    )

    from app.supply.service import SupplyService

    supply_svc = SupplyService(session=session, calculator=calculator)
    report = await supply_svc.get_supply_report(
        org_unit=request_body.org_unit,
        period=resolved_period,
        org_unit_name=org_unit_name,
    )
    commodities = report.to_legacy_commodities()

    try:
        file_bytes = export_service.export_supply(
            request_body.format,
            commodities,
            request_body.org_unit,
            org_unit_name,
            period_label,
        )
    except ExportDependencyError as exc:
        logger.error("Export dependency error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Supply export failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate supply export",
        ) from exc

    audit.log_export(
        export_type=request_body.format,
        user_id=rbac.user_id,
        username=rbac.username,
        org_unit_uid=request_body.org_unit,
        period=period_label,
        indicators=["SUP-01", "SUP-02", "SUP-03", "SUP-04", "SUP-05", "SUP-06"],
    )

    return download_response(
        file_bytes,
        export_service.get_filename("supply_status", org_unit_name or request_body.org_unit, resolved_period, request_body.format),
        export_service.get_content_type(request_body.format),
    )
