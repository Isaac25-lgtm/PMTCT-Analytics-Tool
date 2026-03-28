"""
API tests for report routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _mock_build_cached_connector(*args, **kwargs):
    connector = AsyncMock()
    connector.get_data_values = AsyncMock(return_value={})
    connector.__aenter__ = AsyncMock(return_value=connector)
    connector.__aexit__ = AsyncMock(return_value=False)
    return connector


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
        assert "Numerator maths" in response.text
        assert "AN17a = 950 = 950" in response.text
        assert "AN01a = 1,000 = 1,000" in response.text
        assert "(950 / 1,000) x 100 = 95%" in response.text

    def test_scorecard_accepts_period_range_and_derives_expected_pregnancies(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/reports/scorecard",
            json={
                "org_unit": "akV6429SUqu",
                "period_start": "202401",
                "period_end": "202403",
                "annual_population": 100000,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "202403"
        assert data["period_start"] == "202401"
        assert data["period_end"] == "202403"
        assert data["period_label"] == "Jan 2024 to Mar 2024"
        mock_calculator.set_expected_pregnancies.assert_called_with("akV6429SUqu", 1250)

    def test_scorecard_comparison_mode_returns_comparison_markup(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.reports.resolve_comparison_units",
            AsyncMock(
                return_value=[
                    {"uid": "fac-1", "name": "Facility One", "level": 5},
                    {"uid": "fac-2", "name": "Facility Two", "level": 5},
                ]
            ),
        ):
            response = client.post(
                "/api/reports/scorecard",
                data={
                    "org_unit": "akV6429SUqu",
                    "period_start": "202401",
                    "period_end": "202403",
                    "comparison_mode": "district_facilities",
                    "population_fac-1": "20000",
                },
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert "Comparison view" in response.text
        assert "population_fac-1" in response.text
        assert "Facility One" in response.text

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

        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ):
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

        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ):
            response = client.post(
                "/api/reports/supply-status",
                data={"org_unit": "akV6429SUqu", "period": "202401"},
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_supply_accepts_period_end_fallback(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ):
            response = client.post(
                "/api/reports/supply-status",
                json={"org_unit": "akV6429SUqu", "period_end": "202401", "periodicity": "weekly"},
            )

        assert response.status_code == 200


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
