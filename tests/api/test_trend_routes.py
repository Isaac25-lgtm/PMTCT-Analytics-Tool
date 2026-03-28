"""
API tests for trend routes added in Prompt 7.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.indicators.models import IndicatorCategory, ResultType
from app.services.trends import PeriodValue, TrendDirection, TrendService
from tests.conftest import build_result


@pytest.mark.api
class TestTrendAnalyzeEndpoint:
    def test_invalid_end_period_is_rejected(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        response = client.post(
            "/api/trends/analyze",
            json={
                "indicator_ids": ["VAL-02"],
                "org_unit": "akV6429SUqu",
                "end_period": "202413",
                "num_periods": 3,
            },
        )

        assert response.status_code == 422

    def test_weekly_indicator_is_rejected(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        response = client.post(
            "/api/trends/analyze",
            json={
                "indicator_ids": ["SYS-03"],
                "org_unit": "akV6429SUqu",
                "end_period": "202403",
                "num_periods": 3,
            },
        )

        assert response.status_code == 422
        assert "Weekly indicators" in str(response.json()["detail"])

    def test_json_response_uses_registry_result_type(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        mock_calculator.calculate_single = AsyncMock(
            return_value=build_result(
                "SUP-05",
                "HBsAg Days of Use",
                IndicatorCategory.SUPPLY,
                result_value=42.0,
                numerator_value=120.0,
                denominator_value=3.0,
                result_type=ResultType.PERCENTAGE,
            )
        )
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        response = client.post(
            "/api/trends/analyze",
            json={
                "indicator_ids": ["SUP-05"],
                "org_unit": "akV6429SUqu",
                "end_period": "202403",
                "num_periods": 3,
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert data["periods"] == ["202401", "202402", "202403"]
        assert data["trends"][0]["result_type"] == "days"

    def test_mixed_valid_and_invalid_period_results_are_supported(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        async def calculate_single_side_effect(
            indicator_id: str,
            org_unit: str,
            period: str,
            org_unit_name: str | None = None,
            include_children: bool = False,
        ):
            if period == "202402":
                return build_result(
                    indicator_id,
                    "HIV Testing Coverage at ANC",
                    IndicatorCategory.WHO_VALIDATION,
                    period=period,
                    result_value=None,
                    numerator_value=None,
                    denominator_value=None,
                    target=95.0,
                    is_valid=False,
                    error_message="Missing data",
                )

            values = {"202401": 80.0, "202403": 92.0}
            return build_result(
                indicator_id,
                "HIV Testing Coverage at ANC",
                IndicatorCategory.WHO_VALIDATION,
                period=period,
                result_value=values[period],
                numerator_value=values[period] * 10,
                denominator_value=1000.0,
                target=95.0,
                is_valid=True,
            )

        mock_calculator.calculate_single = AsyncMock(side_effect=calculate_single_side_effect)
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        response = client.post(
            "/api/trends/analyze",
            json={
                "indicator_ids": ["VAL-02"],
                "org_unit": "akV6429SUqu",
                "end_period": "202403",
                "num_periods": 3,
            },
        )

        assert response.status_code == 200
        summary = response.json()["trends"][0]["summary"]
        assert summary["valid_periods"] == 2
        assert summary["total_periods"] == 3
        assert summary["start_value"] == 80.0
        assert summary["end_value"] == 92.0

    def test_htmx_returns_html_partial(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        async def calculate_single_side_effect(
            indicator_id: str,
            org_unit: str,
            period: str,
            org_unit_name: str | None = None,
            include_children: bool = False,
        ):
            values = {"202401": 83.0, "202402": 88.5, "202403": 91.0}
            return build_result(
                indicator_id,
                "HIV Testing Coverage at ANC",
                IndicatorCategory.WHO_VALIDATION,
                period=period,
                result_value=values[period],
                numerator_value=values[period] * 10,
                denominator_value=1000.0,
                target=95.0,
                is_valid=True,
            )

        mock_calculator.calculate_single = AsyncMock(side_effect=calculate_single_side_effect)
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        response = client.post(
            "/api/trends/analyze",
            data=[
                ("indicator_ids", "VAL-02"),
                ("org_unit", "akV6429SUqu"),
                ("end_period", "202403"),
                ("num_periods", "3"),
            ],
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Trend analysis results" in response.text

    def test_htmx_warm_cache_still_renders_partial(
        self,
        client,
        valid_session,
        mock_calculator,
        loaded_registry,
        override_dependencies,
    ) -> None:
        async def calculate_single_side_effect(
            indicator_id: str,
            org_unit: str,
            period: str,
            org_unit_name: str | None = None,
            include_children: bool = False,
        ):
            values = {"202401": 83.0, "202402": 88.5, "202403": 91.0}
            return build_result(
                indicator_id,
                "HIV Testing Coverage at ANC",
                IndicatorCategory.WHO_VALIDATION,
                period=period,
                result_value=values[period],
                numerator_value=values[period] * 10,
                denominator_value=1000.0,
                target=95.0,
                is_valid=True,
            )

        mock_calculator.calculate_single = AsyncMock(side_effect=calculate_single_side_effect)
        override_dependencies(
            session=valid_session,
            calculator=mock_calculator,
            registry=loaded_registry,
        )

        request_kwargs = {
            "data": [
                ("indicator_ids", "VAL-02"),
                ("org_unit", "akV6429SUqu"),
                ("end_period", "202403"),
                ("num_periods", "3"),
            ],
            "headers": {"HX-Request": "true"},
        }

        first = client.post("/api/trends/analyze", **request_kwargs)
        second = client.post("/api/trends/analyze", **request_kwargs)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.headers["content-type"].startswith("text/html")
        assert "Trend analysis results" in second.text
        assert mock_calculator.calculate_single.call_count == 3


@pytest.mark.api
class TestTrendPeriodsEndpoint:
    def test_periods_are_bounded(self, client) -> None:
        response = client.get("/api/trends/periods?count=13")

        assert response.status_code == 422

    def test_periods_return_most_recent_first(self, client) -> None:
        response = client.get("/api/trends/periods?count=3")

        assert response.status_code == 200
        data = response.json()
        assert len(data["periods"]) == 3
        ids = [item["id"] for item in data["periods"]]
        assert ids == sorted(ids, reverse=True)


@pytest.mark.unit
class TestTrendService:
    def test_generate_monthly_periods_oldest_first(self) -> None:
        periods = TrendService.generate_monthly_periods("202404", 4)

        assert periods == ["202401", "202402", "202403", "202404"]

    def test_monthly_period_options_are_most_recent_first(self) -> None:
        periods = TrendService.build_monthly_period_options(count=4, today=date(2024, 4, 15))

        assert [period["id"] for period in periods] == ["202404", "202403", "202402", "202401"]

    def test_calculate_trend_summary_ignores_invalid_periods(self) -> None:
        service = TrendService()
        summary = service.calculate_trend_summary(
            [
                PeriodValue("202401", "Jan 2024", 80.0, 800.0, 1000.0, True),
                PeriodValue("202402", "Feb 2024", None, None, None, False),
                PeriodValue("202403", "Mar 2024", 90.0, 900.0, 1000.0, True),
            ],
            target=95.0,
        )

        assert summary.valid_periods == 2
        assert summary.total_periods == 3
        assert summary.start_value == 80.0
        assert summary.end_value == 90.0
        assert summary.direction == TrendDirection.UP
