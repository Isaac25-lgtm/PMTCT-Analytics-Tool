"""API tests for admin routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.api
class TestAdminDashboard:
    def test_admin_page_requires_auth(self, client) -> None:
        response = client.get("/admin", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_admin_page_redirects_non_admin(self, authenticated_client) -> None:
        response = authenticated_client.get("/admin", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"

    def test_admin_page_renders_for_admin(self, admin_authenticated_client) -> None:
        with patch(
            "app.api.routes.admin.SystemDiagnostics.get_system_status",
            new=AsyncMock(return_value={
                "application": {"environment": "test", "version": "1.0.0"},
                "runtime": {"python_version": "3.11", "platform": "test"},
                "memory": {"available": False, "reason": "test"},
                "cache": {
                    "application": {"stats": {"total_entries": 0, "hit_rate": 0.0}},
                    "session_store": {"stats": {"total_entries": 0, "hit_rate": 0.0}},
                },
                "sessions": {"active_sessions": 1},
                "configuration": {"dhis2_configured": True},
            }),
        ), patch(
            "app.api.routes.admin.SystemDiagnostics.check_dhis2_connectivity",
            new=AsyncMock(return_value={"status": "reachable_auth_required"}),
        ):
            response = admin_authenticated_client.get("/admin")

        assert response.status_code == 200
        assert "Admin dashboard" in response.text


@pytest.mark.api
class TestAdminJsonRoutes:
    def test_status_requires_admin(self, authenticated_client) -> None:
        response = authenticated_client.get("/admin/status")

        assert response.status_code == 403

    def test_status_returns_payload_for_admin(self, admin_authenticated_client) -> None:
        with patch(
            "app.api.routes.admin.SystemDiagnostics.get_system_status",
            new=AsyncMock(return_value={"application": {"environment": "test"}}),
        ), patch(
            "app.api.routes.admin.SystemDiagnostics.check_dhis2_connectivity",
            new=AsyncMock(return_value={"status": "connected"}),
        ):
            response = admin_authenticated_client.get("/admin/status")

        assert response.status_code == 200
        assert response.json()["dhis2"]["status"] == "connected"

    def test_config_validation_returns_summary(self, admin_authenticated_client) -> None:
        response = admin_authenticated_client.get("/admin/config/validate")

        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "results" in data

    def test_cache_details_returns_both_cache_views(self, admin_authenticated_client) -> None:
        response = admin_authenticated_client.get("/admin/cache")

        assert response.status_code == 200
        data = response.json()
        assert "application" in data
        assert "session_store" in data

    def test_session_listing_returns_current_session(self, admin_authenticated_client) -> None:
        response = admin_authenticated_client.get("/admin/sessions")

        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] >= 1
        assert any(item["is_current"] for item in data["sessions"])

    def test_cannot_terminate_current_session(self, admin_authenticated_client, admin_session) -> None:
        response = admin_authenticated_client.post(f"/admin/sessions/{admin_session.session_id}/terminate")

        assert response.status_code == 400

    def test_clear_all_caches(self, admin_authenticated_client) -> None:
        response = admin_authenticated_client.post("/admin/cache/clear")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Cleared all caches"
