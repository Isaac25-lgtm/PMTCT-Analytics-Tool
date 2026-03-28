"""
Data-quality engine built on top of the existing indicator calculator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from app.core.config import load_yaml_config
from app.indicators.calculator import IndicatorCalculator
from app.indicators.models import IndicatorDefinition, IndicatorResult, Periodicity, ResultType
from app.indicators.registry import get_indicator_registry
from app.services.dq_rules import (
    DQCategory,
    DQFinding,
    DQRule,
    DQSeverity,
    check_cascade_consistency,
    check_negative_value,
    check_numerator_exceeds_denominator,
    check_outlier_mad,
    check_percentage_bounds,
    check_repeated_values,
    check_supply_service_reconciliation,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DQResultSummary:
    """Summary counts for a DQ run."""

    total_checks: int = 0
    passed: int = 0
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    @property
    def failed(self) -> int:
        return self.critical_count + self.warning_count + self.info_count

    @property
    def pass_rate(self) -> float:
        if self.total_checks == 0:
            return 100.0
        return (self.passed / self.total_checks) * 100

    @property
    def has_critical(self) -> bool:
        return self.critical_count > 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "total_checks": self.total_checks,
            "passed": self.passed,
            "failed": self.failed,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "pass_rate": round(self.pass_rate, 1),
            "has_critical": self.has_critical,
        }


@dataclass(slots=True)
class DQResult:
    """Complete result of a DQ run."""

    org_unit: str
    period: str
    checked_at: datetime
    summary: DQResultSummary
    findings: List[DQFinding] = field(default_factory=list)
    indicators_checked: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "org_unit": self.org_unit,
            "period": self.period,
            "checked_at": self.checked_at.isoformat(),
            "summary": self.summary.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "indicators_checked": self.indicators_checked,
        }

    def get_findings_by_severity(self, severity: DQSeverity) -> List[DQFinding]:
        """Return findings filtered by severity."""
        return [finding for finding in self.findings if finding.severity == severity]

    def get_findings_by_category(self, category: DQCategory) -> List[DQFinding]:
        """Return findings filtered by category."""
        return [finding for finding in self.findings if finding.category == category]


class DQRuleLoader:
    """Load DQ rules and pair definitions from YAML."""

    def __init__(self, config_path: str = "config/dq_rules.yaml") -> None:
        self.config_path = config_path
        self._rules: Dict[str, DQRule] = {}
        self._cascade_pairs: list[tuple[str, str]] = []
        self._reconciliation_pairs: list[tuple[str, str]] = []
        self._loaded = False

    def load(self) -> None:
        """Load DQ configuration from YAML, with sensible defaults."""
        self._rules = {}
        self._cascade_pairs = []
        self._reconciliation_pairs = []
        try:
            config = self._read_config()
            for rule_data in config.get("rules", []):
                rule = DQRule(
                    rule_id=rule_data["id"],
                    name=rule_data["name"],
                    description=rule_data.get("description", ""),
                    severity=DQSeverity(rule_data.get("severity", "warning")),
                    category=DQCategory(rule_data["category"]),
                    enabled=rule_data.get("enabled", True),
                    params=rule_data.get("params", {}),
                    applies_to=list(rule_data.get("applies_to", ["ALL"])),
                )
                self._rules[rule.rule_id] = rule

            self._cascade_pairs = self._parse_pair_config(
                config.get("cascade_pairs", []),
                "upstream",
                "downstream",
            )
            self._reconciliation_pairs = self._parse_pair_config(
                config.get("reconciliation_pairs", []),
                "service",
                "supply",
            )
            self._loaded = True
            logger.info("Loaded %d DQ rules from %s", len(self._rules), self.config_path)
        except FileNotFoundError:
            logger.warning("DQ rules file not found at %s. Using defaults.", self.config_path)
            self._load_default_rules()
        except Exception as exc:
            logger.warning("Failed to load DQ rules from %s: %s. Using defaults.", self.config_path, exc)
            self._load_default_rules()

    def _read_config(self) -> Dict[str, Any]:
        """Read YAML configuration from the repo config dir or an explicit path."""
        config_path = Path(self.config_path)
        if config_path.parts == ("config", config_path.name):
            return load_yaml_config(config_path.name) or {}
        with config_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _load_default_rules(self) -> None:
        """Populate default rule and pair definitions."""
        defaults = [
            DQRule(
                rule_id="DQ-001",
                name="Negative Value Check",
                description="Check for negative values in count data.",
                severity=DQSeverity.CRITICAL,
                category=DQCategory.CONSISTENCY,
            ),
            DQRule(
                rule_id="DQ-002",
                name="Percentage Bounds Check",
                description="Check that percentage indicators remain within valid bounds.",
                severity=DQSeverity.WARNING,
                category=DQCategory.CONSISTENCY,
                params={"max_percentage": 105.0},
            ),
            DQRule(
                rule_id="DQ-003",
                name="Numerator Exceeds Denominator",
                description="Check that numerator values do not materially exceed denominators.",
                severity=DQSeverity.WARNING,
                category=DQCategory.CONSISTENCY,
                params={"tolerance_percent": 5.0},
            ),
            DQRule(
                rule_id="DQ-004",
                name="Outlier Detection (MAD)",
                description="Detect outliers from historical monthly values using MAD.",
                severity=DQSeverity.WARNING,
                category=DQCategory.OUTLIER,
                params={"mad_threshold": 3.5, "min_history": 3},
            ),
            DQRule(
                rule_id="DQ-005",
                name="Repeated Identical Values",
                description="Check for repeated identical values across consecutive periods.",
                severity=DQSeverity.INFO,
                category=DQCategory.CONSISTENCY,
                params={"min_repeats": 3},
            ),
            DQRule(
                rule_id="DQ-006",
                name="Cascade Consistency",
                description="Check that downstream cascade indicators do not exceed upstream steps.",
                severity=DQSeverity.WARNING,
                category=DQCategory.CASCADE,
            ),
            DQRule(
                rule_id="DQ-007",
                name="Supply-Service Reconciliation",
                description="Check that reported service volume is plausible relative to matching commodity use.",
                severity=DQSeverity.WARNING,
                category=DQCategory.RECONCILIATION,
                params={"tolerance_percent": 10.0},
            ),
        ]
        for rule in defaults:
            self._rules[rule.rule_id] = rule

        self._cascade_pairs = [
            ("HIV-01", "HIV-02"),
            ("VAL-02", "HIV-02"),
            ("VAL-04", "VAL-05"),
            ("HBV-01", "HBV-02"),
        ]
        self._reconciliation_pairs = [
            ("HBV-01", "SUP-01"),
            ("VAL-02", "SUP-03"),
            ("VAL-04", "SUP-03"),
        ]
        self._loaded = True

    @staticmethod
    def _parse_pair_config(
        pair_items: Sequence[Dict[str, Any]],
        left_key: str,
        right_key: str,
    ) -> list[tuple[str, str]]:
        """Parse pair configuration from YAML."""
        pairs: list[tuple[str, str]] = []
        for item in pair_items:
            left = item.get(left_key)
            right = item.get(right_key)
            if left and right:
                pairs.append((str(left), str(right)))
        return pairs

    def _ensure_loaded(self) -> None:
        """Lazy-load the configuration the first time it is needed."""
        if not self._loaded:
            self.load()

    def get_rule(self, rule_id: str) -> Optional[DQRule]:
        """Return a single rule by id."""
        self._ensure_loaded()
        return self._rules.get(rule_id)

    def get_all_rules(self) -> List[DQRule]:
        """Return all configured rules."""
        self._ensure_loaded()
        return list(self._rules.values())

    def get_enabled_rules(self) -> List[DQRule]:
        """Return enabled rules only."""
        self._ensure_loaded()
        return [rule for rule in self._rules.values() if rule.enabled]

    def get_rules_for_indicator(self, indicator_id: str) -> List[DQRule]:
        """Return rules that apply to a given indicator id."""
        return [
            rule
            for rule in self.get_enabled_rules()
            if "ALL" in rule.applies_to or indicator_id in rule.applies_to
        ]

    def get_cascade_pairs(self) -> list[tuple[str, str]]:
        """Return configured cascade relationships."""
        self._ensure_loaded()
        return list(self._cascade_pairs)

    def get_reconciliation_pairs(self) -> list[tuple[str, str]]:
        """Return configured supply-service reconciliation pairs."""
        self._ensure_loaded()
        return list(self._reconciliation_pairs)


class DataQualityEngine:
    """Run DQ rules against existing calculator output."""

    def __init__(
        self,
        calculator: IndicatorCalculator,
        rule_loader: Optional[DQRuleLoader] = None,
    ) -> None:
        self.calculator = calculator
        self.rule_loader = rule_loader or DQRuleLoader()
        self.registry = get_indicator_registry()

    async def run_checks(
        self,
        org_unit: str,
        period: str,
        indicator_ids: Optional[List[str]] = None,
        include_historical: bool = True,
        historical_periods: int = 6,
    ) -> DQResult:
        """Run configured DQ checks for the selected org unit and period."""
        indicators = self._resolve_indicators(indicator_ids)
        current_results = await self.calculator.calculate_all(org_unit=org_unit, period=period)
        results_lookup = {result.indicator_id: result for result in current_results.results}

        summary = DQResultSummary()
        findings: list[DQFinding] = []
        checked_indicators = sorted({indicator.id for indicator in indicators})

        for indicator in indicators:
            result = results_lookup.get(indicator.id)
            for rule in self.rule_loader.get_rules_for_indicator(indicator.id):
                if rule.rule_id in {"DQ-004", "DQ-005", "DQ-006", "DQ-007"}:
                    continue
                summary.total_checks += 1
                finding = self._run_rule(
                    rule=rule,
                    indicator=indicator,
                    result=result,
                    org_unit=org_unit,
                    period=period,
                )
                self._record_check(summary, findings, finding)

        cascade_findings, cascade_checks = self._check_cascades(
            org_unit=org_unit,
            period=period,
            results_lookup=results_lookup,
        )
        self._record_bulk_checks(summary, findings, cascade_findings, cascade_checks)

        reconciliation_findings, reconciliation_checks = self._check_supply_service_reconciliation(
            org_unit=org_unit,
            period=period,
            results_lookup=results_lookup,
        )
        self._record_bulk_checks(
            summary,
            findings,
            reconciliation_findings,
            reconciliation_checks,
        )

        if include_historical:
            historical_findings, historical_checks = await self._check_historical_patterns(
                org_unit=org_unit,
                current_period=period,
                historical_periods=historical_periods,
                results_lookup=results_lookup,
            )
            self._record_bulk_checks(summary, findings, historical_findings, historical_checks)

        severity_order = {
            DQSeverity.CRITICAL: 0,
            DQSeverity.WARNING: 1,
            DQSeverity.INFO: 2,
        }
        findings.sort(
            key=lambda finding: (
                severity_order[finding.severity],
                finding.category.value,
                finding.rule_id,
                finding.indicator_id or "",
            )
        )

        return DQResult(
            org_unit=org_unit,
            period=period,
            checked_at=datetime.now(UTC),
            summary=summary,
            findings=findings,
            indicators_checked=checked_indicators,
        )

    def _resolve_indicators(self, indicator_ids: Optional[List[str]]) -> list[IndicatorDefinition]:
        """Resolve indicator definitions from the registry."""
        if indicator_ids:
            return [
                indicator
                for indicator_id in indicator_ids
                if (indicator := self.registry.get(indicator_id)) is not None
            ]
        return self.registry.get_all()

    @staticmethod
    def _record_check(
        summary: DQResultSummary,
        findings: list[DQFinding],
        finding: Optional[DQFinding],
    ) -> None:
        """Update summary counts from a single executed check."""
        if finding is None:
            summary.passed += 1
            return
        findings.append(finding)
        if finding.severity == DQSeverity.CRITICAL:
            summary.critical_count += 1
        elif finding.severity == DQSeverity.WARNING:
            summary.warning_count += 1
        else:
            summary.info_count += 1

    def _record_bulk_checks(
        self,
        summary: DQResultSummary,
        findings: list[DQFinding],
        new_findings: list[DQFinding],
        checks_run: int,
    ) -> None:
        """Record summary counts for a batch of executed checks."""
        if checks_run <= 0:
            return
        summary.total_checks += checks_run
        summary.passed += max(checks_run - len(new_findings), 0)
        for finding in new_findings:
            self._record_check(summary, findings, finding)

    def _run_rule(
        self,
        rule: DQRule,
        indicator: IndicatorDefinition,
        result: Optional[IndicatorResult],
        org_unit: str,
        period: str,
    ) -> Optional[DQFinding]:
        """Run one indicator-level rule against a result."""
        if rule.rule_id == "DQ-001":
            if result and result.numerator_value is not None:
                return check_negative_value(
                    value=result.numerator_value,
                    org_unit=org_unit,
                    period=period,
                    indicator_id=indicator.id,
                    rule=rule,
                )
            return None

        if rule.rule_id == "DQ-002":
            if result and result.result_value is not None and indicator.result_type == ResultType.PERCENTAGE:
                return check_percentage_bounds(
                    value=result.result_value,
                    org_unit=org_unit,
                    period=period,
                    indicator_id=indicator.id,
                    rule=rule,
                )
            return None

        if rule.rule_id == "DQ-003" and result:
            return check_numerator_exceeds_denominator(
                numerator=result.numerator_value,
                denominator=result.denominator_value,
                org_unit=org_unit,
                period=period,
                indicator_id=indicator.id,
                rule=rule,
            )

        return None

    def _check_cascades(
        self,
        org_unit: str,
        period: str,
        results_lookup: Dict[str, IndicatorResult],
    ) -> tuple[list[DQFinding], int]:
        """Run configured cascade consistency checks."""
        rule = self.rule_loader.get_rule("DQ-006")
        if not rule or not rule.enabled:
            return [], 0

        findings: list[DQFinding] = []
        checks_run = 0
        for upstream_id, downstream_id in self.rule_loader.get_cascade_pairs():
            upstream = results_lookup.get(upstream_id)
            downstream = results_lookup.get(downstream_id)
            if upstream is None or downstream is None:
                continue
            checks_run += 1
            finding = check_cascade_consistency(
                upstream_value=upstream.numerator_value,
                downstream_value=downstream.numerator_value,
                org_unit=org_unit,
                period=period,
                upstream_indicator=upstream_id,
                downstream_indicator=downstream_id,
                rule=rule,
            )
            if finding is not None:
                findings.append(finding)
        return findings, checks_run

    def _check_supply_service_reconciliation(
        self,
        org_unit: str,
        period: str,
        results_lookup: Dict[str, IndicatorResult],
    ) -> tuple[list[DQFinding], int]:
        """Reconcile service activity against matching commodity consumption."""
        rule = self.rule_loader.get_rule("DQ-007")
        if not rule or not rule.enabled:
            return [], 0

        findings: list[DQFinding] = []
        checks_run = 0
        for service_id, supply_id in self.rule_loader.get_reconciliation_pairs():
            service_result = results_lookup.get(service_id)
            supply_result = results_lookup.get(supply_id)
            if service_result is None or supply_result is None:
                continue
            checks_run += 1
            finding = check_supply_service_reconciliation(
                service_value=service_result.numerator_value,
                supply_value=supply_result.result_value,
                org_unit=org_unit,
                period=period,
                service_indicator=service_id,
                supply_indicator=supply_id,
                rule=rule,
            )
            if finding is not None:
                findings.append(finding)
        return findings, checks_run

    async def _check_historical_patterns(
        self,
        org_unit: str,
        current_period: str,
        historical_periods: int,
        results_lookup: Dict[str, IndicatorResult],
    ) -> tuple[list[DQFinding], int]:
        """Run outlier and repeated-value checks using historical monthly data."""
        if not self._is_monthly_period(current_period):
            return [], 0

        outlier_rule = self.rule_loader.get_rule("DQ-004")
        repeated_rule = self.rule_loader.get_rule("DQ-005")
        if (not outlier_rule or not outlier_rule.enabled) and (not repeated_rule or not repeated_rule.enabled):
            return [], 0

        historical_period_labels = self._generate_historical_periods_monthly(current_period, historical_periods)
        findings: list[DQFinding] = []
        checks_run = 0

        for indicator_id, current_result in results_lookup.items():
            if current_result.result_value is None:
                continue

            indicator = self.registry.get(indicator_id)
            if indicator is None or indicator.periodicity == Periodicity.WEEKLY:
                continue

            historical_values, observed_periods = await self._fetch_historical_values(
                indicator_id=indicator_id,
                org_unit=org_unit,
                periods=historical_period_labels,
            )

            if outlier_rule and outlier_rule.enabled:
                min_history = int(outlier_rule.params.get("min_history", 3))
                if len(historical_values) >= min_history:
                    checks_run += 1
                    finding = check_outlier_mad(
                        current_value=current_result.result_value,
                        historical_values=historical_values,
                        org_unit=org_unit,
                        period=current_period,
                        indicator_id=indicator_id,
                        rule=outlier_rule,
                    )
                    if finding is not None:
                        findings.append(finding)

            if repeated_rule and repeated_rule.enabled:
                all_values = historical_values + [current_result.result_value]
                all_periods = observed_periods + [current_period]
                min_repeats = int(repeated_rule.params.get("min_repeats", 3))
                if len([value for value in all_values if value is not None]) >= min_repeats:
                    checks_run += 1
                    finding = check_repeated_values(
                        values=all_values,
                        periods=all_periods,
                        org_unit=org_unit,
                        indicator_id=indicator_id,
                        rule=repeated_rule,
                    )
                    if finding is not None:
                        findings.append(finding)

        return findings, checks_run

    async def _fetch_historical_values(
        self,
        indicator_id: str,
        org_unit: str,
        periods: Sequence[str],
    ) -> tuple[list[float], list[str]]:
        """Fetch historical indicator values while preserving chronological order."""
        values: list[float] = []
        observed_periods: list[str] = []
        for period in periods:
            try:
                result = await self.calculator.calculate_single(
                    indicator_id=indicator_id,
                    org_unit=org_unit,
                    period=period,
                )
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.debug("Skipping historical value for %s %s: %s", indicator_id, period, exc)
                continue

            if result and result.result_value is not None:
                values.append(result.result_value)
                observed_periods.append(period)

        return values, observed_periods

    @staticmethod
    def _is_monthly_period(period: str) -> bool:
        """Return True when the period uses YYYYMM monthly format."""
        normalized = str(period).strip()
        if len(normalized) != 6 or not normalized.isdigit():
            return False
        year = int(normalized[:4])
        month = int(normalized[4:6])
        return 2000 <= year <= 2100 and 1 <= month <= 12

    @staticmethod
    def _generate_historical_periods_monthly(current_period: str, count: int) -> list[str]:
        """Generate previous monthly periods in chronological order."""
        if count <= 0:
            return []

        year = int(current_period[:4])
        month = int(current_period[4:6])
        periods: list[str] = []

        for _ in range(count):
            month -= 1
            if month < 1:
                month = 12
                year -= 1
            periods.append(f"{year}{month:02d}")

        periods.reverse()
        return periods

    async def get_dq_score(self, org_unit: str, period: str) -> Dict[str, Any]:
        """Calculate the overall DQ score and grade for a selection."""
        result = await self.run_checks(
            org_unit=org_unit,
            period=period,
            include_historical=False,
        )

        penalty = (
            result.summary.critical_count * 10
            + result.summary.warning_count * 3
            + result.summary.info_count * 1
        )
        max_penalty = result.summary.total_checks * 10
        if max_penalty == 0:
            score = 100.0
        else:
            score = max(0.0, 100 - (penalty / max_penalty * 100))

        if score >= 90:
            grade = "A"
            grade_label = "Excellent"
        elif score >= 75:
            grade = "B"
            grade_label = "Good"
        elif score >= 60:
            grade = "C"
            grade_label = "Needs Improvement"
        elif score >= 40:
            grade = "D"
            grade_label = "Poor"
        else:
            grade = "F"
            grade_label = "Critical Issues"

        return {
            "org_unit": org_unit,
            "period": period,
            "score": round(score, 1),
            "grade": grade,
            "grade_label": grade_label,
            "summary": result.summary.to_dict(),
        }
