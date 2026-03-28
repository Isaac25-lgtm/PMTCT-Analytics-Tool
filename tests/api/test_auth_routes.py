"""
API tests for authentication routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.auth.dhis2_auth import DHIS2AuthError


@pytest.mark.api
class TestLoginEndpoint:
    def test_login_with_pat_success(self, client, mock_credentials) -> None:
        with patch(
            "app.api.routes.auth.DHIS2AuthHandler.authenticate_pat",
            new=AsyncMock(return_value=mock_credentials),
        ):
            response = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "pat",
                    "pat_token": "test_pat_token_12345",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user_name"] == "Test User"
        assert data["role"] == "data_manager"
        assert "use_ai_insights" in data["permissions"]
        assert response.cookies.get("session_id")

    def test_login_with_basic_success(self, client, mock_credentials_basic) -> None:
        with patch(
            "app.api.routes.auth.DHIS2AuthHandler.authenticate_basic",
            new=AsyncMock(return_value=mock_credentials_basic),
        ):
            response = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "basic",
                    "username": "testuser",
                    "password": "testpass",
                },
            )

        assert response.status_code == 200
        assert response.json()["user_name"] == "Basic User"

    def test_login_sets_httponly_cookie(self, client, mock_credentials) -> None:
        with patch(
            "app.api.routes.auth.DHIS2AuthHandler.authenticate_pat",
            new=AsyncMock(return_value=mock_credentials),
        ):
            response = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "pat",
                    "pat_token": "test_pat_token_12345",
                },
            )

        assert response.status_code == 200
        assert "httponly" in response.headers.get("set-cookie", "").lower()

    def test_login_invalid_credentials_returns_401(self, client) -> None:
        with patch(
            "app.api.routes.auth.DHIS2AuthHandler.authenticate_pat",
            new=AsyncMock(side_effect=DHIS2AuthError("Invalid credentials")),
        ):
            response = client.post(
                "/auth/login",
                json={
                    "dhis2_url": "https://test.dhis2.org",
                    "auth_method": "pat",
                    "pat_token": "invalid",
                },
            )

        assert response.status_code == 401

    def test_login_missing_fields_returns_422(self, client) -> None:
        response = client.post(
            "/auth/login",
            json={"dhis2_url": "https://test.dhis2.org"},
        )

        assert response.status_code == 422


@pytest.mark.api
class TestLogoutEndpoint:
    def test_logout_clears_session(self, authenticated_client, valid_session) -> None:
        response = authenticated_client.post(
            "/auth/logout",
            data={"csrf_token": valid_session.user_data["csrf_token"]},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

        status_response = authenticated_client.get("/auth/status")
        assert status_response.json()["authenticated"] is False

    def test_logout_without_session_still_succeeds(self, client) -> None:
        response = client.post("/auth/logout")

        assert response.status_code == 200
        assert response.json()["success"] is True


@pytest.mark.api
class TestStatusEndpoint:
    def test_status_authenticated(self, authenticated_client) -> None:
        response = authenticated_client.get("/auth/status")

        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["user_name"] == "Test User"
        assert data["role"] == "data_manager"

    def test_status_unauthenticated(self, client) -> None:
        response = client.get("/auth/status")

        assert response.status_code == 200
        assert response.json()["authenticated"] is False


@pytest.mark.api
class TestRefreshEndpoint:
    def test_refresh_extends_session(self, authenticated_client, valid_session) -> None:
        response = authenticated_client.post(
            "/auth/refresh",
            headers={"X-CSRF-Token": valid_session.user_data["csrf_token"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["expires_at"] is not None

    def test_refresh_without_session_returns_401(self, client) -> None:
        response = client.post("/auth/refresh")

        assert response.status_code == 401
