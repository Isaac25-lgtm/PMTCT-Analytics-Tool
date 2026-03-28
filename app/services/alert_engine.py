"""
Monthly alert evaluation built on top of the Prompt 3 calculator.

Prompt 10 is intentionally monthly-only for now. The alerts dashboard and
threshold engine evaluate monthly indicator results plus monthly DQ summaries.
Weekly indicators such as SYS-03 remain available elsewhere in the app, but are
not included here until the UI grows a weekly frequency selector.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

from app.core.config import load_yaml_config
from app.indicators.calculator import IndicatorCalculator
from app.indicators.models import IndicatorDefinition, IndicatorResult, Periodicity
from app.indicators.registry import get_indicator_registry
from app.services.alert_rules import (
    Alert,
    AlertCategory,
    AlertSeverity,
    AlertThreshold,
    AlertType,
    format_alert_message,
)
from app.services.data_quality import DataQualityEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AlertSummary:
    """Summary counts for an alert evaluation."""

    org_unit: str
    period: str
    total_alerts: int = 0
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    acknowledged_count: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)

    @property
    def unacknowledged_count(self) -> int:
        return self.total_alerts - self.acknowledged_count

    @classmethod
    def from_alerts(
        cls,
        org_unit: str,
        period: str,
        alerts: Sequence[Alert],
    ) -> "AlertSummary":
        """Build summary counts from a sequence of alerts."""
        summary = cls(org_unit=org_unit, period=period)
        for alert in alerts:
            summary.total_alerts += 1
            if alert.severity == AlertSeverity.CRITICAL:
                summary.critical_count += 1
            elif alert.severity == AlertSeverity.WARNING:
                summary.warning_count += 1
            else:
                summary.info_count += 1

            if alert.acknowledged:
                summary.acknowledged_count += 1

            key = alert.category.value
            summary.by_category[key] = summary.by_category.get(key, 0) + 1
        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "org_unit": self.org_unit,
            "period": self.period,
            "total_alerts": self.total_alerts,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "acknowledged_count": self.acknowledged_count,
            "unacknowledged_count": self.unacknowledged_count,
            "by_category": self.by_category,
        }


@dataclass(slots=True)
class AlertResult:
    """Complete result of an alert evaluation."""

    org_unit: str
    period: str
    evaluated_at: datetime
    alerts: List[Alert] = field(default_factory=list)
    summary: AlertSummary = field(init=False)

    def __post_init__(self) -> None:
        self.summary = AlertSummary.from_alerts(self.org_unit, self.period, self.alerts)

    def add_alert(self, alert: Alert) -> None:
        """Append an alert and refresh the summary."""
        self.alerts.append(alert)
        self.summary = AlertSummary.from_alerts(self.org_unit, self.period, self.alerts)

    def filtered(
        self,
        *,
        severity: AlertSeverity | None = None,
        category: AlertCategory | None = None,
        include_acknowledged: bool = True,
    ) -> "AlertResult":
        """Return a filtered copy with a recomputed summary."""
        alerts = self.alerts
        if severity is not None:
            alerts = [alert for alert in alerts if alert.severity == severity]
        if category is not None:
            alerts = [alert for alert in alerts if alert.category == category]
        if not include_acknowledged:
            alerts = [alert for alert in alerts if not alert.acknowledged]
        return AlertResult(
            org_unit=self.org_unit,
            period=self.period,
            evaluated_at=self.evaluated_at,
            alerts=list(alerts),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "org_unit": self.org_unit,
            "period": self.period,
            "evaluated_at": self.evaluated_at.isoformat(),
            "alerts": [alert.to_dict() for alert in self.alerts],
            "summary": self.summary.to_dict(),
        }


class AlertThresholdLoader:
    """Load configurable alert thresholds from YAML."""

    def __init__(self, config_path: str = "config/alert_thresholds.yaml") -> None:
        self.config_path = config_path
        self._thresholds: Dict[str, AlertThreshold] = {}
        self._loaded = False

    def load(self) -> None:
        """Load thresholds from YAML or fall back to defaults."""
        self._thresholds = {}
        try:
            config = self._read_config()
            for threshold_data in config.get("thresholds", []):
                threshold = AlertThreshold(
                    threshold_id=threshold_data["id"],
                    name=threshold_data["name"],
                    description=threshold_data.get("description", ""),
                    indicator_ids=list(threshold_data.get("indicator_ids", [])),
                    alert_type=AlertType(threshold_data["alert_type"]),
                    severity=AlertSeverity(threshold_data["severity"]),
                    category=AlertCategory(threshold_data["category"]),
                    operator=threshold_data["operator"],
                    value=threshold_data.get("value"),
                    use_target=threshold_data.get("use_target", False),
                    target_multiplier=threshold_data.get("target_multiplier", 1.0),
                    value_source=threshold_data.get("value_source", "result_value"),
                    enabled=threshold_data.get("enabled", True),
                )
                self._thresholds[threshold.threshold_id] = threshold
            self._loaded = True
            logger.info("Loaded %d alert thresholds from %s", len(self._thresholds), self.config_path)
        except FileNotFoundError:
            logger.warning("Alert thresholds file not found at %s. Using defaults.", self.config_path)
            self._load_defaults()
        except Exception as exc:
            logger.warning("Failed to load alert thresholds from %s: %s. Using defaults.", self.config_path, exc)
            self._load_defaults()

    def _read_config(self) -> Dict[str, Any]:
        """Read YAML configuration from the repo config directory or explicit path."""
        config_path = Path(self.config_path)
        if config_path.parts == ("config", config_path.name):
            return load_yaml_config(config_path.name) or {}
        with config_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _load_defaults(self) -> None:
        """Populate default thresholds that match the current indicator registry."""
        defaults = [
            AlertThreshold(
                threshold_id="WHO-CRITICAL",
                name="WHO Indicator Critically Below Target",
                description="WHO validation indicator below 70% of its configured target.",
                indicator_ids=["VAL-01", "VAL-02", "VAL-03", "VAL-04", "VAL-05", "VAL-06"],
                alert_type=AlertType.CRITICAL_BELOW_TARGET,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.INDICATOR,
                operator="lt",
                use_target=True,
                target_multiplier=0.7,
            ),
            AlertThreshold(
                threshold_id="WHO-WARNING",
                name="WHO Indicator Below Target",
                description="WHO validation indicator below target.",
                indicator_ids=["VAL-01", "VAL-02", "VAL-03", "VAL-04", "VAL-05", "VAL-06"],
                alert_type=AlertType.BELOW_TARGET,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.INDICATOR,
                operator="lt",
                use_target=True,
                target_multiplier=1.0,
            ),
            AlertThreshold(
                threshold_id="HBV-TREATMENT",
                name="HBV Treatment Below Target",
                description="HBV treatment initiation below target.",
                indicator_ids=["HBV-05"],
                alert_type=AlertType.BELOW_TARGET,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.INDICATOR,
                operator="lt",
                use_target=True,
            ),
            AlertThreshold(
                threshold_id="HIV-VL-SUPPRESSION",
                name="Low Viral Load Suppression",
                description="Viral load suppression below 90%.",
                indicator_ids=["HIV-05"],
                alert_type=AlertType.BELOW_TARGET,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.INDICATOR,
                operator="lt",
                value=90.0,
            ),
            AlertThreshold(
                threshold_id="HIV-EID-COVERAGE",
                name="Low EID Coverage",
                description="Early infant diagnosis below 80%.",
                indicator_ids=["HIV-07"],
                alert_type=AlertType.BELOW_TARGET,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.INDICATOR,
                operator="lt",
                value=80.0,
            ),
            AlertThreshold(
                threshold_id="SUPPLY-STOCKOUT",
                name="Stockout",
                description="Derived stock on hand is zero.",
                indicator_ids=["SUP-05", "SUP-06"],
                alert_type=AlertType.STOCKOUT,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.SUPPLY,
                operator="eq",
                value=0.0,
                value_source="numerator_value",
            ),
            AlertThreshold(
                threshold_id="SUPPLY-IMMINENT",
                name="Imminent Stockout",
                description="Days of use below 7 days.",
                indicator_ids=["SUP-05", "SUP-06"],
                alert_type=AlertType.IMMINENT_STOCKOUT,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.SUPPLY,
                operator="lt",
                value=7.0,
            ),
            AlertThreshold(
                threshold_id="SUPPLY-LOW",
                name="Low Stock",
                description="Days of use below 30 days.",
                indicator_ids=["SUP-05", "SUP-06"],
                alert_type=AlertType.LOW_STOCK,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.SUPPLY,
                operator="lt",
                value=30.0,
            ),
            AlertThreshold(
                threshold_id="SUPPLY-OVERSTOCK",
                name="Overstock",
                description="Days of use above 180 days.",
                indicator_ids=["SUP-05", "SUP-06"],
                alert_type=AlertType.OVERSTOCK,
                severity=AlertSeverity.INFO,
                category=AlertCategory.SUPPLY,
                operator="gt",
                value=180.0,
            ),
            AlertThreshold(
                threshold_id="SUPPLY-STOCKOUT-DAYS",
                name="Stockout Days Reported",
                description="Reported stockout days are greater than zero.",
                indicator_ids=["SUP-02", "SUP-04"],
                alert_type=AlertType.STOCKOUT_DAYS,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.SUPPLY,
                operator="gt",
                value=0.0,
            ),
            AlertThreshold(
                threshold_id="SYS-COMPLETENESS",
                name="Low Reporting Completeness",
                description="Monthly reporting completeness below 80%.",
                indicator_ids=["SYS-01"],
                alert_type=AlertType.LOW_COMPLETENESS,
                severity=AlertSeverity.WARNING,
                category=AlertCategory.SYSTEM,
                operator="lt",
                value=80.0,
            ),
        ]
        for threshold in defaults:
            self._thresholds[threshold.threshold_id] = threshold
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def get_threshold(self, threshold_id: str) -> Optional[AlertThreshold]:
        """Return a single threshold by id."""
        self._ensure_loaded()
        return self._thresholds.get(threshold_id)

    def get_all_thresholds(self) -> List[AlertThreshold]:
        """Return all configured thresholds."""
        self._ensure_loaded()
        return list(self._thresholds.values())

    def get_enabled_thresholds(self) -> List[AlertThreshold]:
        """Return enabled thresholds only."""
        self._ensure_loaded()
        return [threshold for threshold in self._thresholds.values() if threshold.enabled]

    def get_for_indicator(self, indicator_id: str) -> List[AlertThreshold]:
        """Return thresholds applicable to a single indicator id."""
        return [
            threshold
            for threshold in self.get_enabled_thresholds()
            if indicator_id in threshold.indicator_ids
        ]


class AlertEngine:
    """Evaluate monthly alerts from calculator output and Prompt 9 DQ summaries."""

    SUPPLY_ITEMS = {
        "SUP-01": "HBsAg kits",
        "SUP-02": "HBsAg kits",
        "SUP-03": "HIV/Syphilis duo kits",
        "SUP-04": "HIV/Syphilis duo kits",
        "SUP-05": "HBsAg kits",
        "SUP-06": "HIV/Syphilis duo kits",
    }

    def __init__(
        self,
        calculator: IndicatorCalculator,
        threshold_loader: Optional[AlertThresholdLoader] = None,
    ) -> None:
        self.calculator = calculator
        self.threshold_loader = threshold_loader or AlertThresholdLoader()
        self.registry = get_indicator_registry()
        self._acknowledged: set[str] = set()

    async def evaluate_alerts(
        self,
        org_unit: str,
        period: str,
        include_dq: bool = True,
    ) -> AlertResult:
        """
        Evaluate monthly indicator alerts for an org unit and monthly period.

        Weekly alerting is intentionally out of scope for Prompt 10.
        """
        self._validate_monthly_period(period)
        result_set = await self.calculator.calculate_all(org_unit=org_unit, period=period)
        results_lookup = {result.indicator_id: result for result in result_set.results}

        alert_result = AlertResult(
            org_unit=org_unit,
            period=period,
            evaluated_at=datetime.now(UTC),
        )

        for indicator_id, indicator_result in results_lookup.items():
            indicator = self.registry.get(indicator_id)
            if indicator is None or indicator.periodicity != Periodicity.MONTHLY:
                continue

            candidate_alerts = self._evaluate_indicator_thresholds(
                indicator=indicator,
                result=indicator_result,
                org_unit=org_unit,
                period=period,
            )
            for alert in self._suppress_duplicate_alerts(candidate_alerts):
                if alert.alert_id in self._acknowledged:
                    alert.acknowledge()
                alert_result.add_alert(alert)

        if include_dq:
            for dq_alert in await self._evaluate_dq_alerts(org_unit=org_unit, period=period):
                if dq_alert.alert_id in self._acknowledged:
                    dq_alert.acknowledge()
                alert_result.add_alert(dq_alert)

        return alert_result

    def _evaluate_indicator_thresholds(
        self,
        *,
        indicator: IndicatorDefinition,
        result: IndicatorResult,
        org_unit: str,
        period: str,
    ) -> list[Alert]:
        """Evaluate all configured thresholds for a single indicator result."""
        alerts: list[Alert] = []
        target = result.target if result.target is not None else indicator.target

        for threshold in self.threshold_loader.get_for_indicator(indicator.id):
            observed_value = threshold.observed_value(result)
            if not threshold.evaluate(observed_value, target):
                continue
            alerts.append(
                self._create_alert(
                    threshold=threshold,
                    indicator=indicator,
                    observed_value=observed_value,
                    target=target,
                    org_unit=org_unit,
                    period=period,
                )
            )
        return alerts

    @staticmethod
    def _suppress_duplicate_alerts(alerts: Iterable[Alert]) -> list[Alert]:
        """Remove lower-priority duplicates for the same indicator."""
        alerts = list(alerts)
        triggered = {alert.alert_type for alert in alerts}
        filtered: list[Alert] = []
        for alert in alerts:
            if alert.alert_type == AlertType.BELOW_TARGET and AlertType.CRITICAL_BELOW_TARGET in triggered:
                continue
            if alert.alert_type == AlertType.IMMINENT_STOCKOUT and AlertType.STOCKOUT in triggered:
                continue
            if alert.alert_type == AlertType.LOW_STOCK and (
                AlertType.STOCKOUT in triggered or AlertType.IMMINENT_STOCKOUT in triggered
            ):
                continue
            filtered.append(alert)
        return filtered

    def _create_alert(
        self,
        *,
        threshold: AlertThreshold,
        indicator: IndicatorDefinition,
        observed_value: Optional[float],
        target: Optional[float],
        org_unit: str,
        period: str,
    ) -> Alert:
        """Create a stable alert instance for one breached threshold."""
        item_name = self.SUPPLY_ITEMS.get(indicator.id, indicator.name)
        threshold_value = threshold.comparison_value(target)
        display_target = target if target is not None else threshold_value
        title, message = format_alert_message(
            threshold.alert_type,
            indicator_name=indicator.name,
            item_name=item_name,
            value=observed_value if observed_value is not None else 0.0,
            threshold=threshold_value if threshold_value is not None else 0.0,
            target=display_target if display_target is not None else 0.0,
        )
        alert_id = f"{org_unit[:8]}-{period}-{indicator.id}-{threshold.threshold_id}"
        return Alert(
            alert_id=alert_id,
            alert_type=threshold.alert_type,
            severity=threshold.severity,
            category=threshold.category,
            title=title,
            message=message,
            org_unit=org_unit,
            period=period,
            indicator_id=indicator.id,
            current_value=observed_value,
            threshold_value=threshold_value,
            target_value=target,
            metadata={
                "threshold_id": threshold.threshold_id,
                "value_source": threshold.value_source,
            },
        )

    async def _evaluate_dq_alerts(self, org_unit: str, period: str) -> list[Alert]:
        """Convert Prompt 9 DQ summary counts into alert items."""
        dq_engine = DataQualityEngine(calculator=self.calculator)
        dq_result = await dq_engine.run_checks(
            org_unit=org_unit,
            period=period,
            include_historical=False,
        )

        alerts: list[Alert] = []
        if dq_result.summary.critical_count > 0:
            title, message = format_alert_message(
                AlertType.DATA_QUALITY_CRITICAL,
                count=dq_result.summary.critical_count,
            )
            alerts.append(
                Alert(
                    alert_id=f"{org_unit[:8]}-{period}-DQ-CRITICAL",
                    alert_type=AlertType.DATA_QUALITY_CRITICAL,
                    severity=AlertSeverity.CRITICAL,
                    category=AlertCategory.DATA_QUALITY,
                    title=title,
                    message=message,
                    org_unit=org_unit,
                    period=period,
                    metadata={"dq_critical_count": dq_result.summary.critical_count},
                )
            )

        if dq_result.summary.warning_count > 0:
            title, message = format_alert_message(
                AlertType.DATA_QUALITY_WARNING,
                count=dq_result.summary.warning_count,
            )
            alerts.append(
                Alert(
                    alert_id=f"{org_unit[:8]}-{period}-DQ-WARNING",
                    alert_type=AlertType.DATA_QUALITY_WARNING,
                    severity=AlertSeverity.WARNING,
                    category=AlertCategory.DATA_QUALITY,
                    title=title,
                    message=message,
                    org_unit=org_unit,
                    period=period,
                    metadata={"dq_warning_count": dq_result.summary.warning_count},
                )
            )

        return alerts

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert id as acknowledged for this session-scoped engine."""
        self._acknowledged.add(alert_id)
        return True

    def get_acknowledged_alerts(self) -> set[str]:
        """Return the session-scoped acknowledged alert ids."""
        return set(self._acknowledged)

    def clear_acknowledgments(self) -> None:
        """Clear all session-scoped acknowledgements."""
        self._acknowledged.clear()

    @staticmethod
    def _is_monthly_period(period: str) -> bool:
        """Return True for valid YYYYMM monthly periods."""
        normalized = str(period).strip()
        if len(normalized) != 6 or not normalized.isdigit():
            return False
        year = int(normalized[:4])
        month = int(normalized[4:6])
        return 2000 <= year <= 2100 and 1 <= month <= 12

    @classmethod
    def _validate_monthly_period(cls, period: str) -> None:
        """Raise ValueError for unsupported alert periods."""
        if not cls._is_monthly_period(period):
            raise ValueError(
                "Alerts currently support monthly DHIS2 periods only (valid YYYYMM values)."
            )
