"""
Data quality rule definitions and reusable check helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DQSeverity(str, Enum):
    """Severity levels for data-quality findings."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class DQCategory(str, Enum):
    """Categories of data-quality checks."""

    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    OUTLIER = "outlier"
    TIMELINESS = "timeliness"
    CASCADE = "cascade"
    RECONCILIATION = "reconciliation"


@dataclass(slots=True)
class DQFinding:
    """A single data-quality finding."""

    rule_id: str
    rule_name: str
    severity: DQSeverity
    category: DQCategory
    message: str
    org_unit: str
    period: str
    indicator_id: Optional[str] = None
    data_element: Optional[str] = None
    current_value: Optional[float] = None
    expected_range: Optional[str] = None
    recommendation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the finding to a JSON-serializable dictionary."""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "category": self.category.value,
            "message": self.message,
            "org_unit": self.org_unit,
            "period": self.period,
            "indicator_id": self.indicator_id,
            "data_element": self.data_element,
            "current_value": self.current_value,
            "expected_range": self.expected_range,
            "recommendation": self.recommendation,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class DQRule:
    """Configuration for a data-quality rule."""

    rule_id: str
    name: str
    description: str
    severity: DQSeverity
    category: DQCategory
    enabled: bool = True
    params: Dict[str, Any] = field(default_factory=dict)
    applies_to: List[str] = field(default_factory=lambda: ["ALL"])

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.rule_id:
            raise ValueError("rule_id is required")
        if not self.name:
            raise ValueError("name is required")


def check_negative_value(
    value: Optional[float],
    org_unit: str,
    period: str,
    indicator_id: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Flag negative values on count-style data."""
    if value is not None and value < 0:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.CRITICAL,
            category=DQCategory.CONSISTENCY,
            message=f"Negative value detected: {value}",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=value,
            expected_range=">= 0",
            recommendation="Verify data entry. Negative counts are not valid.",
        )
    return None


def check_percentage_bounds(
    value: Optional[float],
    org_unit: str,
    period: str,
    indicator_id: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Check that percentage indicators stay within configured bounds."""
    if value is None:
        return None

    max_threshold = float(rule.params.get("max_percentage", 105.0))

    if value < 0:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.CRITICAL,
            category=DQCategory.CONSISTENCY,
            message=f"Percentage below 0%: {value:.1f}%",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=value,
            expected_range="0% - 100%",
            recommendation="Check numerator and denominator values.",
        )

    if value > max_threshold:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.CRITICAL if value > 150 else DQSeverity.WARNING,
            category=DQCategory.CONSISTENCY,
            message=f"Percentage exceeds {max_threshold:.1f}%: {value:.1f}%",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=value,
            expected_range=f"0% - {max_threshold:.1f}%",
            recommendation="Numerator may exceed denominator. Verify both values.",
        )

    return None


def check_numerator_exceeds_denominator(
    numerator: Optional[float],
    denominator: Optional[float],
    org_unit: str,
    period: str,
    indicator_id: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Flag indicators whose numerator materially exceeds the denominator."""
    if numerator is None or denominator is None:
        return None

    if denominator <= 0:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.WARNING,
            category=DQCategory.CONSISTENCY,
            message=f"Denominator is zero or negative: {denominator}",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=denominator,
            expected_range="> 0",
            recommendation="Check denominator data element for missing or incorrect values.",
        )

    tolerance = float(rule.params.get("tolerance_percent", 5.0))
    max_allowed = denominator * (1 + tolerance / 100)
    if numerator > max_allowed:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.WARNING,
            category=DQCategory.CONSISTENCY,
            message=f"Numerator ({numerator}) exceeds denominator ({denominator})",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=numerator,
            expected_range=f"<= {denominator}",
            recommendation="Verify both numerator and denominator values.",
            metadata={"numerator": numerator, "denominator": denominator},
        )

    return None


