"""
Unit tests for the indicator calculator and result models.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.indicators.calculator import CalculationError, IndicatorCalculator
from app.indicators.models import (
    FormulaComponent,
    IndicatorCategory,
    IndicatorDefinition,
    IndicatorResult,
    IndicatorResultSet,
    ResultType,
)
from app.indicators.registry import IndicatorRegistry


@pytest.mark.unit
class TestCalculatorFormulas:
    @pytest.fixture
    def calculator(self, valid_session, population_data) -> IndicatorCalculator:
        IndicatorRegistry._instance = None
        IndicatorRegistry._initialized = False
        return IndicatorCalculator(session=valid_session, population_data=population_data)

    def test_evaluate_simple_code(self, calculator: IndicatorCalculator) -> None:
        calculator._data_cache = {"AN17a": 950.0}

        result, elements = calculator._evaluate_formula("AN17a", "ou123")

        assert result == 950.0
        assert elements["AN17a"] == 950.0

    def test_evaluate_addition(self, calculator: IndicatorCalculator) -> None:
        calculator._data_cache = {"AN20a": 100.0, "AN20b": 50.0}

        result, elements = calculator._evaluate_formula("AN20a + AN20b", "ou123")

        assert result == 150.0
        assert "AN20a" in elements
        assert "AN20b" in elements

    def test_evaluate_subtraction(self, calculator: IndicatorCalculator) -> None:
        calculator._data_cache = {"OE01": 100.0, "OE12": 5.0, "OE13": 10.0, "OE14": 15.0}

        result, _ = calculator._evaluate_formula("OE01 - OE12 - OE13 - OE14", "ou123")

        assert result == 70.0

    def test_evaluate_expected_pregnancies(self, calculator: IndicatorCalculator) -> None:
        result, elements = calculator._evaluate_formula("expected_pregnancies", "akV6429SUqu")

        assert result == 25000.0
        assert elements["expected_pregnancies"] == 25000.0

    def test_evaluate_missing_expected_pregnancies_raises(self, calculator: IndicatorCalculator) -> None:
        with pytest.raises(CalculationError) as exc_info:
            calculator._evaluate_formula("expected_pregnancies", "unknown-ou")

        assert "expected pregnancies" in str(exc_info.value).lower()

    def test_evaluate_missing_data_defaults_to_zero(self, calculator: IndicatorCalculator) -> None:
        calculator._data_cache = {"AN17a": 100.0}

        result, elements = calculator._evaluate_formula("AN17a + AN01a", "ou123")

        assert result == 100.0
        assert elements["AN01a"] is None

    def test_extract_codes_simple(self, sample_percentage_indicator) -> None:
        codes = sample_percentage_indicator.get_required_data_elements()

        assert "AN17a" in codes
        assert "AN01a" in codes

    def test_extract_codes_complex(self) -> None:
        indicator = IndicatorDefinition(
            id="TEST-01",
            name="Complex Formula Indicator",
            category=IndicatorCategory.HIV_CASCADE,
            numerator=FormulaComponent(formula="OE01 - OE12 - OE13 - OE14"),
            denominator=FormulaComponent(formula="OE01"),
            result_type=ResultType.PERCENTAGE,
        )

        assert set(indicator.get_required_data_elements()) == {"OE01", "OE12", "OE13", "OE14"}

    def test_extract_codes_weekly(self, sample_weekly_indicator) -> None:
        codes = sample_weekly_indicator.get_required_data_elements()

        assert "033B-AP05" in codes
        assert "033B-AP04" in codes


@pytest.mark.unit
class TestCalculatorResults:
    @pytest.mark.asyncio
    async def test_calculate_all_returns_result_set(
        self,
        valid_session,
        population_data,
        mock_connector,
        loaded_registry,
    ) -> None:
        calculator = IndicatorCalculator(session=valid_session, population_data=population_data)

        with patch.object(calculator, "_registry", loaded_registry):
            with patch("app.indicators.calculator.DHIS2Connector", return_value=mock_connector):
                result_set = await calculator.calculate_all(
                    org_unit="akV6429SUqu",
                    period="202401",
                    org_unit_name="Test District",
                )

        assert isinstance(result_set, IndicatorResultSet)
        assert result_set.org_unit_uid == "akV6429SUqu"
        assert result_set.period == "202401"

    @pytest.mark.asyncio
    async def test_calculate_single_returns_result(
        self,
        valid_session,
        population_data,
        mock_connector,
        loaded_registry,
    ) -> None:
        calculator = IndicatorCalculator(session=valid_session, population_data=population_data)

        with patch.object(calculator, "_registry", loaded_registry):
            with patch("app.indicators.calculator.DHIS2Connector", return_value=mock_connector):
                result = await calculator.calculate_single(
                    indicator_id="VAL-02",
                    org_unit="akV6429SUqu",
                    period="202401",
                    org_unit_name="Test District",
                )

        assert isinstance(result, IndicatorResult)
        assert result.indicator_id == "VAL-02"


@pytest.mark.unit
class TestIndicatorResultModel:
    def test_formatted_result_percentage(self) -> None:
        result = IndicatorResult(
            indicator_id="VAL-02",
            indicator_name="Test",
            category=IndicatorCategory.WHO_VALIDATION,
            org_unit_uid="ou123",
            period="202401",
            result_value=87.5,
            result_type=ResultType.PERCENTAGE,
        )

        assert result.formatted_result == "87.5%"

    def test_formatted_result_count(self) -> None:
        result = IndicatorResult(
            indicator_id="SUP-01",
            indicator_name="Test",
            category=IndicatorCategory.SUPPLY,
            org_unit_uid="ou123",
            period="202401",
            result_value=1500.0,
            result_type=ResultType.COUNT,
        )

        assert result.formatted_result == "1,500"

    def test_formatted_result_days(self) -> None:
        result = IndicatorResult(
            indicator_id="SUP-05",
            indicator_name="Test",
            category=IndicatorCategory.SUPPLY,
            org_unit_uid="ou123",
            period="202401",
            result_value=45.7,
            result_type=ResultType.DAYS,
        )

        assert result.formatted_result == "46 days"

    def test_formatted_result_na_when_none(self) -> None:
        result = IndicatorResult(
            indicator_id="VAL-02",
            indicator_name="Test",
            category=IndicatorCategory.WHO_VALIDATION,
            org_unit_uid="ou123",
            period="202401",
            result_value=None,
            result_type=ResultType.PERCENTAGE,
        )

        assert result.formatted_result == "N/A"

    def test_target_gap_calculation(self) -> None:
        result = IndicatorResult(
            indicator_id="VAL-02",
            indicator_name="Test",
            category=IndicatorCategory.WHO_VALIDATION,
            org_unit_uid="ou123",
            period="202401",
            result_value=87.5,
            result_type=ResultType.PERCENTAGE,
            target=95.0,
        )

        assert result.target_gap == 7.5


@pytest.mark.unit
class TestIndicatorResultSetModel:
    def test_add_result_updates_counts(self, sample_valid_result: IndicatorResult) -> None:
        result_set = IndicatorResultSet(org_unit_uid="ou123", period="202401")

        result_set.add_result(sample_valid_result)

        assert result_set.total_indicators == 1
        assert result_set.valid_indicators == 1
        assert result_set.indicators_meeting_target == 1

    def test_get_by_category(self, sample_result_set: IndicatorResultSet) -> None:
        who_results = sample_result_set.get_by_category(IndicatorCategory.WHO_VALIDATION)

        assert len(who_results) == 2
        assert all(result.category == IndicatorCategory.WHO_VALIDATION for result in who_results)
