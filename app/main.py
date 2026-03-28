"""
FastAPI application factory for PMTCT Triple Elimination tool.

Stateless design:
- No database
- Credentials in session memory only
- Session cleared on browser close
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import (
    CSRFMiddleware,
    GeneralRateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
)
from app.api.routes import alerts, auth, data_quality, exports, health, indicators, insights, org_units, pages, reports, trends
from app.core.cache import clear_all_caches
from app.core.config import get_settings
from app.core.connection_pool import close_async_client
from app.core.logging_config import configure_logging
from app.core.session import get_session_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown tasks for the application."""
    settings = get_settings()
    configure_logging(level=settings.log_level, format=settings.log_format)
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    session_manager = get_session_manager()
    logger.info(
        "Session manager initialized (timeout: %d minutes)",
        settings.session_timeout_minutes,
    )

    try:
        from app.indicators.registry import get_indicator_registry

        registry = get_indicator_registry()
        logger.info("Loaded %d indicators", registry.indicator_count)
    except Exception as exc:
        logger.error("Failed to load indicator registry: %s", exc)

    health.mark_startup_complete()
    yield

    logger.info("Shutting down...")
    cleaned = session_manager.cleanup_expired()
    logger.info("Cleaned %d expired sessions", cleaned)
    await close_async_client()
    cache_counts = clear_all_caches()
    logger.info(
        "Cleared caches on shutdown (app=%d, session=%d)",
        cache_counts["app"],
        cache_counts["sessions"],
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="PMTCT Triple Elimination Analytics Tool for Uganda MoH",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(CSRFMiddleware)
    app.add_middleware(GeneralRateLimitMiddleware)
    app.add_middleware(
        SessionMiddleware,
        session_timeout_minutes=settings.session_timeout_minutes,
        cookie_secure=not settings.debug,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    if settings.debug:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    repo_root = Path(__file__).resolve().parent.parent
    static_dir = repo_root / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    else:
        logger.warning("Static directory not found: %s", static_dir)

    app.include_router(health.router, tags=["Health"])
    app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    app.include_router(
        indicators.router,
        prefix="/api/indicators",
        tags=["Indicators"],
    )
    app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
    app.include_router(exports.router, prefix="/api/exports", tags=["Exports"])
    app.include_router(data_quality.router, prefix="/api/data-quality", tags=["Data Quality"])
    app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
    app.include_router(insights.router, prefix="/api/insights", tags=["AI Insights"])
    app.include_router(org_units.router, prefix="/api", tags=["Org Units"])
    app.include_router(trends.router, prefix="/api/trends", tags=["Trends"])
    app.include_router(pages.router, tags=["Pages"])

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
