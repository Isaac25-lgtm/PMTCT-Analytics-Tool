"""
Authentication routes for DHIS2 login/logout.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.auth.dhis2_auth import DHIS2AuthError, DHIS2AuthHandler
from app.auth.audit import get_audit_logger
from app.auth.permissions import get_user_permissions
from app.auth.rate_limit import RateLimitOperation, get_rate_limiter
from app.auth.roles import get_role_from_session, resolve_user_role, store_role_in_session
from app.core.config import get_settings, normalize_dhis2_base_url
from app.core.session import AuthMethod, UserSession, get_session_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class LoginRequest(BaseModel):
    """Login request with DHIS2 credentials."""

    dhis2_url: HttpUrl = Field(
        ...,
        description="DHIS2 instance URL, with or without a trailing /api segment",
        examples=["https://hmis.health.go.ug", "https://hmis.health.go.ug/api"],
    )
    auth_method: AuthMethod = Field(default=AuthMethod.BASIC)
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    pat_token: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def validate_credentials(self) -> "LoginRequest":
        """Validate the credential fields for the selected auth method."""
        if self.auth_method == AuthMethod.BASIC:
            if not self.username or not self.password:
                raise ValueError("Username and password required for basic auth")
        elif self.auth_method == AuthMethod.PAT and not self.pat_token:
            raise ValueError("PAT token required for PAT auth")
        return self


class LoginResponse(BaseModel):
    """Successful login response."""

    success: bool = True
    user_id: str
    user_name: str
    org_units: list[dict]
    role: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)
    message: str = "Login successful"


class AuthStatusResponse(BaseModel):
    """Authentication status response."""

    authenticated: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    org_units: Optional[list[dict]] = None
    role: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)
    session_expires_at: Optional[datetime] = None


class LogoutResponse(BaseModel):
    """Logout response."""

    success: bool = True
    message: str = "Logged out successfully"


def _get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    login_data: LoginRequest,
) -> LoginResponse:
    """Authenticate with DHIS2 and create a server-side session."""
    settings = get_settings()
    session_manager = get_session_manager()
    auth_handler = DHIS2AuthHandler()
    audit = get_audit_logger()
    base_url = normalize_dhis2_base_url(str(login_data.dhis2_url)) or ""
    client_ip = _get_client_ip(request)

    if settings.rate_limit_enabled:
        login_limit = get_rate_limiter().check(
            RateLimitOperation.LOGIN,
            ip_address=client_ip,
        )
        if not login_limit.allowed:
            audit.log_rate_limit_exceeded(
                user_id="anonymous",
                username="anonymous",
                operation=RateLimitOperation.LOGIN.value,
                current_count=login_limit.current_count,
                limit=login_limit.limit,
                window_seconds=login_limit.reset_seconds,
                ip_address=client_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many login attempts. Retry after {login_limit.reset_seconds} seconds.",
                headers={"Retry-After": str(login_limit.reset_seconds)},
            )

    try:
        if login_data.auth_method == AuthMethod.BASIC:
            credentials = await auth_handler.authenticate_basic(
                base_url=base_url,
                username=login_data.username or "",
                password=login_data.password or "",
            )
        else:
            credentials = await auth_handler.authenticate_pat(
                base_url=base_url,
                pat_token=login_data.pat_token or "",
            )
    except DHIS2AuthError as exc:
        logger.warning("Authentication failed: %s", exc)
        audit.log_login_failure(
            username=login_data.username or "pat-user",
            reason=str(exc),
            ip_address=client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    role_info = resolve_user_role(
        user_id=credentials.user_id or "",
        username=credentials.user_name or credentials.username or "",
        authorities=credentials.authorities,
        org_units=credentials.org_units,
    )

    session_id = str(uuid4())
    now = datetime.now(timezone.utc)
    session = UserSession(
        session_id=session_id,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.session_timeout_minutes),
        credentials=credentials,
    )
    store_role_in_session(session, role_info)
    session.user_data["csrf_token"] = secrets.token_urlsafe(32)
    session_manager.create_session(session)

    request.state.session = session
    request.state.session_id = session_id

    logger.info(
        "User %s logged in from %s",
        credentials.user_name,
        client_ip or "unknown",
    )
    audit.log_login_success(
        user_id=credentials.user_id or "",
        username=credentials.user_name or credentials.username or "",
        org_units=credentials.org_units,
        ip_address=client_ip,
        session_id=session_id,
    )

    return LoginResponse(
        user_id=credentials.user_id or "",
        user_name=credentials.user_name or "",
        org_units=credentials.org_units,
        role=role_info.role.value,
        permissions=sorted(permission.value for permission in get_user_permissions(role_info)),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request) -> LogoutResponse:
    """Destroy the active session and clear the session cookie."""
    session_manager = get_session_manager()
    audit = get_audit_logger()
    session_id = request.cookies.get("session_id")

    if session_id:
        session = session_manager.get_session(session_id)
        if session and session.credentials:
            logger.info("User %s logged out", session.credentials.user_name)
            session_duration = int((datetime.now(timezone.utc) - session.created_at).total_seconds())
            audit.log_logout(
                user_id=session.credentials.user_id or "unknown",
                username=session.credentials.user_name or session.credentials.username or "unknown",
                session_id=session_id,
                session_duration_seconds=max(0, session_duration),
            )
        session_manager.destroy_session(session_id)

    request.state.session = None
    request.state.session_id = None

    return LogoutResponse()


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    """Return the current authentication state for the browser session."""
    session = getattr(request.state, "session", None)

    if session and session.is_authenticated and not session.is_expired:
        credentials = session.credentials
        role_info = get_role_from_session(session)
        return AuthStatusResponse(
            authenticated=True,
            user_id=credentials.user_id if credentials else None,
            user_name=credentials.user_name if credentials else None,
            org_units=credentials.org_units if credentials else None,
            role=role_info.role.value if role_info else None,
            permissions=(
                sorted(permission.value for permission in get_user_permissions(role_info))
                if role_info
                else []
            ),
            session_expires_at=session.expires_at,
        )

    return AuthStatusResponse(authenticated=False)


@router.post("/refresh")
async def refresh_session(request: Request) -> dict[str, bool | str | None]:
    """Refresh session expiry time for the active browser session."""
    session_manager = get_session_manager()
    session = getattr(request.state, "session", None)
    session_id = getattr(request.state, "session_id", None)

    if not session or not session.is_authenticated or session.is_expired or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    session_manager.refresh_session(session_id)
    updated_session = session_manager.get_session(session_id)

    return {
        "success": True,
        "expires_at": updated_session.expires_at.isoformat() if updated_session else None,
    }
