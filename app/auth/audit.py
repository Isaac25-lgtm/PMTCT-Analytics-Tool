"""
Structured audit logging for security-sensitive operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from hashlib import sha256
import json
import logging
from logging import FileHandler
from pathlib import Path
from typing import Any, Optional

from app.auth.roles import load_rbac_config
from app.core.config import get_settings

logger = logging.getLogger("audit")


class AuditEventType(str, Enum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_EXPIRED = "session_expired"
    PERMISSION_DENIED = "permission_denied"
    ORG_UNIT_ACCESS_DENIED = "org_unit_access_denied"
    EXPORT_PDF = "export_pdf"
    EXPORT_EXCEL = "export_excel"
    EXPORT_CSV = "export_csv"
    AI_INSIGHT_REQUEST = "ai_insight_request"
    AI_INSIGHT_ERROR = "ai_insight_error"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    CACHE_CLEARED = "cache_cleared"
    SESSION_TERMINATED = "session_terminated"
    CONFIG_VALIDATED = "config_validated"


class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """A JSON-serialisable audit record."""

    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: datetime
    user_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    org_unit_uid: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
    details: Optional[dict[str, Any]] = None

    def _mask_session_id(self) -> Optional[str]:
        if not self.session_id:
            return None
        return f"{self.session_id[:8]}..."

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.user_id,
            "username": self.username,
            "session_id": self._mask_session_id(),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "org_unit_uid": self.org_unit_uid,
            "success": self.success,
            "error_message": self.error_message,
            "details": self.details,
        }
        return {key: value for key, value in payload.items() if value is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class AuditLogger:
    """Emit structured audit events through the configured Python logger."""

    def __init__(self, enabled: bool = True, audit_log_file: Optional[str] = None):
        self.enabled = enabled
        self._logger = logger
        if audit_log_file:
            self._ensure_file_handler(audit_log_file)

    def _ensure_file_handler(self, audit_log_file: str) -> None:
        log_path = Path(audit_log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        for handler in self._logger.handlers:
            if isinstance(handler, FileHandler) and Path(handler.baseFilename).resolve() == log_path.resolve():
                return
        file_handler = FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(file_handler)
        self._logger.setLevel(logging.INFO)

    def _emit(self, event: AuditEvent) -> None:
        if not self.enabled:
            return
        method = {
            AuditSeverity.DEBUG: self._logger.debug,
            AuditSeverity.INFO: self._logger.info,
            AuditSeverity.WARNING: self._logger.warning,
            AuditSeverity.ERROR: self._logger.error,
            AuditSeverity.CRITICAL: self._logger.critical,
        }[event.severity]
        method(event.to_json())

    def _event(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        **kwargs: Any,
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            severity=severity,
            timestamp=datetime.now(timezone.utc),
            **kwargs,
        )

    def log_login_success(
        self,
        user_id: str,
        username: str,
        org_units: list[dict[str, Any]],
        ip_address: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.LOGIN_SUCCESS,
                AuditSeverity.INFO,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                session_id=session_id,
                details={
                    "org_unit_count": len(org_units),
                    "org_units": [
                        org_unit.get("id") or org_unit.get("uid")
                        for org_unit in org_units[:5]
                        if org_unit
                    ],
                },
            )
        )

    def log_login_failure(
        self,
        username: str,
        reason: str,
        ip_address: Optional[str] = None,
    ) -> None:
        username_hash = sha256(username.encode("utf-8")).hexdigest()[:16]
        self._emit(
            self._event(
                AuditEventType.LOGIN_FAILURE,
                AuditSeverity.WARNING,
                ip_address=ip_address,
                success=False,
                error_message=reason,
                details={"username_hash": username_hash},
            )
        )

    def log_logout(
        self,
        user_id: str,
        username: str,
        session_id: Optional[str] = None,
        session_duration_seconds: Optional[int] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.LOGOUT,
                AuditSeverity.INFO,
                user_id=user_id,
                username=username,
                session_id=session_id,
                details={"session_duration_seconds": session_duration_seconds},
            )
        )

    def log_session_expired(
        self,
        user_id: str,
        username: str,
        session_id: Optional[str] = None,
        session_duration_seconds: Optional[int] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.SESSION_EXPIRED,
                AuditSeverity.INFO,
                user_id=user_id,
                username=username,
                session_id=session_id,
                details={"session_duration_seconds": session_duration_seconds},
            )
        )

    def log_permission_denied(
        self,
        user_id: str,
        username: str,
        permission: str,
        role: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.PERMISSION_DENIED,
                AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                resource_type=resource_type,
                resource_id=resource_id,
                success=False,
                details={"permission": permission, "role": role},
            )
        )

    def log_org_unit_access_denied(
        self,
        user_id: str,
        username: str,
        org_unit_uid: str,
        ip_address: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.ORG_UNIT_ACCESS_DENIED,
                AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                org_unit_uid=org_unit_uid,
                success=False,
            )
        )

    def log_export(
        self,
        export_type: str,
        user_id: str,
        username: str,
        org_unit_uid: Optional[str] = None,
        period: Optional[str] = None,
        indicators: Optional[list[str]] = None,
    ) -> None:
        event_type = {
            "pdf": AuditEventType.EXPORT_PDF,
            "xlsx": AuditEventType.EXPORT_EXCEL,
            "excel": AuditEventType.EXPORT_EXCEL,
            "csv": AuditEventType.EXPORT_CSV,
        }.get(export_type.lower(), AuditEventType.EXPORT_EXCEL)
        self._emit(
            self._event(
                event_type,
                AuditSeverity.INFO,
                user_id=user_id,
                username=username,
                org_unit_uid=org_unit_uid,
                resource_type="export",
                resource_id=export_type.lower(),
                details={
                    "period": period,
                    "indicator_count": len(indicators) if indicators else None,
                },
            )
        )

    def log_ai_insight(
        self,
        insight_type: str,
        user_id: str,
        username: str,
        org_unit_uid: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.AI_INSIGHT_REQUEST if success else AuditEventType.AI_INSIGHT_ERROR,
                AuditSeverity.INFO if success else AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                org_unit_uid=org_unit_uid,
                resource_type="ai_insight",
                resource_id=insight_type,
                success=success,
                error_message=error_message,
            )
        )

    def log_rate_limit_exceeded(
        self,
        user_id: str,
        username: str,
        operation: str,
        current_count: int,
        limit: int,
        window_seconds: int,
        ip_address: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.RATE_LIMIT_EXCEEDED,
                AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                resource_type="rate_limit",
                resource_id=operation,
                success=False,
                details={
                    "current_count": current_count,
                    "limit": limit,
                    "window_seconds": window_seconds,
                },
            )
        )

    def log_cache_cleared(
        self,
        user_id: str,
        username: str,
        *,
        scope: str,
        cleared_count: int,
        namespace: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.CACHE_CLEARED,
                AuditSeverity.INFO,
                user_id=user_id,
                username=username,
                resource_type="cache",
                resource_id=scope,
                details={
                    "namespace": namespace,
                    "cleared_count": cleared_count,
                },
            )
        )

    def log_session_terminated(
        self,
        user_id: str,
        username: str,
        *,
        terminated_session_id: str,
        terminated_username: Optional[str] = None,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.SESSION_TERMINATED,
                AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                resource_type="session",
                resource_id=terminated_session_id[:8],
                details={
                    "terminated_session_id": f"{terminated_session_id[:8]}...",
                    "terminated_username": terminated_username,
                },
            )
        )

    def log_config_validated(
        self,
        user_id: str,
        username: str,
        *,
        valid: bool,
        files_checked: int,
        error_count: int,
        warning_count: int,
    ) -> None:
        self._emit(
            self._event(
                AuditEventType.CONFIG_VALIDATED,
                AuditSeverity.INFO if valid else AuditSeverity.WARNING,
                user_id=user_id,
                username=username,
                resource_type="config",
                resource_id="all",
                success=valid,
                details={
                    "files_checked": files_checked,
                    "error_count": error_count,
                    "warning_count": warning_count,
                },
            )
        )


@lru_cache
def get_audit_logger() -> AuditLogger:
    """Return the configured singleton audit logger."""
    settings = get_settings()
    audit_config = load_rbac_config().get("audit", {})
    enabled = bool(settings.audit_enabled and audit_config.get("enabled", True))
    return AuditLogger(enabled=enabled, audit_log_file=settings.audit_log_file)
