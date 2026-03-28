"""
API tests for Prompt 13 security behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.rate_limit import RateLimitConfig, RateLimitOperation, RateLimiter
from app.auth.roles import resolve_user_role
from app.core.session import UserSession


def build_viewer_session(mock_credentials) -> UserSession:
    """Create a viewer-level session for authorization tests."""
    now = datetime.now(UTC)
    mock_credentials.authorities = []
    session = UserSession(
        session_id="viewer-session",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        credentials=mock_credentials,
    )
    session.user_data["role_info"] = resolve_user_role(
        user_id=mock_credentials.user_id or "viewer-1",
        username=mock_credentials.user_name or "Viewer",
        authorities=[],
        org_units=mock_credentials.org_units,
    )
    session.user_data["csrf_token"] = "viewer-csrf-token"
    return session


@pytest.mark.api
class TestSecurityHeaders:
    def test_login_page_has_security_headers(self, client) -> None:
        response = client.get("/login")

        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in response.headers


@pytest.mark.api
class TestCSRFProtection:
    def test_refresh_requires_csrf_header(self, authenticated_client) -> None:
        response = authenticated_client.post("/auth/refresh")

        assert response.status_code == 403
        assert response.json()["detail"] == "CSRF validation failed"


@pytest.mark.api
class TestPermissionGuards:
    def test_insights_require_ai_permission(
        self,
        client,
        mock_credentials,
        mock_calculator,
        override_dependencies,
    ) -> None:
        viewer_session = build_viewer_session(mock_credentials)
        override_dependencies(session=viewer_session, calculator=mock_calculator)

        response = client.post(
            "/api/insights/indicator",
            json={
                "indicator_id": "VAL-01",
                "org_unit": mock_credentials.org_units[0]["id"],
                "period": "202401",
            },
        )

        assert response.status_code == 403

    def test_exports_require_export_permission(
        self,
        client,
        mock_credentials,
        mock_calculator,
        override_dependencies,
    ) -> None:
        viewer_session = build_viewer_session(mock_credentials)
        override_dependencies(session=viewer_session, calculator=mock_calculator)

        response = client.post(
            "/api/exports/scorecard",
            json={
                "org_unit": mock_credentials.org_units[0]["id"],
                "period": "202401",
                "format": "pdf",
            },
        )

        assert response.status_code == 403


@pytest.mark.api
class TestRateLimiting:
    def test_login_rate_limit_returns_429(self, client, mock_credentials, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.auth.rate_limit._rate_limiter",
            RateLimiter(
                {
                    RateLimitOperation.LOGIN: RateLimitConfig(
                        operation=RateLimitOperation.LOGIN,
                        max_requests=1,
                        window_seconds=900,
                        scope="ip",
                    )
                }
            ),
        )

        with patch(
            "app.api.routes.auth.DHIS2AuthHandler.authenticate_pat",
            new=AsyncMock(return_value=mock_credentials),
        ):
            first = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "pat",
                    "pat_token": "test_pat_token_12345",
                },
            )
            second = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "pat",
                    "pat_token": "test_pat_token_12345",
                },
            )

        assert first.status_code == 200
        assert second.status_code == 429

    def test_general_api_rate_limit_returns_429(
        self,
        authenticated_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.auth.rate_limit._rate_limiter",
            RateLimiter(
                {
                    RateLimitOperation.API_GENERAL: RateLimitConfig(
                        operation=RateLimitOperation.API_GENERAL,
                        max_requests=1,
                        window_seconds=60,
                        scope="session",
                    )
                }
            ),
        )

        first = authenticated_client.get("/api/alerts/thresholds")
        second = authenticated_client.get("/api/alerts/thresholds")

        assert first.status_code == 200
        assert second.status_code == 429
