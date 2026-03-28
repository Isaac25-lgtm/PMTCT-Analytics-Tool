"""
Unit tests for Prompt 13 RBAC, audit, and rate-limiting helpers.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.auth.audit import AuditEvent, AuditEventType, AuditLogger, AuditSeverity
from app.auth.permissions import Permission, check_any_permission, check_permission, get_user_permissions
from app.auth.rate_limit import RateLimitConfig, RateLimitOperation, RateLimiter
from app.auth.rbac import PermissionDeniedError, RBACEngine
from app.auth.roles import Role, UserRoleInfo, get_role_from_session, resolve_user_role


@pytest.mark.unit
class TestRoleResolution:
    def test_all_authority_maps_to_admin(self) -> None:
        result = resolve_user_role(
            user_id="admin-1",
            username="admin",
            authorities=["ALL"],
            org_units=[{"id": "ou-1"}],
        )

        assert result.role == Role.ADMIN
        assert result.is_super_admin is True

    def test_export_authority_maps_to_analyst(self) -> None:
        result = resolve_user_role(
            user_id="user-1",
            username="analyst",
            authorities=["F_EXPORT_DATA"],
            org_units=[{"id": "ou-1"}],
        )

        assert result.role == Role.ANALYST

    def test_data_entry_authority_maps_to_data_manager(self) -> None:
        result = resolve_user_role(
            user_id="user-2",
            username="manager",
            authorities=["F_DATAVALUE_ADD"],
            org_units=[{"id": "ou-1"}],
        )

        assert result.role == Role.DATA_MANAGER

    def test_missing_special_authorities_falls_back_to_viewer(self) -> None:
        result = resolve_user_role(
            user_id="user-3",
            username="viewer",
            authorities=["M_dhis-web-dashboard"],
            org_units=[{"id": "ou-1"}],
        )

        assert result.role == Role.VIEWER


@pytest.mark.unit
class TestPermissions:
    def test_viewer_permissions_are_read_only(self) -> None:
        role_info = UserRoleInfo(user_id="v", username="viewer", role=Role.VIEWER)

        assert check_permission(role_info, Permission.VIEW_ALERTS).granted is True
        assert check_permission(role_info, Permission.EXPORT_PDF).granted is False

    def test_any_permission_accepts_first_matching_permission(self) -> None:
        role_info = UserRoleInfo(user_id="a", username="analyst", role=Role.ANALYST)

        result = check_any_permission(role_info, {Permission.SYSTEM_ADMIN, Permission.USE_AI_INSIGHTS})

        assert result.granted is True
        assert result.permission == Permission.USE_AI_INSIGHTS

    def test_super_admin_gets_all_permissions(self) -> None:
        role_info = UserRoleInfo(
            user_id="admin",
            username="admin",
            role=Role.ADMIN,
            is_super_admin=True,
        )

        assert Permission.SYSTEM_ADMIN in get_user_permissions(role_info)
        assert check_permission(role_info, Permission.SYSTEM_ADMIN).granted is True


@pytest.mark.unit
class TestRBACEngine:
    @pytest.fixture
    def session(self):
        role_info = UserRoleInfo(
            user_id="user-1",
            username="analyst",
            role=Role.ANALYST,
            dhis2_authorities={"F_EXPORT_DATA"},
            org_unit_uids={"ou-1", "ou-2"},
        )
        session = Mock()
        session.credentials = Mock()
        session.credentials.user_id = "user-1"
        session.credentials.user_name = "analyst"
        session.credentials.username = "analyst"
        session.credentials.authorities = ["F_EXPORT_DATA"]
        session.credentials.org_units = [{"id": "ou-1"}, {"id": "ou-2"}]
        session.user_data = {"role_info": role_info}
        return session

    def test_engine_reads_role_info_from_session(self, session) -> None:
        engine = RBACEngine(session)

        assert engine.role == Role.ANALYST
        assert engine.has_permission(Permission.USE_AI_INSIGHTS) is True

    def test_authorize_checks_direct_org_unit_access(self, session) -> None:
        engine = RBACEngine(session)

        allowed = engine.authorize(Permission.VIEW_INDICATORS, org_unit_uid="ou-1")
        denied = engine.authorize(Permission.VIEW_INDICATORS, org_unit_uid="ou-x")

        assert allowed.authorized is True
        assert denied.authorized is False
        assert denied.org_unit_allowed is False

    def test_require_raises_for_missing_permission(self, session) -> None:
        engine = RBACEngine(session)

        with pytest.raises(PermissionDeniedError):
            engine.require(Permission.SYSTEM_ADMIN)

    def test_get_role_from_session_derives_when_missing(self) -> None:
        session = Mock()
        session.user_data = {}
        session.credentials = Mock()
        session.credentials.user_id = "user-4"
        session.credentials.user_name = "viewer"
        session.credentials.username = "viewer"
        session.credentials.authorities = []
        session.credentials.org_units = [{"id": "ou-9"}]

        role_info = get_role_from_session(session)

        assert role_info is not None
        assert role_info.role == Role.VIEWER
        assert session.user_data["role_info"].role == Role.VIEWER


@pytest.mark.unit
class TestRateLimiter:
    def test_rate_limiter_blocks_after_limit(self) -> None:
        limiter = RateLimiter(
            {
                RateLimitOperation.AI_INSIGHTS: RateLimitConfig(
                    operation=RateLimitOperation.AI_INSIGHTS,
                    max_requests=2,
                    window_seconds=60,
                    scope="session",
                )
            }
        )

        first = limiter.check(RateLimitOperation.AI_INSIGHTS, session_id="session-1")
        second = limiter.check(RateLimitOperation.AI_INSIGHTS, session_id="session-1")
        third = limiter.check(RateLimitOperation.AI_INSIGHTS, session_id="session-1")

        assert first.allowed is True
        assert second.allowed is True
        assert third.allowed is False

    def test_rate_limiter_keeps_sessions_separate(self) -> None:
        limiter = RateLimiter(
            {
                RateLimitOperation.AI_INSIGHTS: RateLimitConfig(
                    operation=RateLimitOperation.AI_INSIGHTS,
                    max_requests=1,
                    window_seconds=60,
                    scope="session",
                )
            }
        )

        limiter.check(RateLimitOperation.AI_INSIGHTS, session_id="session-1")
        result = limiter.check(RateLimitOperation.AI_INSIGHTS, session_id="session-2")

        assert result.allowed is True


@pytest.mark.unit
class TestAuditLogger:
    def test_audit_event_masks_session_id(self) -> None:
        event = AuditEvent(
            event_type=AuditEventType.LOGIN_SUCCESS,
            severity=AuditSeverity.INFO,
            timestamp=datetime.utcnow(),
            session_id="session-abcdef123456",
        )

        payload = event.to_dict()

        assert payload["session_id"] == "session-..."

    def test_login_failure_hashes_username(self) -> None:
        audit = AuditLogger(enabled=True)

        with patch.object(audit, "_emit") as emit:
            audit.log_login_failure("bad-user", "Invalid credentials", ip_address="127.0.0.1")

        event = emit.call_args[0][0]
        assert event.event_type == AuditEventType.LOGIN_FAILURE
        assert event.details["username_hash"] != "bad-user"