def check_outlier_mad(
    current_value: Optional[float],
    historical_values: List[float],
    org_unit: str,
    period: str,
    indicator_id: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Check for outliers using Median Absolute Deviation."""
    if current_value is None or len(historical_values) < 3:
        return None

    import statistics

    median = statistics.median(historical_values)
    deviations = [abs(value - median) for value in historical_values]
    mad = statistics.median(deviations)

    if mad == 0:
        if current_value != median:
            return DQFinding(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=DQSeverity.INFO,
                category=DQCategory.OUTLIER,
                message=f"Value {current_value} differs from constant historical value {median}",
                org_unit=org_unit,
                period=period,
                indicator_id=indicator_id,
                current_value=current_value,
                expected_range=f"~{median}",
                recommendation="Historical values were constant. Verify this change is real.",
            )
        return None

    threshold = float(rule.params.get("mad_threshold", 3.5))
    modified_z = 0.6745 * (current_value - median) / mad
    if abs(modified_z) > threshold:
        direction = "high" if modified_z > 0 else "low"
        range_floor = median - threshold * mad
        range_ceiling = median + threshold * mad
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.WARNING,
            category=DQCategory.OUTLIER,
            message=f"Outlier detected: {current_value} is unusually {direction} (Z={modified_z:.1f})",
            org_unit=org_unit,
            period=period,
            indicator_id=indicator_id,
            current_value=current_value,
            expected_range=f"{range_floor:.1f} - {range_ceiling:.1f}",
            recommendation=f"Value is {abs(modified_z):.1f} MAD from median. Verify data entry.",
            metadata={"median": median, "mad": mad, "z_score": modified_z},
        )

    return None


def check_cascade_consistency(
    upstream_value: Optional[float],
    downstream_value: Optional[float],
    org_unit: str,
    period: str,
    upstream_indicator: str,
    downstream_indicator: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Ensure downstream cascade steps do not exceed upstream ones."""
    if upstream_value is None or downstream_value is None:
        return None

    if upstream_value < 0 or downstream_value < 0:
        return None

    if downstream_value > upstream_value:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.WARNING,
            category=DQCategory.CASCADE,
            message=(
                f"{downstream_indicator} ({downstream_value}) exceeds "
                f"{upstream_indicator} ({upstream_value})"
            ),
            org_unit=org_unit,
            period=period,
            indicator_id=downstream_indicator,
            current_value=downstream_value,
            expected_range=f"<= {upstream_value}",
            recommendation="Downstream indicator cannot exceed upstream. Check both values.",
            metadata={
                "upstream_indicator": upstream_indicator,
                "upstream_value": upstream_value,
                "downstream_indicator": downstream_indicator,
                "downstream_value": downstream_value,
            },
        )

    return None


def check_repeated_values(
    values: List[Optional[float]],
    periods: List[str],
    org_unit: str,
    indicator_id: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Check for repeated identical values across consecutive periods."""
    valid_values = [(value, item_period) for value, item_period in zip(values, periods) if value is not None]
    if len(valid_values) < 3:
        return None

    min_repeats = int(rule.params.get("min_repeats", 3))
    consecutive_count = 1
    last_value = valid_values[0][0]

    for value, _period in valid_values[1:]:
        if value == last_value:
            consecutive_count += 1
        else:
            consecutive_count = 1
            last_value = value

    if consecutive_count >= min_repeats:
        repeated_periods = [item_period for _, item_period in valid_values[-consecutive_count:]]
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.INFO,
            category=DQCategory.CONSISTENCY,
            message=f"Identical value ({last_value}) repeated for {consecutive_count} periods",
            org_unit=org_unit,
            period=valid_values[-1][1],
            indicator_id=indicator_id,
            current_value=last_value,
            recommendation="Repeated identical values may indicate copy-forward reporting. Verify accuracy.",
            metadata={"repeat_count": consecutive_count, "periods": repeated_periods},
        )

    return None


def check_supply_service_reconciliation(
    service_value: Optional[float],
    supply_value: Optional[float],
    org_unit: str,
    period: str,
    service_indicator: str,
    supply_indicator: str,
    rule: DQRule,
) -> Optional[DQFinding]:
    """Ensure reported service volume is plausible relative to commodity use."""
    if service_value is None or supply_value is None:
        return None

    tolerance = float(rule.params.get("tolerance_percent", 10.0))
    max_allowed = supply_value * (1 + tolerance / 100)
    if service_value > max_allowed:
        return DQFinding(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=DQSeverity.WARNING,
            category=DQCategory.RECONCILIATION,
            message=(
                f"Service volume ({service_value}) materially exceeds matched supply "
                f"consumption ({supply_value})"
            ),
            org_unit=org_unit,
            period=period,
            indicator_id=service_indicator,
            current_value=service_value,
            expected_range=f"<= {max_allowed:.1f}",
            recommendation=(
                f"Review {service_indicator} against {supply_indicator}. Service counts "
                "should not greatly exceed consumed commodities."
            ),
            metadata={
                "service_indicator": service_indicator,
                "service_value": service_value,
                "supply_indicator": supply_indicator,
                "supply_value": supply_value,
            },
        )

    return None
