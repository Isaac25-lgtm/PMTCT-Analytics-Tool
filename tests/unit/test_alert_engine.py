"""
Unit tests for Prompt 10 alert evaluation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.indicators.models import IndicatorCategory, IndicatorResultSet, ResultType
from app.services.alert_engine import AlertEngine, AlertResult, AlertThresholdLoader
from app.services.alert_rules import Alert, AlertCategory, AlertSeverity, AlertType
from app.services.data_quality import DQResult, DQResultSummary
from tests.conftest import TEST_ORG_UNIT, TEST_ORG_UNIT_NAME, TEST_PERIOD, build_result


def build_result_set(*results) -> IndicatorResultSet:
    """Build a compact result set for alert tests."""
    result_set = IndicatorResultSet(
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=TEST_PERIOD,
    )
    for result in results:
        result_set.add_result(result)
    return result_set


@pytest.mark.unit
class TestAlertEngine:
    def test_monthly_period_validation(self) -> None:
        assert AlertEngine._is_monthly_period("202401") is True
        assert AlertEngine._is_monthly_period("202413") is False
        assert AlertEngine._is_monthly_period("2024W05") is False

    @pytest.mark.asyncio
    async def test_supply_stockout_uses_real_days_of_use_indicator_fields(
        self,
        mock_calculator,
    ) -> None:
        mock_calculator.calculate_all = AsyncMock(
            return_value=build_result_set(
                build_result(
                    "SUP-05",
                    "HBsAg Days of Use",
                    IndicatorCategory.SUPPLY,
                    result_value=0.0,
                    numerator_value=0.0,
                    denominator_value=1.0,
                    result_type=ResultType.DAYS,
                )
            )
        )
        engine = AlertEngine(calculator=mock_calculator)

        result = await engine.evaluate_alerts(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            include_dq=False,
        )

        assert any(alert.alert_type == AlertType.STOCKOUT for alert in result.alerts)
        assert not any(alert.alert_type == AlertType.IMMINENT_STOCKOUT for alert in result.alerts)
        assert result.summary.critical_count == 1

    @pytest.mark.asyncio
    async def test_weekly_period_is_rejected_for_prompt_ten(self, mock_calculator) -> None:
        engine = AlertEngine(calculator=mock_calculator)

        with pytest.raises(ValueError, match="monthly DHIS2 periods only"):
            await engine.evaluate_alerts(
                org_unit=TEST_ORG_UNIT,
                period="2024W05",
                include_dq=False,
            )

    def test_filtered_summary_recomputes_after_filters(self) -> None:
        alerts = [
            Alert(
                alert_id="critical-1",
                alert_type=AlertType.STOCKOUT,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.SUPPLY,
                title="Stockout",
                message="Zero stock on hand",
                org_unit=TEST_ORG_UNIT,
                period=TEST_PERIOD,
            ),
            Alert(
                alert_id="warning-1",
                alert_type=AlertType.BELOW_TARGET,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.INDICATOR,
                title="Below target",
                message="Indicator below target",
                org_unit=TEST_ORG_UNIT,
                period=TEST_PERIOD,
                acknowledged=True,
                acknowledged_at=datetime.now(UTC),
            ),
        ]
        result = AlertResult(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            evaluated_at=datetime.now(UTC),
            alerts=alerts,
        )

        filtered = result.filtered(severity=AlertSeverity.WARNING, include_acknowledged=False)

        assert filtered.summary.total_alerts == 0
        assert filtered.summary.warning_count == 0
        assert filtered.summary.acknowledged_count == 0

    @pytest.mark.asyncio
    async def test_dq_summary_is_converted_to_alerts(self, mock_calculator, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_calculator.calculate_all = AsyncMock(return_value=build_result_set())
        engine = AlertEngine(calculator=mock_calculator)
        monkeypatch.setattr(
            "app.services.alert_engine.DataQualityEngine.run_checks",
            AsyncMock(
                return_value=DQResult(
                    org_unit=TEST_ORG_UNIT,
                    period=TEST_PERIOD,
                    checked_at=datetime.now(UTC),
                    summary=DQResultSummary(
                        total_checks=5,
                        passed=2,
                        critical_count=2,
                        warning_count=1,
                        info_count=0,
                    ),
                    findings=[],
                    indicators_checked=[],
                )
            ),
        )

        result = await engine.evaluate_alerts(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            include_dq=True,
        )

        assert {alert.alert_type for alert in result.alerts} == {
            AlertType.DATA_QUALITY_CRITICAL,
            AlertType.DATA_QUALITY_WARNING,
        }
        assert result.summary.critical_count == 1
        assert result.summary.warning_count == 1


@pytest.mark.unit
class TestAlertThresholdLoader:
    def test_default_thresholds_match_real_monthly_scope(self) -> None:
        loader = AlertThresholdLoader(config_path="does-not-exist.yaml")

        thresholds = loader.get_all_thresholds()

        threshold_ids = {threshold.threshold_id for threshold in thresholds}
        indicator_ids = {
            indicator_id
            for threshold in thresholds
            for indicator_id in threshold.indicator_ids
        }

        assert "SUPPLY-STOCKOUT" in threshold_ids
        assert "SYS-03" not in indicator_ids
