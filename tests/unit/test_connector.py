"""
Unit tests for the DHIS2 connector.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.dhis2_connector import DHIS2Connector, DHIS2NotAuthenticated, PeriodType


@pytest.mark.unit
class TestDHIS2ConnectorInit:
    def test_connector_requires_authenticated_session(self, expired_session) -> None:
        with pytest.raises(DHIS2NotAuthenticated):
            DHIS2Connector(expired_session)

    def test_connector_accepts_authenticated_session(self, valid_session) -> None:
        connector = DHIS2Connector(valid_session)
        assert isinstance(connector, DHIS2Connector)


@pytest.mark.unit
class TestConnectorPublicMethods:
    @pytest.mark.asyncio
    async def test_get_data_values_returns_uid_value_map(self, valid_session) -> None:
        connector = DHIS2Connector(valid_session)
        response = {
            "headers": [
                {"name": "dx"},
                {"name": "pe"},
                {"name": "ou"},
                {"name": "value"},
            ],
            "rows": [
                ["Q9nSogNmKPt", "202401", "ou123", "100"],
                ["uALBQG7TFhq", "202401", "ou123", "95"],
            ],
        }

        with patch.object(connector, "_request_with_retry", AsyncMock(return_value=response)):
            result = await connector.get_data_values(
                data_elements=["Q9nSogNmKPt", "uALBQG7TFhq"],
                org_unit="ou123",
                period="202401",
            )

        assert result == {"Q9nSogNmKPt": 100.0, "uALBQG7TFhq": 95.0}

    @pytest.mark.asyncio
    async def test_get_an21_pos_total_sums_positive_cocs(self, valid_session) -> None:
        connector = DHIS2Connector(valid_session)

        with patch.object(
            connector,
            "get_disaggregated_values",
            AsyncMock(return_value={"H9qJO0yGTKz": 5.0, "BaWI6qkhScq": 10.0}),
        ):
            total = await connector.get_an21_pos_total(org_unit="ou123", period="202401")

        assert total == 15.0

    @pytest.mark.asyncio
    async def test_get_user_org_units_returns_session_org_units(self, valid_session) -> None:
        connector = DHIS2Connector(valid_session)

        org_units = await connector.get_user_org_units()

        assert len(org_units) == 2
        assert org_units[0].uid == "akV6429SUqu"
        assert org_units[0].name == "Test District"

    @pytest.mark.asyncio
    async def test_context_manager_yields_connector(self, valid_session) -> None:
        connector = DHIS2Connector(valid_session)

        async with connector as managed:
            assert managed is connector


@pytest.mark.unit
class TestPeriodHelpers:
    def test_format_monthly_period(self) -> None:
        assert DHIS2Connector.format_period(2024, month=1) == "202401"
        assert DHIS2Connector.format_period(2024, month=12) == "202412"

    def test_format_weekly_period(self) -> None:
        assert DHIS2Connector.format_period(2024, week=5, period_type=PeriodType.WEEKLY) == "2024W05"

    def test_format_quarterly_period(self) -> None:
        assert DHIS2Connector.format_period(2024, month=4, period_type=PeriodType.QUARTERLY) == "2024Q2"

    def test_format_yearly_period(self) -> None:
        assert DHIS2Connector.format_period(2024, period_type=PeriodType.YEARLY) == "2024"

    def test_monthly_period_days(self) -> None:
        assert DHIS2Connector.get_period_days("202401") == 31
        assert DHIS2Connector.get_period_days("202402") == 29
        assert DHIS2Connector.get_period_days("202404") == 30

    def test_weekly_period_days(self) -> None:
        assert DHIS2Connector.get_period_days("2024W05") == 7

    def test_quarterly_period_days(self) -> None:
        assert DHIS2Connector.get_period_days("2024Q1") == 90
