"""
API tests for Prompt 16 supply routes.

Verifies backward compatibility of existing supply endpoints and enriched
JSON/HTMX rendering.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_build_cached_connector(*args, **kwargs):
    """Return a mock connector that yields from async with."""
    mock_connector = AsyncMock()
    mock_connector.get_data_values = AsyncMock(return_value={})
    mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
    mock_connector.__aexit__ = AsyncMock(return_value=False)
    return mock_connector


def _build_supply_calculator(supply_result_set):
    """Return a calculator mock that serves real supply indicators."""
    calculator = MagicMock()
    calculator.calculate_all = AsyncMock(return_value=supply_result_set)
    calculator.calculate_single = AsyncMock(return_value=None)
    calculator.set_expected_pregnancies = MagicMock()
    return calculator


@pytest.mark.api
class TestSupplyStatusRoute:
    """Tests for POST /api/reports/supply-status."""

    def _post(self, client, path, *, json=None, data=None, headers=None):
        """POST with connector patched so raw-value fetches don't hit DHIS2."""
        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ):
            if json is not None:
                return client.post(path, json=json, headers=headers)
            return client.post(path, data=data, headers=headers)

    def test_supply_json_returns_legacy_shape(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            json={"org_unit": "ou123", "period": "202401"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "commodities" in data
        for commodity in data["commodities"]:
            assert "commodity" in commodity
            assert "consumed" in commodity
            assert "stockout_days" in commodity
            assert "stock_on_hand" in commodity
            assert "days_of_use" in commodity
            assert "status" in commodity

    def test_supply_json_has_enriched_fields(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            json={"org_unit": "ou123", "period": "202401"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "enriched_commodities" in data
        assert "unmapped_commodities" in data
        assert "alerts" in data
        assert "validation" in data
        assert "forecasts" in data

    def test_supply_json_enriched_lists_keep_commodity_identity(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            json={"org_unit": "ou123", "period": "202401"},
        )
        data = response.json()
        assert data["enriched_commodities"]
        assert data["alerts"]
        assert data["forecasts"]
        assert all("commodity" in row for row in data["enriched_commodities"])
        assert all("commodity_id" in row for row in data["alerts"])
        assert all("commodity" in row for row in data["alerts"])
        assert all("commodity_id" in row for row in data["forecasts"])

    def test_supply_json_still_has_two_mapped_commodities(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            json={"org_unit": "ou123", "period": "202401"},
        )
        data = response.json()
        assert len(data["commodities"]) == 2
        names = {c["commodity"] for c in data["commodities"]}
        assert "HBsAg Test Kits" in names
        assert "HIV/Syphilis Duo Test Kits" in names

    def test_supply_json_unmapped_commodities_present(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            json={"org_unit": "ou123", "period": "202401"},
        )
        data = response.json()
        unmapped = data.get("unmapped_commodities", [])
        assert len(unmapped) >= 4
        names = {c["name"] for c in unmapped}
        assert "Benzathine Penicillin G 2.4MU" in names
        assert all(c["mapping_status"] == "mapping_pending" for c in unmapped)

    def test_supply_htmx_returns_html(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            data={"org_unit": "ou123", "period": "202401"},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Supply chain status" in response.text

    def test_supply_htmx_shows_legacy_fields(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            data={"org_unit": "ou123", "period": "202401"},
            headers={"HX-Request": "true"},
        )
        text = response.text
        assert "Stock on hand" in text
        assert "Days of use" in text
        assert "Consumed" in text
        assert "Stockout days" in text

    def test_supply_htmx_shows_unmapped_section(
        self, client, valid_session, supply_result_set, override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=_build_supply_calculator(supply_result_set),
        )
        response = self._post(
            client, "/api/reports/supply-status",
            data={"org_unit": "ou123", "period": "202401"},
            headers={"HX-Request": "true"},
        )
        assert "mapping pending" in response.text.lower() or "Pending DHIS2 mapping" in response.text


class TestSupplyExportRoute:
    def test_supply_export_requires_auth(self, client) -> None:
        response = client.post(
            "/api/exports/supply",
            json={"org_unit": "ou123", "period": "202401", "format": "csv"},
        )
        assert response.status_code == 401


class TestSupplyPageRoute:
    def test_supply_page_renders(self, authenticated_client) -> None:
        response = authenticated_client.get("/supply")
        assert response.status_code == 200
        assert "Supply chain status" in response.text

    def test_supply_page_requires_auth(self, client) -> None:
        response = client.get("/supply", follow_redirects=False)
        assert response.status_code == 302
