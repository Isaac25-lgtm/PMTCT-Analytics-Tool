"""
Indicator registry - loads and manages indicator definitions from YAML.

UIDs are loaded from config/mappings.yaml (single source of truth).
Indicator definitions are loaded from config/indicators.yaml.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.indicators.models import (
    FormulaComponent,
    IndicatorCategory,
    IndicatorDefinition,
    Periodicity,
    ResultType,
)

logger = logging.getLogger(__name__)


class IndicatorRegistry:
    """
    Registry of indicator definitions loaded from YAML.

    Singleton pattern - indicators are loaded once and cached.

    UIDs come from config/mappings.yaml (shared with connector).
    Indicator definitions come from config/indicators.yaml.
    """

    _instance: Optional["IndicatorRegistry"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if IndicatorRegistry._initialized:
            return

        self._indicators: Dict[str, IndicatorDefinition] = {}
        self._data_elements: Dict[str, str] = {}
        self._an21_pos_cocs: List[str] = []
        self._indicators_path: Optional[Path] = None
        self._mappings_path: Optional[Path] = None

        IndicatorRegistry._initialized = True

    def load(
        self,
        indicators_path: str = "config/indicators.yaml",
        mappings_path: str = "config/mappings.yaml",
    ) -> None:
        """
        Load indicators and UID mappings from YAML files.

        Args:
            indicators_path: Path to indicator definitions
            mappings_path: Path to UID mappings (single source of truth)
        """
        self._indicators.clear()
        self._data_elements.clear()
        self._an21_pos_cocs = []

        self._load_mappings(mappings_path)
        self._load_indicators(indicators_path)

    def _load_mappings(self, mappings_path: str) -> None:
        """Load data element UIDs from mappings.yaml."""
        path = Path(mappings_path)
        if not path.exists():
            raise FileNotFoundError(f"Mappings config not found: {path}")

        self._mappings_path = path

        with path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        raw_data_elements = config.get("data_elements", {})
        normalized_data_elements: Dict[str, str] = {}

        for code, data in raw_data_elements.items():
            if isinstance(data, dict):
                uid = data.get("uid")
            else:
                uid = data

            if uid:
                normalized_data_elements[code] = uid

        self._data_elements = normalized_data_elements

        direct_cocs = config.get("an21_pos_cocs", [])
        if isinstance(direct_cocs, list) and direct_cocs:
            self._an21_pos_cocs = direct_cocs
        else:
            coc_section = config.get("category_option_combos", {})
            an21_pos = coc_section.get("AN21_POS", {})
            combos = an21_pos.get("combos", [])
            self._an21_pos_cocs = [
                combo.get("coc_uid")
                for combo in combos
                if isinstance(combo, dict) and combo.get("coc_uid")
            ]

        logger.info(
            "Loaded %d data element mappings from %s",
            len(self._data_elements),
            path,
        )

    def _load_indicators(self, indicators_path: str) -> None:
        """Load indicator definitions from indicators.yaml."""
        path = Path(indicators_path)
        if not path.exists():
            raise FileNotFoundError(f"Indicator config not found: {path}")

        self._indicators_path = path

        with path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        for indicator_id, indicator_data in config.get("indicators", {}).items():
            try:
                definition = self._parse_indicator(indicator_id, indicator_data)
                self._indicators[indicator_id] = definition
            except Exception as exc:
                logger.error("Failed to parse indicator %s: %s", indicator_id, exc)
                raise

        logger.info("Loaded %d indicators from %s", len(self._indicators), path)

    def _parse_indicator(
        self,
        indicator_id: str,
        data: Dict,
    ) -> IndicatorDefinition:
        """Parse indicator data into IndicatorDefinition."""
        numerator = None
        denominator = None

        if "numerator" in data and data["numerator"]:
            numerator = FormulaComponent(
                formula=data["numerator"].get("formula"),
                label=data["numerator"].get("label"),
            )

        if "denominator" in data and data["denominator"]:
            denominator_data = data["denominator"]
            if denominator_data.get("formula") is not None:
                denominator = FormulaComponent(
                    formula=denominator_data.get("formula"),
                    label=denominator_data.get("label"),
                )

        return IndicatorDefinition(
            id=data.get("id", indicator_id),
            name=data.get("name", indicator_id),
            category=IndicatorCategory(data.get("category", "system")),
            description=data.get("description", ""),
            numerator=numerator,
            denominator=denominator,
            result_type=ResultType(data.get("result_type", "percentage")),
            target=data.get("target"),
            periodicity=Periodicity(data.get("periodicity", "monthly")),
            notes=data.get("notes"),
            calculation_type=data.get("calculation_type"),
            stock_on_hand=data.get("stock_on_hand"),
            consumption=data.get("consumption"),
            alias_of=data.get("alias_of"),
        )

    def get(self, indicator_id: str) -> Optional[IndicatorDefinition]:
        """Get indicator definition by ID."""
        return self._indicators.get(indicator_id)

    def get_all(self) -> List[IndicatorDefinition]:
        """Get all indicator definitions."""
        return list(self._indicators.values())

    def get_by_category(
        self,
        category: IndicatorCategory,
    ) -> List[IndicatorDefinition]:
        """Get indicators filtered by category."""
        return [
            indicator for indicator in self._indicators.values() if indicator.category == category
        ]

    def get_data_element_uid(self, code: str) -> Optional[str]:
        """Get DHIS2 UID for a data element code."""
        return self._data_elements.get(code)

    def get_all_data_element_uids(self) -> Dict[str, str]:
        """Get all data element code -> UID mappings."""
        return self._data_elements.copy()

    def get_an21_pos_cocs(self) -> List[str]:
        """Get COC UIDs for AN21-POS calculation."""
        return self._an21_pos_cocs.copy()

    def resolve_formula_uids(self, formula: str) -> str:
        """Replace data element codes with UIDs in formula."""
        resolved = formula
        sorted_codes = sorted(
            self._data_elements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )

        for code, uid in sorted_codes:
            resolved = re.sub(rf"\b{re.escape(code)}\b", uid, resolved)

        return resolved

    @property
    def indicator_count(self) -> int:
        return len(self._indicators)

    @property
    def is_loaded(self) -> bool:
        return len(self._indicators) > 0


def get_indicator_registry() -> IndicatorRegistry:
    """Get or create the indicator registry singleton."""
    registry = IndicatorRegistry()
    if not registry.is_loaded:
        registry.load()
    return registry
