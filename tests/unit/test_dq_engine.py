"""
Unit tests for the Prompt 9 data-quality engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.indicators.models import IndicatorCategory, IndicatorResultSet, ResultType
from app.services.data_quality import DQResult, DQResultSummary, DQRuleLoader, DataQualityEngine
from app.services.dq_rules import DQCategory, DQFinding, DQSeverity
from tests.conftest import TEST_ORG_UNIT, TEST_ORG_UNIT_NAME, TEST_PERIOD, build_result


def build_result_set(*results):
    """Build a small result set for DQ tests."""
    result_set = IndicatorResultSet(
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=TEST_PERIOD,
    )
    for result in results:
        result_set.add_result(result)
    return result_set


@pytest.mark.unit
class TestDataQualityEngine:
    def test_monthly_period_validation(self) -> None:
        assert DataQualityEngine._is_monthly_period("202401") is True
        assert DataQualityEngine._is_monthly_period("202412") is True
        assert DataQualityEngine._is_monthly_period("2024W05") is False
        assert DataQualityEngine._is_monthly_period("2024") is False
        assert DataQualityEngine._is_monthly_period("20241") is False

    def test_generate_historical_periods_rolls_across_year_boundary(self) -> None:
        periods = DataQualityEngine._generate_historical_periods_monthly("202402", 3)

        assert periods == ["202311", "202312", "202401"]

    @pytest.mark.asyncio
    async def test_run_checks_uses_real_result_fields(
        self,
        loaded_registry,
        mock_calculator,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.data_quality.get_indicator_registry", lambda: loaded_registry)
        mock_calculator.calculate_all = AsyncMock(
            return_value=build_result_set(
                build_result(
                    "VAL-02",
                    "HIV Testing Coverage at ANC",
                    IndicatorCategory.WHO_VALIDATION,
                    result_value=110.0,
                    numerator_value=1100.0,
                    denominator_value=1000.0,
                    target=95.0,
                    meets_target=True,
                )
            )
        )
        engine = DataQualityEngine(calculator=mock_calculator)

        result = await engine.run_checks(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            indicator_ids=["VAL-02"],
            include_historical=False,
        )

        assert result.summary.total_checks == 3
        assert result.summary.warning_count == 2
        assert {finding.rule_id for finding in result.findings} == {"DQ-002", "DQ-003"}
        assert all(finding.indicator_id == "VAL-02" for finding in result.findings)

    @pytest.mark.asyncio
    async def test_repeated_values_finding_uses_monthly_history(
        self,
        loaded_registry,
        mock_calculator,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.data_quality.get_indicator_registry", lambda: loaded_registry)
        current_result = build_result(
            "VAL-02",
            "HIV Testing Coverage at ANC",
            IndicatorCategory.WHO_VALIDATION,
            result_value=95.0,
            numerator_value=950.0,
            denominator_value=1000.0,
            target=95.0,
            meets_target=True,
        )
        mock_calculator.calculate_all = AsyncMock(return_value=build_result_set(current_result))
        mock_calculator.calculate_single = AsyncMock(return_value=current_result)
        engine = DataQualityEngine(calculator=mock_calculator)

        result = await engine.run_checks(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            indicator_ids=["VAL-02"],
            include_historical=True,
            historical_periods=3,
        )

        assert any(finding.rule_id == "DQ-005" for finding in result.findings)
        assert result.summary.info_count == 1

    @pytest.mark.asyncio
    async def test_weekly_indicator_skips_historical_checks(
        self,
        loaded_registry,
        mock_calculator,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.data_quality.get_indicator_registry", lambda: loaded_registry)
        weekly_result = build_result(
            "SYS-03",
            "Missed Appointment Rate",
            IndicatorCategory.SYSTEM,
            result_value=25.0,
            numerator_value=5.0,
            denominator_value=20.0,
            result_type=ResultType.PERCENTAGE,
        )
        mock_calculator.calculate_all = AsyncMock(return_value=build_result_set(weekly_result))
        mock_calculator.calculate_single = AsyncMock(return_value=weekly_result)
        engine = DataQualityEngine(calculator=mock_calculator)

        result = await engine.run_checks(
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            indicator_ids=["SYS-03"],
            include_historical=True,
            historical_periods=3,
        )

        mock_calculator.calculate_single.assert_not_awaited()
        assert result.summary.total_checks == 2
        assert not any(finding.rule_id in {"DQ-004", "DQ-005"} for finding in result.findings)

    @pytest.mark.asyncio
    async def test_dq_score_formula_matches_frozen_thresholds(self, mock_calculator) -> None:
        engine = DataQualityEngine(calculator=mock_calculator)
        engine.run_checks = AsyncMock(
            return_value=DQResult(
                org_unit=TEST_ORG_UNIT,
                period=TEST_PERIOD,
                checked_at=datetime.now(UTC),
                summary=DQResultSummary(
                    total_checks=10,
                    passed=6,
                    critical_count=1,
                    warning_count=2,
                    info_count=1,
                ),
                findings=[
                    DQFinding(
                        rule_id="DQ-001",
                        rule_name="Negative Value Check",
                        severity=DQSeverity.CRITICAL,
                        category=DQCategory.CONSISTENCY,
                        message="Negative value detected",
                        org_unit=TEST_ORG_UNIT,
                        period=TEST_PERIOD,
                    )
                ],
            )
        )

        score = await engine.get_dq_score(org_unit=TEST_ORG_UNIT, period=TEST_PERIOD)

        assert score["score"] == 83.0
        assert score["grade"] == "B"
        assert score["grade_label"] == "Good"


@pytest.mark.unit
class TestDQRuleLoader:
    def test_default_rules_include_reconciliation_check(self) -> None:
        loader = DQRuleLoader(config_path="does-not-exist.yaml")

        rules = loader.get_all_rules()

        rule_ids = {rule.rule_id for rule in rules}
        assert "DQ-007" in rule_ids
        assert loader.get_reconciliation_pairs() == [
            ("HBV-01", "SUP-01"),
            ("VAL-02", "SUP-03"),
            ("VAL-04", "SUP-03"),
        ]
