"""
API tests for HTML page routes.
"""

from __future__ import annotations

import pytest

from app.auth.roles import resolve_user_role


@pytest.mark.api
class TestPublicPages:
    def test_root_redirects_to_login(self, client) -> None:
        response = client.get("/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_root_redirects_to_dashboard_when_authenticated(self, authenticated_client) -> None:
        response = authenticated_client.get("/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"

    def test_login_page_renders(self, client) -> None:
        response = client.get("/login")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "dhis2" in response.text.lower() or "login" in response.text.lower()

    def test_login_page_redirects_when_authenticated(self, authenticated_client) -> None:
        response = authenticated_client.get("/login", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"

    def test_login_page_shows_expired_message(self, client) -> None:
        response = client.get("/login?session_expired=1")

        assert response.status_code == 200
        assert "session" in response.text.lower()


@pytest.mark.api
class TestProtectedPages:
    def test_dashboard_requires_auth(self, client) -> None:
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_dashboard_renders_when_authenticated(self, authenticated_client) -> None:
        response = authenticated_client.get("/dashboard")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Select an organisation unit" in response.text
        assert 'name="annual_population"' in response.text
        assert 'name="period_start"' in response.text
        assert 'name="period_end"' in response.text

    def test_indicators_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/indicators")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Select an organisation unit" in response.text
        assert 'name="period_start"' in response.text
        assert 'name="period_end"' in response.text

    @pytest.mark.parametrize("cascade_type", ["hiv", "hbv", "syphilis"])
    def test_cascade_pages_render(self, authenticated_client, cascade_type: str) -> None:
        response = authenticated_client.get(f"/cascade/{cascade_type}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_cascade_invalid_type_returns_404(self, authenticated_client) -> None:
        response = authenticated_client.get("/cascade/invalid")

        assert response.status_code == 404

    def test_supply_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/supply")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert 'name="periodicity"' in response.text
        assert "Weekly reporting frequency" in response.text

    def test_data_quality_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/data-quality")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Run DQ checks" in response.text

    def test_alerts_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/alerts")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Evaluate alerts" in response.text
        assert "monthly threshold monitoring" in response.text.lower()

    def test_insights_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/insights")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "AI-assisted programme interpretation" in response.text
        assert "Current-session Q&amp;A" in response.text

    def test_trends_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/trends")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Multi-period indicator comparison" in response.text
        assert "SYS-03" not in response.text
        assert 'name="period_start"' in response.text
        assert 'name="period_end"' in response.text

    def test_expired_session_redirects_to_login(self, client, fresh_session_manager, expired_session) -> None:
        fresh_session_manager.create_session(expired_session)
        client.cookies.set("session_id", expired_session.session_id)

        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_data_quality_requires_auth(self, client) -> None:
        response = client.get("/data-quality", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_alerts_requires_auth(self, client) -> None:
        response = client.get("/alerts", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_insights_requires_auth(self, client) -> None:
        response = client.get("/insights", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    def test_insights_redirects_viewer_without_ai_access(
        self,
        client,
        fresh_session_manager,
        valid_session,
    ) -> None:
        valid_session.credentials.authorities = []
        valid_session.user_data["role_info"] = resolve_user_role(
            user_id=valid_session.credentials.user_id or "user123",
            username=valid_session.credentials.user_name or "Test User",
            authorities=[],
            org_units=valid_session.credentials.org_units,
        )
        fresh_session_manager.create_session(valid_session)
        client.cookies.set("session_id", valid_session.session_id)

        response = client.get("/insights", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"


@pytest.mark.api
class TestPageContext:
    def test_dashboard_has_user_name(self, authenticated_client) -> None:
        response = authenticated_client.get("/dashboard")

        assert response.status_code == 200
        assert "Test User" in response.text

    def test_dashboard_has_org_units(self, authenticated_client) -> None:
        response = authenticated_client.get("/dashboard")

        assert response.status_code == 200
        assert "Select an organisation unit" in response.text
        assert "No organisation unit selected yet." in response.text
        assert 'id="dashboard-org-unit-selector-input"' in response.text

    def test_pages_have_periods(self, authenticated_client) -> None:
        response = authenticated_client.get("/indicators")

        assert response.status_code == 200
        assert "January" in response.text or "Week" in response.text or "Q" in response.text

    def test_dashboard_does_not_show_weekly_frequency_control(self, authenticated_client) -> None:
        response = authenticated_client.get("/dashboard")

        assert response.status_code == 200
        assert 'name="periodicity"' not in response.text
