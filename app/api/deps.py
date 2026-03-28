"""
FastAPI dependency helpers bound to the live app contracts.

Prompt 13 extends the existing session, connector, calculator, and org-unit
dependencies with RBAC, audit logging, and reusable rate-limit guards.
"""

from __future__ import annotations

from typing import Annotated, Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from app.auth.audit import AuditLogger, get_audit_logger
from app.auth.permissions import Permission, check_any_permission
from app.auth.rbac import RBACEngine, get_rbac_engine
from app.auth.rate_limit import RateLimitOperation, RateLimiter, get_rate_limiter as get_rate_limiter_instance
from app.connectors.cached_connector import CachedDHIS2Connector, build_cached_connector
from app.core.cache import InMemoryCache, SessionCache, get_app_cache, get_session_cache
from app.core.config import get_settings
from app.core.session import UserSession
from app.indicators.cached_calculator import CachedIndicatorCalculator, build_cached_calculator
from app.indicators.calculator import load_population_data
from app.indicators.registry import IndicatorRegistry, get_indicator_registry
from app.services.cached_org_units import CachedOrgUnitService, build_cached_org_unit_service


async def get_current_session(request: Request) -> UserSession:
    """Return the current authenticated session or raise 401."""
    session = getattr(request.state, "session", None)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not session.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if session.is_expired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session


async def get_optional_session(request: Request) -> Optional[UserSession]:
    """Return the current session when present and valid."""
    session = getattr(request.state, "session", None)
    if session and session.is_authenticated and not session.is_expired:
        return session
    return None


async def get_dhis2_connector(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> CachedDHIS2Connector:
    """Return the cached DHIS2 connector for the active session."""
    return build_cached_connector(session)


async def get_indicator_calculator(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> CachedIndicatorCalculator:
    """Return the cached indicator calculator for the active session."""
    return build_cached_calculator(session, load_population_data())


def get_registry() -> IndicatorRegistry:
    """Return the shared indicator registry."""
    return get_indicator_registry()


async def get_org_unit_service(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> CachedOrgUnitService:
    """Return the cached Prompt 12 org-unit service for the active session."""
    return build_cached_org_unit_service(session)


async def get_app_cache_dep() -> InMemoryCache:
    """Return the shared application cache."""
    return get_app_cache()


async def get_session_cache_dep(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> SessionCache:
    """Return the current session-scoped cache."""
    return get_session_cache(session.session_id)


async def get_rbac(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> RBACEngine:
    """Return the RBAC engine for the current session."""
    return get_rbac_engine(session)


async def get_audit() -> AuditLogger:
    """Return the configured audit logger."""
    return get_audit_logger()


async def get_rate_limiter() -> RateLimiter:
    """Return the shared in-memory rate limiter."""
    return get_rate_limiter_instance()


def get_client_ip(request: Request) -> Optional[str]:
    """Extract the client IP address from proxy-aware request headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def require_permission(permission: Permission) -> Callable:
    """Dependency factory that enforces a single permission."""

    async def dependency(
        request: Request,
        session: Annotated[UserSession, Depends(get_current_session)],
    ) -> None:
        rbac = get_rbac_engine(session)
        result = rbac.authorize(permission)
        if result.authorized:
            return

        get_audit_logger().log_permission_denied(
            user_id=rbac.user_id,
            username=rbac.username,
            permission=permission.value,
            role=rbac.role.value,
            resource_type="route",
            resource_id=request.url.path,
            ip_address=get_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.denial_reason or "Permission denied",
        )

    return dependency


def require_any_permission(permissions: set[Permission]) -> Callable:
    """Dependency factory that accepts any one of the provided permissions."""

    async def dependency(
        request: Request,
        session: Annotated[UserSession, Depends(get_current_session)],
    ) -> None:
        role_info = get_rbac_engine(session).role_info
        result = check_any_permission(role_info, permissions)
        if result.granted:
            return

        get_audit_logger().log_permission_denied(
            user_id=role_info.user_id,
            username=role_info.username,
            permission=",".join(sorted(permission.value for permission in permissions)),
            role=role_info.role.value,
            resource_type="route",
            resource_id=request.url.path,
            ip_address=get_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.reason or "Permission denied",
        )

    return dependency


def require_role(min_role) -> Callable:
    """Dependency factory that enforces a minimum role level."""

    async def dependency(
        request: Request,
        session: Annotated[UserSession, Depends(get_current_session)],
    ) -> None:
        rbac = get_rbac_engine(session)
        if rbac.role >= min_role:
            return

        get_audit_logger().log_permission_denied(
            user_id=rbac.user_id,
            username=rbac.username,
            permission=f"min_role:{min_role.value}",
            role=rbac.role.value,
            resource_type="route",
            resource_id=request.url.path,
            ip_address=get_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires role {min_role.value} or higher",
        )

    return dependency


def rate_limited(operation: RateLimitOperation) -> Callable:
    """Dependency factory that applies a configured rate limit."""

    async def dependency(
        request: Request,
        session: Annotated[UserSession, Depends(get_current_session)],
    ) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return

        limiter = get_rate_limiter_instance()
        result = limiter.check(
            operation,
            session_id=session.session_id,
            user_id=session.credentials.user_id if session.credentials else None,
            ip_address=get_client_ip(request),
        )
        if result.allowed:
            return

        rbac = get_rbac_engine(session)
        get_audit_logger().log_rate_limit_exceeded(
            user_id=rbac.user_id,
            username=rbac.username,
            operation=operation.value,
            current_count=result.current_count,
            limit=result.limit,
            window_seconds=result.reset_seconds,
            ip_address=get_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {result.reset_seconds} seconds.",
            headers={"Retry-After": str(result.reset_seconds)},
        )

    return dependency


CurrentSession = Annotated[UserSession, Depends(get_current_session)]
OptionalSession = Annotated[Optional[UserSession], Depends(get_optional_session)]
AppCache = Annotated[InMemoryCache, Depends(get_app_cache_dep)]
SessCache = Annotated[SessionCache, Depends(get_session_cache_dep)]
Connector = Annotated[CachedDHIS2Connector, Depends(get_dhis2_connector)]
Calculator = Annotated[CachedIndicatorCalculator, Depends(get_indicator_calculator)]
Registry = Annotated[IndicatorRegistry, Depends(get_registry)]
OrgUnitSvc = Annotated[CachedOrgUnitService, Depends(get_org_unit_service)]
RBAC = Annotated[RBACEngine, Depends(get_rbac)]
Audit = Annotated[AuditLogger, Depends(get_audit)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
