"""
API tests for report routes.
"""

from __future__ import annotations

import pytest


@pytest.mark.api
class TestScorecardEndpoint:
    def test_scorecard_returns_json(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/scorecard",
            json={"org_unit": "akV6429SUqu", "period": "202401"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_scorecard_returns_html_for_htmx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/scorecard",
            data={"org_unit": "akV6429SUqu", "period": "202401"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_scorecard_requires_auth(self, client) -> None:
        response = client.post(
            "/api/reports/scorecard",
            json={"org_unit": "akV6429SUqu", "period": "202401"},
        )

        assert response.status_code == 401


@pytest.mark.api
class TestCascadeEndpoint:
    @pytest.mark.parametrize("cascade_type", ["hiv", "hbv", "syphilis"])
    def test_cascade_valid_types(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        cascade_type: str,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/cascade",
            json={
                "org_unit": "akV6429SUqu",
                "period": "202401",
                "cascade_type": cascade_type,
            },
        )

        assert response.status_code == 200

    def test_cascade_invalid_type_returns_400(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/cascade",
            json={
                "org_unit": "akV6429SUqu",
                "period": "202401",
                "cascade_type": "invalid",
            },
        )

        assert response.status_code == 400

    def test_cascade_returns_html_for_htmx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/cascade",
            data={"org_unit": "akV6429SUqu", "period": "202401", "cascade_type": "hiv"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.api
class TestSupplyEndpoint:
    def test_supply_returns_data(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/supply-status",
            json={"org_unit": "akV6429SUqu", "period": "202401"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_supply_returns_html_for_htmx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/supply-status",
            data={"org_unit": "akV6429SUqu", "period": "202401"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.api
class TestReportHelpers:
    def test_org_units_returns_session_org_units(
        self,
        client,
        valid_session,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session)

        response = client.get("/api/reports/org-units")

        assert response.status_code == 200
        assert response.json()["org_units"][0]["id"] == "akV6429SUqu"

    def test_periods_returns_json(self, client) -> None:
        response = client.get("/api/reports/periods?periodicity=monthly&count=3")

        assert response.status_code == 200
        data = response.json()
        assert len(data["periods"]) == 3
        assert all(len(period["id"]) == 6 for period in data["periods"])

    def test_periods_returns_html_for_htmx(self, client) -> None:
        response = client.get(
            "/api/reports/periods?periodicity=weekly&history_depth=3m",
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<option" in response.text
