"""
Unit tests for the indicator registry.
"""

from __future__ import annotations

import pytest

from app.indicators.models import IndicatorCategory, Periodicity, ResultType
from app.indicators.registry import IndicatorRegistry, get_indicator_registry


@pytest.mark.unit
class TestIndicatorRegistry:
    def test_singleton_pattern(self) -> None:
        IndicatorRegistry._instance = None
        IndicatorRegistry._initialized = False

        registry_one = IndicatorRegistry()
        registry_two = IndicatorRegistry()

        assert registry_one is registry_two

    def test_load_populates_indicators_and_mappings(
        self,
        fresh_registry: IndicatorRegistry,
        minimal_indicators_yaml,
        minimal_mappings_yaml,
    ) -> None:
        fresh_registry.load(
            indicators_path=str(minimal_indicators_yaml),
            mappings_path=str(minimal_mappings_yaml),
        )

        assert fresh_registry.is_loaded is True
        assert fresh_registry.indicator_count == 5

    def test_load_missing_indicators_raises(
        self,
        fresh_registry: IndicatorRegistry,
        minimal_mappings_yaml,
    ) -> None:
        with pytest.raises(FileNotFoundError):
            fresh_registry.load(
                indicators_path="missing-indicators.yaml",
                mappings_path=str(minimal_mappings_yaml),
            )

    def test_load_missing_mappings_raises(
        self,
        fresh_registry: IndicatorRegistry,
        minimal_indicators_yaml,
    ) -> None:
        with pytest.raises(FileNotFoundError):
            fresh_registry.load(
                indicators_path=str(minimal_indicators_yaml),
                mappings_path="missing-mappings.yaml",
            )

    def test_get_returns_indicator(self, loaded_registry: IndicatorRegistry) -> None:
        indicator = loaded_registry.get("VAL-02")

        assert indicator is not None
        assert indicator.id == "VAL-02"
        assert indicator.name == "HIV Testing Coverage at ANC"
        assert indicator.category == IndicatorCategory.WHO_VALIDATION
        assert indicator.result_type == ResultType.PERCENTAGE
        assert indicator.target == 95.0

    def test_get_returns_none_for_unknown(self, loaded_registry: IndicatorRegistry) -> None:
        assert loaded_registry.get("UNKNOWN") is None

    def test_get_all_returns_all_indicators(self, loaded_registry: IndicatorRegistry) -> None:
        indicators = loaded_registry.get_all()

        assert len(indicators) == 5
        assert {indicator.id for indicator in indicators} >= {"VAL-02", "SUP-01", "SYS-03"}

    def test_get_by_category_filters_results(self, loaded_registry: IndicatorRegistry) -> None:
        who_indicators = loaded_registry.get_by_category(IndicatorCategory.WHO_VALIDATION)
        supply_indicators = loaded_registry.get_by_category(IndicatorCategory.SUPPLY)

        assert all(indicator.category == IndicatorCategory.WHO_VALIDATION for indicator in who_indicators)
        assert all(indicator.category == IndicatorCategory.SUPPLY for indicator in supply_indicators)

    def test_get_data_element_uid_returns_mapping(self, loaded_registry: IndicatorRegistry) -> None:
        assert loaded_registry.get_data_element_uid("AN01a") == "Q9nSogNmKPt"

    def test_get_data_element_uid_returns_none_for_unknown(self, loaded_registry: IndicatorRegistry) -> None:
        assert loaded_registry.get_data_element_uid("UNKNOWN_CODE") is None

    def test_get_an21_pos_cocs_returns_list(self, loaded_registry: IndicatorRegistry) -> None:
        cocs = loaded_registry.get_an21_pos_cocs()

        assert cocs == ["H9qJO0yGTKz", "BaWI6qkhScq"]

    def test_is_loaded_false_before_load(self, fresh_registry: IndicatorRegistry) -> None:
        assert fresh_registry.is_loaded is False


@pytest.mark.unit
class TestIndicatorDefinitionParsing:
    def test_parse_percentage_indicator(self, loaded_registry: IndicatorRegistry) -> None:
        indicator = loaded_registry.get("VAL-02")

        assert indicator is not None
        assert indicator.result_type == ResultType.PERCENTAGE
        assert indicator.numerator is not None
        assert indicator.denominator is not None
        assert indicator.numerator.formula == "AN17a"
        assert indicator.denominator.formula == "AN01a"

    def test_parse_count_indicator(self, loaded_registry: IndicatorRegistry) -> None:
        indicator = loaded_registry.get("SUP-01")

        assert indicator is not None
        assert indicator.result_type == ResultType.COUNT
        assert indicator.denominator is None

    def test_parse_days_of_use_indicator(self, loaded_registry: IndicatorRegistry) -> None:
        indicator = loaded_registry.get("SUP-05")

        assert indicator is not None
        assert indicator.result_type == ResultType.DAYS
        assert indicator.calculation_type == "days_of_use"
        assert indicator.stock_on_hand == "SS40c"
        assert indicator.consumption == "SS40a"

    def test_parse_weekly_indicator(self, loaded_registry: IndicatorRegistry) -> None:
        indicator = loaded_registry.get("SYS-03")

        assert indicator is not None
        assert indicator.periodicity == Periodicity.WEEKLY


@pytest.mark.unit
class TestGetIndicatorRegistry:
    def test_get_indicator_registry_returns_loaded_registry(self, tmp_path, monkeypatch) -> None:
        IndicatorRegistry._instance = None
        IndicatorRegistry._initialized = False

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mappings.yaml").write_text(
            """
data_elements:
  AN01a: Q9nSogNmKPt
an21_pos_cocs: []
""",
            encoding="utf-8",
        )
        (config_dir / "indicators.yaml").write_text(
            """
indicators:
  TEST-01:
    id: "TEST-01"
    name: "Test Indicator"
    category: "system"
    result_type: "percentage"
    periodicity: "monthly"
""",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        registry = get_indicator_registry()

        assert registry.is_loaded is True
        assert registry.get("TEST-01") is not None
