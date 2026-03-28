"""
Pydantic models for indicator definitions and results.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class ResultType(str, Enum):
    """Type of indicator result."""

    PERCENTAGE = "percentage"
    COUNT = "count"
    DAYS = "days"
    RATE = "rate"


class Periodicity(str, Enum):
    """Indicator calculation periodicity."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class IndicatorCategory(str, Enum):
    """Indicator category for grouping."""

    WHO_VALIDATION = "who_validation"
    HIV_CASCADE = "hiv_cascade"
    HBV_CASCADE = "hbv_cascade"
    SYPHILIS = "syphilis"
    SYSTEM = "system"
    SUPPLY = "supply"


class FormulaComponent(BaseModel):
    """Numerator or denominator formula component."""

    formula: Optional[str] = None
    label: Optional[str] = None


class IndicatorDefinition(BaseModel):
    """Complete indicator definition loaded from YAML."""

    id: str
    name: str
    category: IndicatorCategory
    description: str = ""

    numerator: Optional[FormulaComponent] = None
    denominator: Optional[FormulaComponent] = None

    result_type: ResultType = ResultType.PERCENTAGE
    target: Optional[float] = None
    periodicity: Periodicity = Periodicity.MONTHLY
    notes: Optional[str] = None

    calculation_type: Optional[str] = None
    stock_on_hand: Optional[str] = None
    consumption: Optional[str] = None
    alias_of: Optional[str] = None

    def get_required_data_elements(self) -> List[str]:
        """Extract data element codes required for calculation."""
        codes = set()

        if self.numerator and self.numerator.formula:
            codes.update(self._extract_codes(self.numerator.formula))

        if self.denominator and self.denominator.formula:
            codes.update(self._extract_codes(self.denominator.formula))

        if self.stock_on_hand:
            codes.add(self.stock_on_hand)

        if self.consumption:
            codes.add(self.consumption)

        codes.discard("expected_pregnancies")
        codes.discard("AN21-POS")

        return list(codes)

    @staticmethod
    def _extract_codes(formula: str) -> List[str]:
        """Extract data element codes from formula string."""
        import re

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


class IndicatorResult(BaseModel):
    """Result of an indicator calculation."""

    indicator_id: str
    indicator_name: str
    category: IndicatorCategory

    org_unit_uid: str
    org_unit_name: Optional[str] = None
    period: str

    numerator_value: Optional[float] = None
    denominator_value: Optional[float] = None
    result_value: Optional[float] = None

    result_type: ResultType
    target: Optional[float] = None

    is_valid: bool = True
    meets_target: Optional[bool] = None
    error_message: Optional[str] = None

    calculated_at: datetime = Field(default_factory=_utc_now)
    data_elements_used: Dict[str, Optional[float]] = Field(default_factory=dict)

    @property
    def formatted_result(self) -> str:
        """Format result for display."""
        if self.result_value is None:
            return "N/A"

        if self.result_type == ResultType.PERCENTAGE:
            return f"{self.result_value:.1f}%"
        if self.result_type == ResultType.DAYS:
            return f"{self.result_value:.0f} days"
        if self.result_type == ResultType.COUNT:
            return f"{self.result_value:,.0f}"
        return f"{self.result_value:.2f}"

    @property
    def target_gap(self) -> Optional[float]:
        """Calculate gap to target (positive = shortfall)."""
        if self.target is None or self.result_value is None:
            return None
        return self.target - self.result_value


class IndicatorResultSet(BaseModel):
    """Collection of indicator results for a report."""

    org_unit_uid: str
    org_unit_name: Optional[str] = None
    period: str

    results: List[IndicatorResult] = Field(default_factory=list)

    generated_at: datetime = Field(default_factory=_utc_now)

    total_indicators: int = 0
    valid_indicators: int = 0
    indicators_meeting_target: int = 0

    def add_result(self, result: IndicatorResult) -> None:
        """Add a result and update summary statistics."""
        self.results.append(result)
        self.total_indicators += 1
        if result.is_valid:
            self.valid_indicators += 1
        if result.meets_target:
            self.indicators_meeting_target += 1

    def get_by_category(self, category: IndicatorCategory) -> List[IndicatorResult]:
        """Get results filtered by category."""
        return [result for result in self.results if result.category == category]

    def get_by_id(self, indicator_id: str) -> Optional[IndicatorResult]:
        """Get result by indicator ID."""
        for result in self.results:
            if result.indicator_id == indicator_id:
                return result
        return None

    def to_summary_dict(self) -> Dict[str, Any]:
        """Convert to summary dictionary for reporting."""
        return {
            "org_unit": self.org_unit_name or self.org_unit_uid,
            "period": self.period,
            "total": self.total_indicators,
            "valid": self.valid_indicators,
            "meeting_target": self.indicators_meeting_target,
            "results": {
                result.indicator_id: result.formatted_result for result in self.results
            },
        }
