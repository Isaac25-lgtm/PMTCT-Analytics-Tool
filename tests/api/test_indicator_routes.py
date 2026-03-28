"""
API tests for indicator routes.
"""

from __future__ import annotations

import pytest


@pytest.mark.api
class TestIndicatorListEndpoint:
    def test_list_indicators_is_public(self, client, override_dependencies, loaded_registry) -> None:
        override_dependencies(registry=loaded_registry)

        response = client.get("/api/indicators/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert "indicators" in data

    def test_list_indicators_can_filter_by_category(
        self,
        client,
        override_dependencies,
        loaded_registry,
    ) -> None:
        override_dependencies(registry=loaded_registry)

        response = client.get("/api/indicators/?category=who_validation")

        assert response.status_code == 200
        assert all(item["category"] == "who_validation" for item in response.json()["indicators"])


@pytest.mark.api
class TestCategoriesEndpoint:
    def test_get_categories(self, client) -> None:
        response = client.get("/api/indicators/categories")

        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        category_ids = {category["id"] for category in data["categories"]}
        assert "who_validation" in category_ids
        assert "hiv_cascade" in category_ids


@pytest.mark.api
class TestSingleIndicatorEndpoint:
    def test_get_known_indicator(
        self,
        client,
        override_dependencies,
        loaded_registry,
    ) -> None:
        override_dependencies(registry=loaded_registry)

        response = client.get("/api/indicators/VAL-02")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "VAL-02"
        assert data["name"] == "HIV Testing Coverage at ANC"

    def test_get_unknown_indicator_returns_404(
        self,
        client,
        override_dependencies,
        loaded_registry,
    ) -> None:
        override_dependencies(registry=loaded_registry)

        response = client.get("/api/indicators/UNKNOWN")

        assert response.status_code == 404


@pytest.mark.api
class TestCalculateEndpoint:
    def test_calculate_all_indicators(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/indicators/calculate",
            json={
                "org_unit": "akV6429SUqu",
                "period": "202401",
                "org_unit_name": "Test District",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["org_unit_uid"] == "akV6429SUqu"
        assert "results" in data

    def test_calculate_with_category_filter(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/indicators/calculate",
            json={
                "org_unit": "akV6429SUqu",
                "period": "202401",
                "categories": ["who_validation"],
            },
        )

        assert response.status_code == 200

    def test_calculate_returns_html_for_htmx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/indicators/calculate",
            data={"org_unit": "akV6429SUqu", "period": "202401"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_calculate_missing_required_fields_returns_422(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/indicators/calculate",
            json={"period": "202401"},
        )

        assert response.status_code == 422

    def test_calculate_requires_auth(self, client) -> None:
        response = client.post(
            "/api/indicators/calculate",
            json={"org_unit": "akV6429SUqu", "period": "202401"},
        )

        assert response.status_code == 401
