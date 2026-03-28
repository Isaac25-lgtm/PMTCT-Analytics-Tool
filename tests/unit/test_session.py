"""
Unit tests for session management.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.session import SessionManager, UserSession, get_session_manager


@pytest.mark.unit
class TestSessionManager:
    def test_create_session_returns_session_id(
        self,
        fresh_session_manager: SessionManager,
        valid_session: UserSession,
    ) -> None:
        result = fresh_session_manager.create_session(valid_session)

        assert result == valid_session.session_id
        assert fresh_session_manager.active_session_count == 1

    def test_get_session_returns_session(
        self,
        fresh_session_manager: SessionManager,
        valid_session: UserSession,
    ) -> None:
        fresh_session_manager.create_session(valid_session)

        retrieved = fresh_session_manager.get_session(valid_session.session_id)

        assert retrieved is not None
        assert retrieved.session_id == valid_session.session_id
        assert retrieved.credentials is not None
        assert retrieved.credentials.user_name == valid_session.credentials.user_name

    def test_get_session_returns_none_for_unknown(self, fresh_session_manager: SessionManager) -> None:
        assert fresh_session_manager.get_session("missing-session") is None

    def test_refresh_session_extends_expiry(
        self,
        fresh_session_manager: SessionManager,
        valid_session: UserSession,
    ) -> None:
        fresh_session_manager.create_session(valid_session)
        original_expiry = valid_session.expires_at

        refreshed = fresh_session_manager.refresh_session(valid_session.session_id)
        updated = fresh_session_manager.get_session(valid_session.session_id)

        assert refreshed is True
        assert updated is not None
        assert updated.expires_at > original_expiry

    def test_refresh_session_returns_false_for_unknown(self, fresh_session_manager: SessionManager) -> None:
        assert fresh_session_manager.refresh_session("missing-session") is False

    def test_destroy_session_removes_session(
        self,
        fresh_session_manager: SessionManager,
        valid_session: UserSession,
    ) -> None:
        fresh_session_manager.create_session(valid_session)

        destroyed = fresh_session_manager.destroy_session(valid_session.session_id)

        assert destroyed is True
        assert fresh_session_manager.get_session(valid_session.session_id) is None
        assert fresh_session_manager.active_session_count == 0

    def test_destroy_session_returns_false_for_unknown(self, fresh_session_manager: SessionManager) -> None:
        assert fresh_session_manager.destroy_session("missing-session") is False

    def test_cleanup_expired_removes_only_expired(
        self,
        fresh_session_manager: SessionManager,
        valid_session: UserSession,
        expired_session: UserSession,
    ) -> None:
        fresh_session_manager.create_session(valid_session)
        fresh_session_manager.create_session(expired_session)

        cleaned = fresh_session_manager.cleanup_expired()

        assert cleaned == 1
        assert fresh_session_manager.get_session(valid_session.session_id) is not None
        assert fresh_session_manager.get_session(expired_session.session_id) is None

    def test_active_session_count_tracks_current_sessions(
        self,
        fresh_session_manager: SessionManager,
        mock_credentials,
    ) -> None:
        now = datetime.now(UTC)
        for index in range(3):
            fresh_session_manager.create_session(
                UserSession(
                    session_id=f"session-{index}",
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                    credentials=mock_credentials,
                )
            )

        assert fresh_session_manager.active_session_count == 3


@pytest.mark.unit
class TestUserSession:
    def test_is_authenticated_true_with_active_credentials(self, valid_session: UserSession) -> None:
        assert valid_session.is_authenticated is True

    def test_is_authenticated_false_without_credentials(self) -> None:
        now = datetime.now(UTC)
        session = UserSession(
            session_id="anonymous",
            created_at=now,
            expires_at=now + timedelta(hours=1),
            credentials=None,
        )

        assert session.is_authenticated is False

    def test_is_expired_false_for_valid_session(self, valid_session: UserSession) -> None:
        assert valid_session.is_expired is False

    def test_is_expired_true_for_expired_session(self, expired_session: UserSession) -> None:
        assert expired_session.is_expired is True


@pytest.mark.unit
class TestGetSessionManager:
    def test_get_session_manager_returns_singleton(self) -> None:
        manager_one = get_session_manager()
        manager_two = get_session_manager()

        assert manager_one is manager_two
