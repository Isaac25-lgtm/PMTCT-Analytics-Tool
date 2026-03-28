"""Integration tests for live indicator and trend flows against mock DHIS2."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestIndicatorCalculationIntegration:
    def test_calculate_indicators(self, integration_client) -> None:
        response = integration_client.post(
            "/api/indicators/calculate",
            json={
                "org_unit": "OU_FACILITY_1",
                "period": "202401",
                "categories": ["who_validation"],
                "expected_pregnancies": 200,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_indicators"] >= 1
        assert any(item["indicator_id"] == "VAL-02" for item in data["results"])

    def test_calculate_single_indicator(self, integration_client) -> None:
        response = integration_client.get(
            "/api/indicators/calculate/VAL-02",
            params={"org_unit": "OU_FACILITY_1", "period": "202401"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["indicator_id"] == "VAL-02"
        assert data["result_value"] is not None

    def test_trend_analysis(self, integration_client) -> None:
        response = integration_client.post(
            "/api/trends/analyze",
            json={
                "indicator_ids": ["VAL-02"],
                "org_unit": "OU_FACILITY_1",
                "end_period": "202403",
                "num_periods": 3,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["periods"] == ["202403", "202402", "202401"]
        assert len(data["trends"]) == 1
