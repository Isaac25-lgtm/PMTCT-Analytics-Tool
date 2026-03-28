"""
Health and operational status endpoints.

These endpoints are designed for Render deployment probes and lightweight
operational visibility without introducing any persistent monitoring store.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.core.cache import get_app_cache
from app.core.config import get_settings
from app.core.session import get_session_manager
from app.indicators.models import IndicatorCategory
from app.indicators.registry import get_indicator_registry

logger = logging.getLogger(__name__)
router = APIRouter()

_startup_time: Optional[float] = None


def mark_startup_complete() -> None:
    """Record the time the application became ready to serve traffic."""
    global _startup_time
    _startup_time = time.time()


class HealthStatus(BaseModel):
    """Detailed health response."""

    status: str
    timestamp: str
    version: str
    environment: str
    uptime_seconds: Optional[float] = None
    checks: dict[str, Any] = Field(default_factory=dict)


class LivenessResponse(BaseModel):
    """Simple liveness payload."""

    status: str


class ReadinessResponse(BaseModel):
    """Readiness payload used by probes and diagnostics."""

    status: str
    ready: bool
    checks: dict[str, Any]


def _uptime_seconds() -> Optional[float]:
    if _startup_time is None:
        return None
    return max(0.0, time.time() - _startup_time)


def _build_readiness_checks() -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    all_healthy = True

    checks["startup"] = {
        "status": "ok" if _startup_time is not None else "pending",
    }
    if _startup_time is None:
        all_healthy = False

    try:
        settings = get_settings()
        checks["config"] = {
            "status": "ok",
            "environment": settings.app_env,
            "cache_enabled": settings.cache_enabled,
        }
    except Exception as exc:  # pragma: no cover - defensive
        checks["config"] = {"status": "error", "error": str(exc)}
        all_healthy = False

    try:
        registry = get_indicator_registry()
        checks["indicator_registry"] = {
            "status": "ok" if registry.is_loaded else "error",
            "indicator_count": registry.indicator_count,
        }
        if not registry.is_loaded:
            all_healthy = False
    except Exception as exc:
        checks["indicator_registry"] = {"status": "error", "error": str(exc)}
        all_healthy = False

    try:
        cache = get_app_cache()
        stats = cache.stats
        checks["cache"] = {
            "status": "ok",
            "entries": stats.total_entries,
            "hit_rate": round(stats.hit_rate, 4),
            "evictions": stats.evictions,
        }
    except Exception as exc:  # pragma: no cover - defensive
        checks["cache"] = {"status": "error", "error": str(exc)}
        all_healthy = False

    return all_healthy, checks


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Return healthy whenever the process is alive."""
    return LivenessResponse(status="healthy")


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    """Return readiness status for Render health checks."""
    ready, checks = _build_readiness_checks()
    status_value = "healthy" if ready else "unhealthy"
    if not ready:
        response.status_code = 503
    return ReadinessResponse(status=status_value, ready=ready, checks=checks)


@router.get("/health/startup")
async def startup_check(response: Response) -> dict[str, Any]:
    """Return startup completion state for slow-start diagnostics."""
    if _startup_time is None:
        response.status_code = 503
        return {"status": "starting"}

    return {
        "status": "started",
        "started_at": datetime.fromtimestamp(_startup_time, tz=timezone.utc).isoformat(),
    }


@router.get("/health", response_model=HealthStatus)
async def health_check(response: Response) -> HealthStatus:
    """Return detailed application health information."""
    settings = get_settings()
    ready, checks = _build_readiness_checks()
    degraded = False

    try:
        import resource
        import sys

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mb = usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024
        checks["memory"] = {
            "status": "ok",
            "rss_mb": round(rss_mb, 2),
        }
    except Exception:
        checks["memory"] = {"status": "unavailable"}

    try:
        session_manager = get_session_manager()
        checks["sessions"] = {
            "status": "ok",
            "active": session_manager.active_session_count,
        }
    except Exception as exc:  # pragma: no cover - defensive
        checks["sessions"] = {"status": "error", "error": str(exc)}
        degraded = True

    if not ready:
        status_value = "unhealthy"
        response.status_code = 503
    elif degraded:
        status_value = "degraded"
    else:
        status_value = "healthy"

    return HealthStatus(
        status=status_value,
        timestamp=datetime.now(timezone.utc).isoformat(),
        version=settings.app_version,
        environment=settings.app_env,
        uptime_seconds=round(_uptime_seconds(), 2) if _uptime_seconds() is not None else None,
        checks=checks,
    )


@router.get("/health/cache")
async def cache_status() -> dict[str, Any]:
    """Return application cache statistics for troubleshooting."""
    settings = get_settings()
    cache = get_app_cache()
    stats = cache.stats
    return {
        "enabled": settings.cache_enabled,
        "max_size": cache.max_size,
        "current_size": stats.total_entries,
        "utilization": round(stats.total_entries / cache.max_size, 4) if cache.max_size else 0.0,
        "hits": stats.hits,
        "misses": stats.misses,
        "hit_rate": round(stats.hit_rate, 4),
        "evictions": stats.evictions,
        "expirations": stats.expirations,
        "size_bytes": stats.total_size_bytes,
    }


@router.get("/health/stats")
async def system_stats() -> dict[str, Any]:
    """Return a backward-compatible system summary endpoint."""
    settings = get_settings()
    session_manager = get_session_manager()
    registry = get_indicator_registry()
    category_counts = {
        category.value: len(registry.get_by_category(category))
        for category in IndicatorCategory
    }

    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "debug": settings.debug,
        },
        "sessions": {
            "active": session_manager.active_session_count,
            "timeout_minutes": settings.session_timeout_minutes,
        },
        "indicators": {
            "total": registry.indicator_count,
            "categories": category_counts,
        },
        "cache": await cache_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
