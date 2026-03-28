"""
FastAPI middleware for session, rate limiting, CSRF, and security headers.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Callable, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth.audit import get_audit_logger
from app.auth.rate_limit import RateLimitOperation, get_rate_limiter
from app.core.config import get_settings
from app.core.logging_config import RequestLogger, request_id_var
from app.core.session import get_session_manager

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {
    "/",
    "/login",
    "/health",
    "/health/ready",
    "/health/live",
    "/health/startup",
    "/health/cache",
    "/health/stats",
    "/auth/login",
    "/auth/status",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/api/reports/periods",
}

PUBLIC_PREFIXES = ["/static/"]


def is_public_path(path: str) -> bool:
    """Check whether a request path should bypass authentication."""
    if path in PUBLIC_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return True
    if path.startswith("/api/indicators/"):
        return "/calculate" not in path
    return path == "/api/indicators"


def should_require_api_auth(path: str) -> bool:
    """Return True when middleware should enforce a 401 response directly."""
    if path.startswith("/api/"):
        return not is_public_path(path)
    if path.startswith("/auth/"):
        return path not in PUBLIC_PATHS
    return False


def has_session_dependency_override(request: Request) -> bool:
    """Return True when tests override the session dependency directly."""
    try:
        from app.api.deps import get_current_session

        return get_current_session in getattr(request.app, "dependency_overrides", {})
    except Exception:
        return False


def get_client_ip(request: Request) -> Optional[str]:
    """Extract a proxy-aware client IP address."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


class SessionMiddleware(BaseHTTPMiddleware):
    """Load and refresh the server-side session for the current request."""

    def __init__(
        self,
        app: ASGIApp,
        session_timeout_minutes: int = 60,
        cookie_secure: bool = True,
    ) -> None:
        super().__init__(app)
        self.session_timeout_minutes = session_timeout_minutes
        self.cookie_secure = cookie_secure
        self.session_manager = get_session_manager()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request.state.session = None
        request.state.session_id = None

        session_id = request.cookies.get("session_id")
        if session_id:
            raw_session = self.session_manager.peek_session(session_id)
            if raw_session and raw_session.is_expired:
                if raw_session.credentials:
                    duration = int((time.time() - raw_session.created_at.timestamp()))
                    get_audit_logger().log_session_expired(
                        user_id=raw_session.credentials.user_id or "unknown",
                        username=raw_session.credentials.user_name or raw_session.credentials.username or "unknown",
                        session_id=session_id,
                        session_duration_seconds=max(0, duration),
                    )
                self.session_manager.destroy_session(session_id)
            else:
                session = self.session_manager.get_session(session_id)
                if session and session.is_authenticated and not session.is_expired:
                    self.session_manager.refresh_session(session_id)
                    request.state.session = session
                    request.state.session_id = session_id

        if (
            should_require_api_auth(request.url.path)
            and not request.state.session
            and not has_session_dependency_override(request)
        ):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

        response = await call_next(request)

        if request.state.session_id and request.state.session:
            response.set_cookie(
                key="session_id",
                value=request.state.session_id,
                httponly=True,
                secure=self.cookie_secure,
                samesite="lax",
            )
        elif session_id and not request.state.session:
            response.delete_cookie(
                key="session_id",
                httponly=True,
                secure=self.cookie_secure,
                samesite="lax",
            )

        return response


class GeneralRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the general per-session API rate limit to authenticated API calls."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)

        session = getattr(request.state, "session", None)
        if not session:
            return await call_next(request)

        limiter = get_rate_limiter()
        result = limiter.check(
            RateLimitOperation.API_GENERAL,
            session_id=getattr(request.state, "session_id", None),
            user_id=session.credentials.user_id if session.credentials else None,
            ip_address=get_client_ip(request),
        )
        if result.allowed:
            return await call_next(request)

        credentials = session.credentials
        get_audit_logger().log_rate_limit_exceeded(
            user_id=credentials.user_id if credentials and credentials.user_id else "unknown",
            username=credentials.user_name if credentials and credentials.user_name else "unknown",
            operation=RateLimitOperation.API_GENERAL.value,
            current_count=result.current_count,
            limit=result.limit,
            window_seconds=result.reset_seconds,
            ip_address=get_client_ip(request),
        )
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded. Retry after {result.reset_seconds} seconds."},
            headers={"Retry-After": str(result.reset_seconds)},
        )


class CSRFMiddleware(BaseHTTPMiddleware):
    """Protect state-changing requests with a session-bound CSRF token."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    CSRF_HEADER = "X-CSRF-Token"
    CSRF_FORM_FIELD = "csrf_token"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if not settings.csrf_enabled or request.method in self.SAFE_METHODS:
            return await call_next(request)

        if self._is_exempt(request.url.path):
            return await call_next(request)

        session = getattr(request.state, "session", None)
        if not session:
            return await call_next(request)

        expected_token = session.user_data.get("csrf_token")
        if not expected_token:
            expected_token = secrets.token_urlsafe(32)
            session.user_data["csrf_token"] = expected_token

        provided_token = request.headers.get(self.CSRF_HEADER)
        if not provided_token:
            content_type = request.headers.get("content-type", "")
            if "form" in content_type:
                form = await request.form()
                provided_token = form.get(self.CSRF_FORM_FIELD)

        if not provided_token or not secrets.compare_digest(str(provided_token), str(expected_token)):
            logger.warning(
                "CSRF validation failed for %s %s",
                request.method,
                request.url.path,
            )
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})

        return await call_next(request)

    @staticmethod
    def _is_exempt(path: str) -> bool:
        settings = get_settings()
        if path in set(settings.csrf_exempt_paths):
            return True
        return path.startswith("/health")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log request/response pairs for operational visibility."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        request_logger = RequestLogger()
        start = time.time()
        request_logger.log_request(
            method=request.method,
            path=request.url.path,
            request_id=request_id,
            client_ip=get_client_ip(request),
        )

        try:
            response = await call_next(request)
        finally:
            duration_ms = (time.time() - start) * 1000
            status_code = getattr(locals().get("response"), "status_code", 500)
            request_logger.log_response(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_id=request_id,
            )
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline browser security headers to every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        if "server" in response.headers:
            del response.headers["server"]
        return response


def get_csrf_token(request: Request) -> Optional[str]:
    """Return the session CSRF token, generating it lazily when needed."""
    session = getattr(request.state, "session", None)
    if not session:
        return None

    token = session.user_data.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session.user_data["csrf_token"] = token
    return token
