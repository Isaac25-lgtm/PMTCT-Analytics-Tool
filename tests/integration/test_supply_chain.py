"""Integration tests for the enriched supply report flow."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestSupplyIntegration:
    def test_supply_status_json(self, integration_client) -> None:
        response = integration_client.post(
            "/api/reports/supply-status",
            json={"org_unit": "OU_FACILITY_1", "period": "202401"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "commodities" in data
        assert "enriched_commodities" in data
        assert "unmapped_commodities" in data
        assert any(item["commodity"] == "HBsAg Test Kits" for item in data["commodities"])

    def test_supply_status_htmx(self, integration_client) -> None:
        response = integration_client.post(
            "/api/reports/supply-status",
            data={"org_unit": "OU_FACILITY_1", "period": "202401"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "HBsAg Test Kits" in response.text
        assert "Pending DHIS2 mapping" in response.text
