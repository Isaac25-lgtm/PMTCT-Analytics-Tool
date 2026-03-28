"""
Session-based credential storage.
No persistence - credentials exist only in memory for session duration.

WARNING:
- This is an in-memory backend session store suitable for single-instance deployment.
- It is NOT encrypted by itself.
- Browser-cookie signing / secure cookie configuration belongs in the web layer.
- For multi-instance deployment, replace with Redis-backed sessions.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthMethod(Enum):
    """Supported DHIS2 authentication methods."""

    BASIC = "basic"
    PAT = "pat"


@dataclass
class DHIS2Credentials:
    """DHIS2 connection credentials stored in backend session memory only."""

    base_url: str
    auth_method: AuthMethod
    username: Optional[str] = None
    password: Optional[str] = None
    pat_token: Optional[str] = None

    user_id: Optional[str] = None
    user_name: Optional[str] = None
    authorities: List[str] = field(default_factory=list)
    org_units: List[Dict[str, Any]] = field(default_factory=list)

    def get_auth_header(self) -> Dict[str, str]:
        """Return appropriate Authorization header."""
        if self.auth_method == AuthMethod.PAT:
            return {"Authorization": f"ApiToken {self.pat_token}"}

        credentials = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    def clear_secrets(self) -> None:
        """Clear sensitive credential data."""
        self.password = None
        self.pat_token = None


@dataclass
class UserSession:
    """User session containing DHIS2 credentials and session-only data."""

    session_id: str
    created_at: datetime
    expires_at: datetime
    credentials: Optional[DHIS2Credentials] = None
    user_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    @property
    def is_authenticated(self) -> bool:
        return self.credentials is not None and not self.is_expired


class SessionManager:
    """
    In-memory session manager.

    DEPLOYMENT NOTES:
    - Single Render instance: works as-is
    - Multiple workers/instances: sessions not shared
    - Redeploy: all sessions lost (acceptable for stateless MVP)
    """

    def __init__(self, timeout_minutes: int = 60):
        self._sessions: Dict[str, UserSession] = {}
        self._timeout = timedelta(minutes=timeout_minutes)

    def create_session(self, session: UserSession) -> str:
        """Store a session and return its session ID."""
        self._sessions[session.session_id] = session
        return session.session_id

    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get active session by ID or None if missing/expired."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            self.destroy_session(session_id)
            return None
        return session

    def peek_session(self, session_id: str) -> Optional[UserSession]:
        """Return a session without expiring or mutating it."""
        return self._sessions.get(session_id)

    def refresh_session(self, session_id: str) -> bool:
        """Refresh session expiry if active."""
        session = self.get_session(session_id)
        if not session:
            return False
        session.expires_at = datetime.now(UTC) + self._timeout
        return True

    def destroy_session(self, session_id: str) -> bool:
        """Destroy session and clear credential secrets."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        from app.core.cache import clear_session_cache

        clear_session_cache(session_id)
        if session.credentials:
            session.credentials.clear_secrets()
        del self._sessions[session_id]
        return True

    def cleanup_expired(self) -> int:
        """Remove expired sessions and return count cleaned."""
        expired = [session_id for session_id, session in self._sessions.items() if session.is_expired]
        for session_id in expired:
            self.destroy_session(session_id)
        return len(expired)

    @property
    def active_session_count(self) -> int:
        """Return active session count."""
        return len(self._sessions)


_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """
    Get session manager instance configured from settings.
    Lazy initialization ensures settings are loaded first.
    """
    global _session_manager
    if _session_manager is None:
        from app.core.config import get_settings

        settings = get_settings()
        _session_manager = SessionManager(
            timeout_minutes=settings.session_timeout_minutes
        )
    return _session_manager
