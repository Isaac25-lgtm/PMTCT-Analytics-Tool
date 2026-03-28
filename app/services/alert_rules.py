"""
Alert rule definitions and reusable formatting helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from app.indicators.models import IndicatorResult


class AlertSeverity(str, Enum):
    """Severity levels for alerting."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertType(str, Enum):
    """Supported alert types."""

    CRITICAL_BELOW_TARGET = "critical_below_target"
    BELOW_TARGET = "below_target"
    ABOVE_THRESHOLD = "above_threshold"
    STOCKOUT = "stockout"
    IMMINENT_STOCKOUT = "imminent_stockout"
    LOW_STOCK = "low_stock"
    OVERSTOCK = "overstock"
    STOCKOUT_DAYS = "stockout_days"
    LOW_COMPLETENESS = "low_completeness"
    DATA_QUALITY_CRITICAL = "data_quality_critical"
    DATA_QUALITY_WARNING = "data_quality_warning"


class AlertCategory(str, Enum):
    """Top-level grouping for alerts."""

    INDICATOR = "indicator"
    SUPPLY = "supply"
    SYSTEM = "system"
    DATA_QUALITY = "data_quality"


@dataclass(slots=True)
class Alert:
    """A single alert instance."""

    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    category: AlertCategory
    title: str
    message: str
    org_unit: str
    period: str
    indicator_id: Optional[str] = None
    current_value: Optional[float] = None
    threshold_value: Optional[float] = None
    target_value: Optional[float] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.alert_id:
            self.alert_id = uuid4().hex[:8]

    def acknowledge(self) -> None:
        """Mark this alert as acknowledged."""
        self.acknowledged = True
        self.acknowledged_at = datetime.now(UTC)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title,
            "message": self.message,
            "org_unit": self.org_unit,
            "period": self.period,
            "indicator_id": self.indicator_id,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "target_value": self.target_value,
            "created_at": self.created_at.isoformat(),
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class AlertThreshold:
    """Configuration record for a threshold-based alert."""

    threshold_id: str
    name: str
    description: str
    indicator_ids: list[str]
    alert_type: AlertType
    severity: AlertSeverity
    category: AlertCategory
    operator: str
    value: Optional[float] = None
    use_target: bool = False
    target_multiplier: float = 1.0
    value_source: str = "result_value"
    enabled: bool = True

    def observed_value(self, result: IndicatorResult) -> Optional[float]:
        """Read the configured comparison source from an indicator result."""
        source = (self.value_source or "result_value").strip()
        value = getattr(result, source, None)
        return float(value) if value is not None else None

    def comparison_value(self, target: Optional[float] = None) -> Optional[float]:
        """Resolve the threshold comparison value."""
        if self.use_target:
            if target is None:
                return None
            return float(target) * float(self.target_multiplier)
        if self.value is None:
            return None
        return float(self.value)

    def evaluate(self, observed_value: Optional[float], target: Optional[float] = None) -> bool:
        """Return True when the threshold condition is breached."""
        if observed_value is None:
            return False

        compare_value = self.comparison_value(target)
        if compare_value is None:
            return False

        if self.operator == "lt":
            return observed_value < compare_value
        if self.operator == "lte":
            return observed_value <= compare_value
        if self.operator == "gt":
            return observed_value > compare_value
        if self.operator == "gte":
            return observed_value >= compare_value
        if self.operator == "eq":
            return observed_value == compare_value
        return False


ALERT_TEMPLATES: dict[AlertType, dict[str, str]] = {
    AlertType.CRITICAL_BELOW_TARGET: {
        "title": "{indicator_name} critically below target",
        "message": (
            "{indicator_name} is at {value:.1f}%, below 70% of the "
            "{target:.0f}% target. Immediate attention is required."
        ),
    },
    AlertType.BELOW_TARGET: {
        "title": "{indicator_name} below target",
        "message": "{indicator_name} is at {value:.1f}%, below the {target:.0f}% target.",
    },
    AlertType.ABOVE_THRESHOLD: {
        "title": "{indicator_name} above threshold",
        "message": (
            "{indicator_name} is at {value:.1f}, exceeding the configured threshold "
            "of {threshold:.1f}."
        ),
    },
    AlertType.STOCKOUT: {
        "title": "Stockout: {item_name}",
        "message": "{item_name} stock on hand is zero. Services are at immediate risk.",
    },
    AlertType.IMMINENT_STOCKOUT: {
        "title": "Imminent stockout: {item_name}",
        "message": "{item_name} has only {value:.0f} days of use remaining. Reorder urgently.",
    },
    AlertType.LOW_STOCK: {
        "title": "Low stock: {item_name}",
        "message": "{item_name} has {value:.0f} days of use remaining. Replenishment should be planned.",
    },
    AlertType.OVERSTOCK: {
        "title": "Overstock: {item_name}",
        "message": "{item_name} has {value:.0f} days of use on hand. Review redistribution or expiry risk.",
    },
    AlertType.STOCKOUT_DAYS: {
        "title": "Stockout days reported: {item_name}",
        "message": "{item_name} was out of stock for {value:.0f} day(s) during this period.",
    },
    AlertType.LOW_COMPLETENESS: {
        "title": "Low reporting completeness",
        "message": "Reporting completeness is {value:.1f}%, below the {threshold:.0f}% threshold.",
    },
    AlertType.DATA_QUALITY_CRITICAL: {
        "title": "Critical data-quality issues",
        "message": "{count} critical data-quality issue(s) were detected. Review before interpreting the results.",
    },
    AlertType.DATA_QUALITY_WARNING: {
        "title": "Data-quality warnings",
        "message": "{count} warning-level data-quality issue(s) were detected. Follow-up is recommended.",
    },
}


def format_alert_message(alert_type: AlertType, **kwargs: Any) -> tuple[str, str]:
    """Format a human-readable title and message for an alert."""
    template = ALERT_TEMPLATES.get(alert_type)
    if template is None:
        fallback = alert_type.value.replace("_", " ").title()
        return fallback, f"{fallback} was triggered."

    try:
        return template["title"].format(**kwargs), template["message"].format(**kwargs)
    except (KeyError, ValueError):
        fallback = alert_type.value.replace("_", " ").title()
        return fallback, f"{fallback} was triggered."
