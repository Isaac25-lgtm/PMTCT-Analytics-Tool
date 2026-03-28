"""
Indicator calculation engine.
Calculates all 30 PMTCT Triple Elimination indicators.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.connectors.cached_connector import CachedDHIS2Connector, build_cached_connector
from app.connectors.dhis2_connector import DHIS2Connector
from app.core.session import UserSession
from app.indicators.models import (
    IndicatorCategory,
    IndicatorDefinition,
    IndicatorResult,
    IndicatorResultSet,
    ResultType,
)
from app.indicators.registry import get_indicator_registry

logger = logging.getLogger(__name__)


class CalculationError(Exception):
    """Raised when indicator calculation fails."""


class SafeMathParser:
    """Recursive-descent parser for simple arithmetic expressions."""

    def __init__(self, expression: str):
        self._expression = expression
        self._tokens = self._tokenize(expression)
        self._index = 0

    @classmethod
    def parse(cls, expression: str) -> float:
        """Parse and evaluate a math expression safely."""
        parser = cls(expression)
        if not parser._tokens:
            raise CalculationError("Empty formula")

        result = parser._parse_expression()
        if parser._current_token() is not None:
            raise CalculationError("Unexpected token")
        return result

    def _tokenize(self, expression: str) -> List[str]:
        tokens: List[str] = []
        index = 0

        while index < len(expression):
            char = expression[index]

            if char.isspace():
                index += 1
                continue

            if char in "+-*/()":
                tokens.append(char)
                index += 1
                continue

            if char.isdigit() or char == ".":
                start = index
                dot_count = 0
                digit_count = 0

                while index < len(expression) and (
                    expression[index].isdigit() or expression[index] == "."
                ):
                    if expression[index] == ".":
                        dot_count += 1
                    else:
                        digit_count += 1
                    index += 1

                if dot_count > 1 or digit_count == 0:
                    raise CalculationError(f"Invalid number in formula: {expression}")

                tokens.append(expression[start:index])
                continue

            raise CalculationError(f"Invalid formula: {expression}")

        return tokens

    def _current_token(self) -> str | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _consume(self, expected: str | None = None) -> str:
        token = self._current_token()
        if token is None:
            raise CalculationError("Unexpected end of formula")
        if expected is not None and token != expected:
            raise CalculationError(f"Expected '{expected}'")
        self._index += 1
        return token

    def _parse_expression(self) -> float:
        value = self._parse_term()

        while self._current_token() in {"+", "-"}:
            operator = self._consume()
            right = self._parse_term()
            if operator == "+":
                value += right
            else:
                value -= right

        return value

    def _parse_term(self) -> float:
        value = self._parse_factor()

        while self._current_token() in {"*", "/"}:
            operator = self._consume()
            right = self._parse_factor()
            if operator == "*":
                value *= right
            else:
                if right == 0:
                    raise CalculationError("Division by zero")
                value /= right

        return value

    def _parse_factor(self) -> float:
        token = self._current_token()
        if token is None:
            raise CalculationError("Unexpected end of formula")

        if token == "-":
            self._consume("-")
            return -self._parse_factor()

        if token == "(":
            self._consume("(")
            value = self._parse_expression()
            self._consume(")")
            return value

        self._consume()
        try:
            return float(token)
        except ValueError as exc:
            raise CalculationError(f"Invalid token: {token}") from exc


def parse_math_expression(expression: str) -> float:
    """Evaluate a simple arithmetic expression without eval()."""
    return SafeMathParser.parse(expression)


class IndicatorCalculator:
    """
    Calculates PMTCT Triple Elimination indicators.

    Uses DHIS2Connector for data extraction and IndicatorRegistry
    for indicator definitions.
    """

    def __init__(
        self,
        session: UserSession,
        population_data: Optional[Dict[str, int]] = None,
    ):
        """
        Initialize calculator.

        Args:
            session: Authenticated user session
            population_data: Dict mapping org_unit_uid -> expected_pregnancies
                            For facility-level, can be None (indicator hidden)
        """
        self._session = session
        self._registry = get_indicator_registry()
        self._population_data = population_data or {}

        # Cache for fetched data values
        self._data_cache: Dict[str, Optional[float]] = {}

    def set_expected_pregnancies(self, org_unit: str, value: int) -> None:
        """
        Set expected pregnancies for an org unit.

        Used by API routes to inject facility-level values for VAL-01.
        """
        self._population_data[org_unit] = value

    def clear_expected_pregnancies(self, org_unit: str | None = None) -> None:
        """Clear expected-pregnancy overrides for one org unit or all."""
        if org_unit is None:
            self._population_data.clear()
            return
        self._population_data.pop(org_unit, None)

    async def calculate_all(
        self,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str] = None,
        include_children: bool = False,
        categories: Optional[List[IndicatorCategory]] = None,
    ) -> IndicatorResultSet:
        """
        Calculate all indicators for an org unit and period.

        Args:
            org_unit: Organisation unit UID
            period: DHIS2 period (e.g., "202401", "2024W05")
            org_unit_name: Optional display name
            include_children: Aggregate child org units
            categories: Optional list of categories to calculate (default: all)

        Returns:
            IndicatorResultSet with all calculated results
        """
        result_set = IndicatorResultSet(
            org_unit_uid=org_unit,
            org_unit_name=org_unit_name,
            period=period,
        )

        indicators = self._registry.get_all()
        if categories:
            indicators = [indicator for indicator in indicators if indicator.category in categories]

        self._data_cache.clear()

        async with build_cached_connector(self._session) as connector:
            await self._prefetch_data(
                connector,
                indicators,
                org_unit,
                period,
                include_children,
            )

            for indicator in indicators:
                try:
                    result = await self._calculate_indicator(
                        connector,
                        indicator,
                        org_unit,
                        period,
                        org_unit_name,
                    )
                    result_set.add_result(result)
                except Exception as exc:
                    logger.error("Failed to calculate %s: %s", indicator.id, exc)
                    result_set.add_result(
                        IndicatorResult(
                            indicator_id=indicator.id,
                            indicator_name=indicator.name,
                            category=indicator.category,
                            org_unit_uid=org_unit,
                            org_unit_name=org_unit_name,
                            period=period,
                            result_type=indicator.result_type,
                            target=indicator.target,
                            is_valid=False,
                            error_message=str(exc),
                        )
                    )

        return result_set

    async def calculate_single(
        self,
        indicator_id: str,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str] = None,
        include_children: bool = False,
    ) -> IndicatorResult:
        """Calculate a single indicator."""
        indicator = self._registry.get(indicator_id)
        if not indicator:
            raise CalculationError(f"Unknown indicator: {indicator_id}")

        self._data_cache.clear()

        async with build_cached_connector(self._session) as connector:
            await self._prefetch_data(
                connector,
                [indicator],
                org_unit,
                period,
                include_children,
            )
            return await self._calculate_indicator(
                connector,
                indicator,
                org_unit,
                period,
                org_unit_name,
            )

    async def _prefetch_data(
        self,
        connector: CachedDHIS2Connector,
        indicators: List[IndicatorDefinition],
        org_unit: str,
        period: str,
        include_children: bool,
    ) -> None:
        """Pre-fetch all required data elements in batch."""
        all_codes = set()
        needs_an21_pos = False

        for indicator in indicators:
            all_codes.update(indicator.get_required_data_elements())

            formulas = []
            if indicator.numerator and indicator.numerator.formula:
                formulas.append(indicator.numerator.formula)
            if indicator.denominator and indicator.denominator.formula:
                formulas.append(indicator.denominator.formula)

            if any("AN21-POS" in formula for formula in formulas):
                needs_an21_pos = True

        uids: List[str] = []
        code_to_uid: Dict[str, str] = {}
        for code in all_codes:
            uid = self._registry.get_data_element_uid(code)
            if uid:
                uids.append(uid)
                code_to_uid[code] = uid
            else:
                logger.warning("Unknown data element code: %s", code)

        if uids:
            values = await connector.get_data_values(
                data_elements=uids,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            )

            for code, uid in code_to_uid.items():
                self._data_cache[code] = values.get(uid)

        if needs_an21_pos:
            an21_pos = await connector.get_an21_pos_total(org_unit, period)
            self._data_cache["AN21-POS"] = an21_pos

    async def _calculate_indicator(
        self,
        connector: CachedDHIS2Connector,
        indicator: IndicatorDefinition,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str],
    ) -> IndicatorResult:
        """Calculate a single indicator."""
        if indicator.calculation_type == "completeness_api":
            return await self._calculate_completeness(
                connector,
                indicator,
                org_unit,
                period,
                org_unit_name,
            )

        if indicator.calculation_type == "days_of_use":
            return await self._calculate_dou(
                connector,
                indicator,
                org_unit,
                period,
                org_unit_name,
            )

        if indicator.alias_of:
            alias_indicator = self._registry.get(indicator.alias_of)
            if alias_indicator:
                result = await self._calculate_indicator(
                    connector,
                    alias_indicator,
                    org_unit,
                    period,
                    org_unit_name,
                )
                result.indicator_id = indicator.id
                result.indicator_name = indicator.name
                result.category = indicator.category
                result.target = indicator.target
                return result

        numerator_value = None
        denominator_value = None
        result_value = None
        error_message = None
        data_elements_used: Dict[str, Optional[float]] = {}

        if indicator.numerator and indicator.numerator.formula:
            try:
                numerator_value, numerator_elements = self._evaluate_formula(
                    indicator.numerator.formula,
                    org_unit,
                )
                data_elements_used.update(numerator_elements)
            except Exception as exc:
                error_message = f"Numerator error: {exc}"

        if indicator.denominator and indicator.denominator.formula:
            try:
                denominator_value, denominator_elements = self._evaluate_formula(
                    indicator.denominator.formula,
                    org_unit,
                )
                data_elements_used.update(denominator_elements)
            except Exception as exc:
                error_message = f"Denominator error: {exc}"

        is_valid = True

        if indicator.result_type == ResultType.COUNT:
            result_value = numerator_value
        elif numerator_value is not None and denominator_value is not None:
            if denominator_value > 0:
                result_value = (numerator_value / denominator_value) * 100
            else:
                error_message = "Denominator is zero"
                is_valid = False
        elif denominator_value == 0:
            error_message = "Denominator is zero"
            is_valid = False
        elif numerator_value is None or denominator_value is None:
            is_valid = False
            if not error_message:
                error_message = "Missing data"

        meets_target = None
        if indicator.target is not None and result_value is not None:
            meets_target = result_value >= indicator.target

        return IndicatorResult(
            indicator_id=indicator.id,
            indicator_name=indicator.name,
            category=indicator.category,
            org_unit_uid=org_unit,
            org_unit_name=org_unit_name,
            period=period,
            numerator_value=numerator_value,
            denominator_value=denominator_value,
            result_value=result_value,
            result_type=indicator.result_type,
            target=indicator.target,
            is_valid=is_valid,
            meets_target=meets_target,
            error_message=error_message,
            data_elements_used=data_elements_used,
        )

    def _evaluate_formula(
        self,
        formula: str,
        org_unit: str,
    ) -> tuple[Optional[float], Dict[str, Optional[float]]]:
        """
        Evaluate a formula string using cached data values.

        Returns:
            Tuple of (result, dict of data elements used)
        """
        elements_used: Dict[str, Optional[float]] = {}

        if formula == "expected_pregnancies":
            expected_pregnancies = self._population_data.get(org_unit)
            if expected_pregnancies is None:
                raise CalculationError("No expected pregnancies data for this org unit")
            value = float(expected_pregnancies)
            return value, {"expected_pregnancies": value}

        working_formula = formula
        codes = self._extract_codes(formula)

        for code in codes:
            value = self._data_cache.get(code)
            elements_used[code] = value

            replacement = "0" if value is None else str(value)
            working_formula = re.sub(
                rf"\b{re.escape(code)}\b",
                replacement,
                working_formula,
            )

        try:
            result = parse_math_expression(working_formula)
            return float(result), elements_used
        except CalculationError:
            raise
        except Exception as exc:
            raise CalculationError(f"Formula evaluation failed: {exc}") from exc

    @staticmethod
    def _extract_codes(formula: str) -> List[str]:
        """Extract data element codes from formula."""
        pattern = (
            r"\b("
            r"AN21-POS|"
            r"033B-[A-Z]{2}\d{2}|"
            r"AN\d{2}[a-z]?\d?|"
            r"OE\d{2}[a-z]?|"
            r"MA\d{2}[a-z]?\d?|"
            r"CL\d{2}|"
            r"HB\d{2}|"
            r"SS\d{2}[a-z]"
            r")\b"
        )
        return re.findall(pattern, formula)

    async def _calculate_completeness(
        self,
        connector: CachedDHIS2Connector,
        indicator: IndicatorDefinition,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str],
    ) -> IndicatorResult:
        """Calculate reporting completeness using DHIS2 API."""
        return IndicatorResult(
            indicator_id=indicator.id,
            indicator_name=indicator.name,
            category=indicator.category,
            org_unit_uid=org_unit,
            org_unit_name=org_unit_name,
            period=period,
            result_type=ResultType.PERCENTAGE,
            is_valid=False,
            # TODO(PMTCT): Wire up HMIS 105/033b dataset UIDs for completeness.
            # See config/mappings.yaml for dataset UID placeholders.
            error_message="Reporting completeness data is not yet available for this instance",
        )

    async def _calculate_dou(
        self,
        connector: CachedDHIS2Connector,
        indicator: IndicatorDefinition,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str],
    ) -> IndicatorResult:
        """
        Calculate Days of Use (DOU) for supply indicators.

        Formula: DOU = SOH / ADC
        Where: ADC = Consumption / period_days
        """
        if not indicator.stock_on_hand or not indicator.consumption:
            return IndicatorResult(
                indicator_id=indicator.id,
                indicator_name=indicator.name,
                category=indicator.category,
                org_unit_uid=org_unit,
                org_unit_name=org_unit_name,
                period=period,
                result_type=ResultType.DAYS,
                is_valid=False,
                error_message="Missing stock_on_hand or consumption config",
            )

        stock_on_hand = self._data_cache.get(indicator.stock_on_hand)
        consumption = self._data_cache.get(indicator.consumption)

        data_elements_used = {
            indicator.stock_on_hand: stock_on_hand,
            indicator.consumption: consumption,
        }

        if stock_on_hand is None or consumption is None:
            return IndicatorResult(
                indicator_id=indicator.id,
                indicator_name=indicator.name,
                category=indicator.category,
                org_unit_uid=org_unit,
                org_unit_name=org_unit_name,
                period=period,
                result_type=ResultType.DAYS,
                is_valid=False,
                error_message="Missing stock or consumption data",
                data_elements_used=data_elements_used,
            )

        period_days = DHIS2Connector.get_period_days(period)
        average_daily_consumption = consumption / period_days if period_days > 0 else 0

        if average_daily_consumption > 0:
            days_of_use = stock_on_hand / average_daily_consumption
        else:
            days_of_use = None

        return IndicatorResult(
            indicator_id=indicator.id,
            indicator_name=indicator.name,
            category=indicator.category,
            org_unit_uid=org_unit,
            org_unit_name=org_unit_name,
            period=period,
            numerator_value=stock_on_hand,
            denominator_value=average_daily_consumption,
            result_value=days_of_use,
            result_type=ResultType.DAYS,
            is_valid=days_of_use is not None,
            error_message="Zero consumption" if days_of_use is None else None,
            data_elements_used=data_elements_used,
        )


def load_population_data(
    config_path: str = "config/populations.yaml",
) -> Dict[str, int]:
    """
    Load population data from YAML.

    Returns:
        Dict mapping org_unit_uid -> expected_pregnancies
    """
    path = Path(config_path)
    if not path.exists():
        logger.warning("Population config not found: %s", path)
        return {}

    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    result: Dict[str, int] = {}

    for uid, data in config.get("districts", {}).items():
        if uid == "PLACEHOLDER_DISTRICT_UID":
            continue

        expected_pregnancies = data.get("expected_pregnancies")
        if expected_pregnancies is not None:
            result[uid] = int(expected_pregnancies)

    national = config.get("national", {})
    if "expected_pregnancies" in national:
        # TODO: Get national org unit UID
        pass

    return result
