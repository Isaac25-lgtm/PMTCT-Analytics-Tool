"""
Prompt 11 AI insights engine.

The engine stays grounded in the current request context only. It never stores
cross-session state and always works from the current calculator, alert, and
data-quality outputs already implemented in Prompts 2-10.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from app.core.config import Settings, get_settings
from app.indicators.calculator import IndicatorCalculator
from app.indicators.models import (
    IndicatorCategory,
    IndicatorDefinition,
    IndicatorResult,
    IndicatorResultSet,
    Periodicity,
    ResultType,
)
from app.indicators.registry import IndicatorRegistry, get_indicator_registry
from app.services.ai_prompts import (
    InsightType,
    SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT_QA,
    build_alert_prompt,
    build_cascade_prompt,
    build_dq_prompt,
    build_executive_summary_prompt,
    build_indicator_prompt,
    build_qa_prompt,
    build_recommendation_prompt,
    get_cascade_definition,
)
from app.services.alert_engine import AlertEngine, AlertResult
from app.services.data_quality import DataQualityEngine, DQResult
from app.services.dq_rules import DQFinding
from app.services.llm_provider import LLMProvider, LLMResponse, get_llm_provider
from app.services.trends import IndicatorTrend, TrendDirection, TrendService

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class InsightStatus(str, Enum):
    """Insight generation status."""

    SUCCESS = "success"
    FALLBACK = "fallback"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(slots=True)
class Insight:
    """One generated insight payload."""

    insight_id: str
    insight_type: InsightType
    content: str
    org_unit: str
    period: str
    status: InsightStatus
    error_message: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    model_used: str | None = None
    tokens_used: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe insight payload."""
        return {
            "insight_id": self.insight_id,
            "insight_type": self.insight_type.value,
            "content": self.content,
            "org_unit": self.org_unit,
            "period": self.period,
            "status": self.status.value,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class InsightEnvelope:
    """Wrapper used by JSON routes and HTMX partials alike."""

    insight: Insight

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe response envelope."""
        return {"insight": self.insight.to_dict()}


class AIInsightsEngine:
    """Generate Prompt 11 insights from the live service layer."""

    HISTORY_DEPTH_MAP = {"3m": 3, "12m": 12, "36m": 36}
    FULL_HISTORY_START = (2000, 1)

    def __init__(
        self,
        *,
        calculator: IndicatorCalculator,
        dq_engine: DataQualityEngine | None = None,
        alert_engine: AlertEngine | None = None,
        trend_service: TrendService | None = None,
        llm_provider: LLMProvider | None = None,
        settings: Settings | None = None,
        registry: IndicatorRegistry | None = None,
    ) -> None:
        self.calculator = calculator
        self.settings = settings or get_settings()
        self.registry = registry or get_indicator_registry()
        self.dq_engine = dq_engine or DataQualityEngine(calculator=calculator)
        self.alert_engine = alert_engine or AlertEngine(calculator=calculator)
        self.trend_service = trend_service or TrendService()
        self._llm_provider = llm_provider or get_llm_provider(self.settings)

    async def close(self) -> None:
        """Close any request-scoped LLM resources."""
        if self._llm_provider is not None:
            await self._llm_provider.close()

    def _new_insight(
        self,
        *,
        insight_type: InsightType,
        content: str,
        org_unit: str,
        period: str,
        status: InsightStatus,
        error_message: str | None = None,
        model_used: str | None = None,
        tokens_used: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InsightEnvelope:
        """Build an insight envelope while enforcing output limits."""
        return InsightEnvelope(
            insight=Insight(
                insight_id=f"{insight_type.value[:3]}-{uuid4().hex[:8]}",
                insight_type=insight_type,
                content=self._enforce_max_length(content),
                org_unit=org_unit,
                period=period,
                status=status,
                error_message=error_message,
                model_used=model_used,
                tokens_used=tokens_used,
                metadata=metadata or {},
            )
        )

    def _enforce_max_length(self, content: str) -> str:
        """Apply the configured max-content safeguard to every response."""
        normalized = str(content or "").strip()
        max_length = max(120, int(self.settings.llm_max_content_length))
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 20].rstrip() + "\n\n[Content truncated]"

    def _llm_available(self) -> bool:
        """Return True when LLM generation is enabled and configured."""
        return bool(self.settings.llm_enabled and self._llm_provider is not None)

    async def _call_llm(
        self,
        *,
        user_prompt: str,
        system_prompt: str = SYSTEM_PROMPT_BASE,
    ) -> LLMResponse:
        """Call the configured provider."""
        if self._llm_provider is None:
            raise ValueError("LLM provider is not configured")
        response = await self._llm_provider.generate(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=self.settings.llm_max_tokens,
            temperature=self.settings.llm_temperature,
        )
        response.content = self._enforce_max_length(response.content)
        return response

    @staticmethod
    def _category_label(category: IndicatorCategory) -> str:
        """Format an indicator category for display and prompting."""
        return category.value.replace("_", " ").title()

    @staticmethod
    def _result_type_unit(result_type: ResultType) -> str:
        """Return the display suffix for a result type."""
        if result_type == ResultType.PERCENTAGE:
            return "%"
        if result_type == ResultType.DAYS:
            return " days"
        return ""

    @classmethod
    def _format_value(cls, value: float | None, result_type: ResultType) -> str:
        """Format a value safely for prompts, Q&A, and fallbacks."""
        if value is None:
            return "N/A"
        if result_type == ResultType.PERCENTAGE:
            return f"{value:.1f}%"
        if result_type == ResultType.DAYS:
            return f"{value:.1f} days"
        if result_type == ResultType.COUNT:
            return f"{value:,.0f}"
        return f"{value:.2f}"

    def _format_result_context_line(self, result: IndicatorResult) -> str:
        """
        Format one indicator line for Q&A context safely.

        This intentionally avoids inline conditional formatting inside the
        numeric format specifier, which caused the Prompt 11 f-string bug.
        """
        current_value = self._format_value(result.result_value, result.result_type)
        target_value = (
            self._format_value(result.target, result.result_type)
            if result.target is not None
            else "N/A"
        )
        return (
            f"- {result.indicator_id} {result.indicator_name}: {current_value} "
            f"(target: {target_value})"
        )

    @classmethod
    def _resolve_history_period_count(cls, history_depth: str, end_period: str) -> int:
        """Map the Prompt 5-style history selector to a month count."""
        normalized = history_depth.strip().lower()
        if normalized in cls.HISTORY_DEPTH_MAP:
            return cls.HISTORY_DEPTH_MAP[normalized]
        if normalized != "full":
            raise ValueError("history_depth must be one of 3m, 12m, 36m, or full")

        validated = TrendService.validate_monthly_period(end_period)
        end_year = int(validated[:4])
        end_month = int(validated[4:6])
        start_year, start_month = cls.FULL_HISTORY_START
        return ((end_year - start_year) * 12) + (end_month - start_month) + 1

    async def _build_trend_context(
        self,
        *,
        indicator: IndicatorDefinition,
        org_unit: str,
        period: str,
        history_depth: str,
    ) -> str:
        """Build trend context for monthly indicator interpretation."""
        if indicator.periodicity != Periodicity.MONTHLY:
            return "Trend context skipped because this indicator is not monthly."

        end_period = TrendService.validate_monthly_period(period)
        month_count = self._resolve_history_period_count(history_depth, end_period)
        periods = self.trend_service.generate_monthly_periods(end_period=end_period, num_periods=month_count)

        period_results: list[tuple[str, IndicatorResult | None]] = []
        for month in periods:
            try:
                period_results.append(
                    (
                        month,
                        await self.calculator.calculate_single(indicator.id, org_unit, month),
                    )
                )
            except Exception as exc:
                logger.debug("Trend context skipped result for %s %s: %s", indicator.id, month, exc)
                period_results.append((month, None))

        trend = self.trend_service.build_indicator_trend(
            indicator_id=indicator.id,
            indicator_name=indicator.name,
            category=self._category_label(indicator.category),
            target=indicator.target,
            result_type=indicator.result_type,
            period_results=period_results,
        )
        return self._describe_trend(trend)

    def _describe_trend(self, trend: IndicatorTrend) -> str:
        """Convert a trend summary into a short narrative string."""
        summary = trend.summary
        if summary.direction == TrendDirection.UNKNOWN or summary.start_value is None or summary.end_value is None:
            return "No valid trend context available for the selected history window."

        start_value = self._format_value(summary.start_value, trend.result_type)
        end_value = self._format_value(summary.end_value, trend.result_type)
        if summary.percent_change is None:
            return (
                f"Trend direction: {summary.direction.value}. "
                f"Started at {start_value} and ended at {end_value}."
            )
        return (
            f"Trend direction: {summary.direction.value}. "
            f"Started at {start_value}, ended at {end_value}, "
            f"and changed by {summary.percent_change:+.1f}% across "
            f"{summary.valid_periods} valid months."
        )

    def _build_supply_status_entries(self, result_set: IndicatorResultSet) -> list[dict[str, str]]:
        """Summarise supply status using the real Prompt 3 and Prompt 10 meanings."""
        entries: list[dict[str, str]] = []
        lookup = {result.indicator_id: result for result in result_set.results}

        for days_id, stockout_days_id, consumed_id, item_name in (
            ("SUP-05", "SUP-02", "SUP-01", "HBsAg kits"),
            ("SUP-06", "SUP-04", "SUP-03", "HIV/Syphilis duo kits"),
        ):
            days_result = lookup.get(days_id)
            stockout_days_result = lookup.get(stockout_days_id)
            consumed_result = lookup.get(consumed_id)

            status_parts: list[str] = []
            if days_result is not None:
                stock_on_hand = days_result.numerator_value
                days_of_use = days_result.result_value
                if stock_on_hand == 0:
                    status_parts.append("stockout derived from days-of-use stock on hand = 0")
                elif days_of_use is None:
                    status_parts.append("days of use unavailable")
                elif days_of_use < 7:
                    status_parts.append(f"critical stock at {days_of_use:.1f} days of use")
                elif days_of_use < 30:
                    status_parts.append(f"low stock at {days_of_use:.1f} days of use")
                elif days_of_use > 180:
                    status_parts.append(f"overstock at {days_of_use:.1f} days of use")
                else:
                    status_parts.append(f"{days_of_use:.1f} days of use available")

            if stockout_days_result is not None and stockout_days_result.result_value is not None:
                stockout_days = stockout_days_result.result_value
                if stockout_days > 0:
                    status_parts.append(f"{stockout_days:.0f} stockout days reported")

            if consumed_result is not None and consumed_result.result_value is not None:
                status_parts.append(f"{consumed_result.result_value:,.0f} kits consumed")

            entries.append(
                {
                    "name": item_name,
                    "status": "; ".join(status_parts) if status_parts else "No supply data available.",
                }
            )

        return entries

    def _fallback_indicator_insight(self, result: IndicatorResult, trend_context: str) -> str:
        """Rule-based indicator interpretation."""
        current_value = self._format_value(result.result_value, result.result_type)
        target_value = self._format_value(result.target, result.result_type)
        if result.result_value is None:
            return f"{result.indicator_name} has no current value for this period."
        if result.target is None:
            return (
                f"{result.indicator_name} is currently {current_value}. "
                f"No configured target is available for comparison. {trend_context}"
            )
        if result.meets_target:
            return (
                f"{result.indicator_name} is currently {current_value}, which meets the target of "
                f"{target_value}. {trend_context}"
            )
        gap = result.target_gap
        gap_text = self._format_value(gap, result.result_type) if gap is not None else "N/A"
        return (
            f"{result.indicator_name} is currently {current_value}, below the target of "
            f"{target_value}. The current shortfall is {gap_text}. {trend_context}"
        )

    @staticmethod
    def _fallback_cascade_insight(cascade_name: str, cascade_steps: list[dict[str, Any]]) -> str:
        """Rule-based cascade narrative."""
        if not cascade_steps:
            return (
                f"SUMMARY: No data is available for the {cascade_name}.\n"
                "BOTTLENECK: Unable to assess.\n"
                "RECOMMENDATION: Confirm that the required indicators were reported for this period."
            )

        weakest_step = min(
            (step for step in cascade_steps if step.get("value") is not None and not step.get("is_positivity")),
            key=lambda step: step["value"],
            default=None,
        )
        if weakest_step is None:
            return (
                f"SUMMARY: The {cascade_name} has insufficient non-positivity steps for bottleneck analysis.\n"
                "BOTTLENECK: Unable to determine from the available data.\n"
                "RECOMMENDATION: Review the missing cascade inputs first."
            )

        return (
            f"SUMMARY: The {cascade_name} shows uneven performance across its monthly steps.\n"
            f"BOTTLENECK: {weakest_step['name']} at {weakest_step['display_value']}.\n"
            "RECOMMENDATION: Focus supportive supervision and follow-up on the weakest step first."
        )

    @staticmethod
    def _fallback_alert_insight(alert_result: AlertResult) -> str:
        """Rule-based alert synthesis."""
        summary = alert_result.summary
        if summary.total_alerts == 0:
            return (
                "SITUATION: No active monthly alerts were generated for this selection.\n"
                "PRIORITIES:\n"
                "1. Continue routine monitoring.\n"
                "2. Keep reviewing reporting completeness.\n"
                "3. Re-run the evaluation after new data arrives.\n"
                "PATTERNS: None identified"
            )
        return (
            f"SITUATION: There are {summary.critical_count} critical and {summary.warning_count} warning "
            f"monthly alerts requiring programme follow-up.\n"
            "PRIORITIES:\n"
            "1. Resolve critical supply or indicator alerts first.\n"
            "2. Review warning-level gaps against targets.\n"
            "3. Check whether data-quality issues are amplifying the alert burden.\n"
            f"PATTERNS: Categories affected include {', '.join(sorted(summary.by_category.keys())) or 'none'}"
        )

    @staticmethod
    def _fallback_dq_insight(score_data: dict[str, Any], dq_result: DQResult) -> str:
        """Rule-based DQ explanation."""
        summary = dq_result.summary
        if summary.critical_count > 0:
            critical_issue = dq_result.findings[0].message if dq_result.findings else "Critical issues detected."
            fix = (
                "Review the critical finding first, validate the reported source values, "
                "and correct the affected indicators before using the data for decisions."
            )
        elif summary.warning_count > 0:
            critical_issue = "Warning-level issues were identified."
            fix = "Review the warning findings and correct the affected source records."
        else:
            critical_issue = "No major issues were identified."
            fix = "Continue routine data-quality review and timely reporting."
        return (
            f"STATUS: Data quality is scored at {score_data['score']:.1f}/100 with grade "
            f"{score_data['grade']} ({score_data['grade_label']}).\n"
            f"CRITICAL ISSUE: {critical_issue}\n"
            f"FIX: {fix}"
        )

    def _fallback_executive_summary(
        self,
        *,
        validation_results: list[IndicatorResult],
        alert_result: AlertResult,
        dq_score: dict[str, Any],
        supply_entries: list[dict[str, str]],
    ) -> str:
        """Rule-based executive summary."""
        met_targets = sum(1 for result in validation_results if result.meets_target)
        total_targets = len(validation_results)
        supply_summary = "; ".join(
            f"{entry['name']}: {entry['status']}" for entry in supply_entries[:2]
        ) or "No supply data available."
        return (
            "Executive Summary:\n\n"
            f"{met_targets} of {total_targets} WHO validation indicators are currently meeting target. "
            f"Monthly alerts include {alert_result.summary.critical_count} critical and "
            f"{alert_result.summary.warning_count} warning items.\n\n"
            f"Data quality is {dq_score['grade_label'].lower()} with a score of {dq_score['score']:.1f}/100. "
            f"Supply context: {supply_summary}\n\n"
            "Next steps: address critical alerts first, close the largest target gaps, and confirm the "
            "underlying data quality issues before escalation."
        )

    @staticmethod
    def _fallback_recommendations(result: IndicatorResult) -> str:
        """Rule-based recommendations."""
        if result.meets_target:
            return (
                "1. Maintain the current delivery approach that is keeping this indicator on target.\n"
                "2. Document the strongest contributing practices for replication elsewhere.\n"
                "3. Keep monitoring for any early signs of decline."
            )
        return (
            "1. Review facility-level performance to identify where the target gap is largest.\n"
            "2. Provide focused supportive supervision and refresher guidance on the weakest step.\n"
            "3. Verify that required commodities and registers are available before the next reporting cycle.\n"
            "4. Check related data-quality findings to confirm the gap reflects true performance.\n"
            "5. Follow up on progress in the next monthly review meeting."
        )

    @staticmethod
    def _fallback_qa_response(question: str) -> str:
        """Rule-based Q&A response when no LLM is configured."""
        return (
            "I cannot generate a full natural-language answer because no LLM provider is currently "
            f"available. Please review the current-session indicator, alert, and data-quality context for: {question}"
        )

    async def generate_indicator_insight(
        self,
        *,
        indicator_id: str,
        org_unit: str,
        period: str,
        include_trend: bool = True,
        history_depth: str = "12m",
    ) -> InsightEnvelope:
        """Generate a plain-language interpretation for one indicator."""
        try:
            result = await self.calculator.calculate_single(indicator_id, org_unit, period)
            indicator = self.registry.get(indicator_id)
            if indicator is None:
                raise ValueError(f"Unknown indicator: {indicator_id}")
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.INDICATOR_INTERPRETATION,
                content=f"Unable to analyse indicator {indicator_id}.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
                metadata={"indicator_id": indicator_id},
            )

        trend_context = "Trend context not requested."
        if include_trend:
            try:
                trend_context = await self._build_trend_context(
                    indicator=indicator,
                    org_unit=org_unit,
                    period=period,
                    history_depth=history_depth,
                )
            except Exception as exc:
                logger.debug("Trend context unavailable for %s: %s", indicator_id, exc)
                trend_context = "Trend context unavailable for the selected history window."

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_indicator_insight(result, trend_context)
                if self.settings.llm_fallback_enabled
                else "AI insight generation is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.INDICATOR_INTERPRETATION,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"indicator_id": indicator_id, "history_depth": history_depth},
            )

        prompt = build_indicator_prompt(
            indicator_name=result.indicator_name,
            category=self._category_label(result.category),
            description=indicator.description,
            current_value=self._format_value(result.result_value, result.result_type),
            target_value=self._format_value(result.target, result.result_type),
            meets_target="Yes" if result.meets_target else "No",
            numerator=self._format_value(result.numerator_value, ResultType.COUNT),
            denominator=self._format_value(result.denominator_value, ResultType.COUNT),
            org_unit=result.org_unit_name or org_unit,
            period=period,
            trend_context=trend_context,
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.INDICATOR_INTERPRETATION,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"indicator_id": indicator_id, "history_depth": history_depth},
            )
        except Exception as exc:
            logger.warning("Indicator insight LLM call failed: %s", exc)
            if not self.settings.llm_fallback_enabled:
                return self._new_insight(
                    insight_type=InsightType.INDICATOR_INTERPRETATION,
                    content="Unable to generate the requested insight.",
                    org_unit=org_unit,
                    period=period,
                    status=InsightStatus.ERROR,
                    error_message=str(exc),
                    metadata={"indicator_id": indicator_id},
                )
            return self._new_insight(
                insight_type=InsightType.INDICATOR_INTERPRETATION,
                content=self._fallback_indicator_insight(result, trend_context),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"indicator_id": indicator_id, "history_depth": history_depth},
            )

    async def generate_cascade_insight(
        self,
        *,
        cascade: str,
        org_unit: str,
        period: str,
    ) -> InsightEnvelope:
        """Generate a bottleneck analysis for a supported cascade."""
        cascade_definition = get_cascade_definition(cascade)
        if cascade_definition is None:
            return self._new_insight(
                insight_type=InsightType.CASCADE_ANALYSIS,
                content=f"Unsupported cascade '{cascade}'.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message="Cascade must be hiv, syphilis, or hbv.",
                metadata={"cascade": cascade},
            )

        cascade_steps: list[dict[str, Any]] = []
        for indicator_id in cascade_definition["indicators"]:
            try:
                result = await self.calculator.calculate_single(indicator_id, org_unit, period)
            except Exception as exc:
                logger.debug("Cascade step %s skipped: %s", indicator_id, exc)
                continue
            cascade_steps.append(
                {
                    "id": indicator_id,
                    "name": result.indicator_name,
                    "value": result.result_value,
                    "display_value": self._format_value(result.result_value, result.result_type),
                    "target": self._format_value(result.target, result.result_type),
                    "meets_target": result.meets_target,
                    "is_positivity": indicator_id in cascade_definition["positivity_indicators"],
                }
            )

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_cascade_insight(cascade_definition["name"], cascade_steps)
                if self.settings.llm_fallback_enabled
                else "AI cascade analysis is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.CASCADE_ANALYSIS,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"cascade": cascade},
            )

        step_lines = "\n".join(
            (
                f"- {step['name']}: {step['display_value']} "
                f"(target: {step['target']}, meets target: {'yes' if step['meets_target'] else 'no'})"
            )
            for step in cascade_steps
        )
        prompt = build_cascade_prompt(
            cascade_name=cascade_definition["name"],
            org_unit=org_unit,
            period=period,
            cascade_steps=step_lines,
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.CASCADE_ANALYSIS,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"cascade": cascade},
            )
        except Exception as exc:
            logger.warning("Cascade insight LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.CASCADE_ANALYSIS,
                content=self._fallback_cascade_insight(cascade_definition["name"], cascade_steps),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"cascade": cascade},
            )

    async def generate_alert_insight(self, *, org_unit: str, period: str) -> InsightEnvelope:
        """Generate a management summary of current monthly alerts."""
        try:
            alert_result = await self.alert_engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.ALERT_SYNTHESIS,
                content="Unable to evaluate monthly alerts for this selection.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
            )

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_alert_insight(alert_result)
                if self.settings.llm_fallback_enabled
                else "AI alert synthesis is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.ALERT_SYNTHESIS,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
            )

        alert_lines = "\n".join(
            f"- [{alert.severity.value.upper()}] {alert.title}: {alert.message}"
            for alert in alert_result.alerts[:15]
        )
        prompt = build_alert_prompt(
            org_unit=org_unit,
            period=period,
            critical_count=alert_result.summary.critical_count,
            warning_count=alert_result.summary.warning_count,
            info_count=alert_result.summary.info_count,
            alert_lines=alert_lines,
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.ALERT_SYNTHESIS,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
            )
        except Exception as exc:
            logger.warning("Alert insight LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.ALERT_SYNTHESIS,
                content=self._fallback_alert_insight(alert_result),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
            )

    async def generate_dq_insight(self, *, org_unit: str, period: str) -> InsightEnvelope:
        """Generate a plain-language explanation of current DQ findings."""
        try:
            dq_result = await self.dq_engine.run_checks(org_unit=org_unit, period=period)
            score_data = await self.dq_engine.get_dq_score(org_unit=org_unit, period=period)
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.DQ_EXPLANATION,
                content="Unable to retrieve data-quality findings for this selection.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
            )

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_dq_insight(score_data, dq_result)
                if self.settings.llm_fallback_enabled
                else "AI data-quality explanation is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.DQ_EXPLANATION,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"score": score_data["score"], "grade": score_data["grade"]},
            )

        finding_lines = "\n".join(
            f"- [{finding.severity.value}] {finding.message}" for finding in dq_result.findings[:12]
        )
        prompt = build_dq_prompt(
            org_unit=org_unit,
            period=period,
            score=f"{score_data['score']:.1f}",
            grade=score_data["grade"],
            grade_label=score_data["grade_label"],
            finding_lines=finding_lines,
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.DQ_EXPLANATION,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"score": score_data["score"], "grade": score_data["grade"]},
            )
        except Exception as exc:
            logger.warning("DQ insight LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.DQ_EXPLANATION,
                content=self._fallback_dq_insight(score_data, dq_result),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"score": score_data["score"], "grade": score_data["grade"]},
            )

    async def generate_executive_summary(self, *, org_unit: str, period: str) -> InsightEnvelope:
        """Generate a combined monthly management summary."""
        try:
            result_set = await self.calculator.calculate_all(org_unit=org_unit, period=period)
            dq_score = await self.dq_engine.get_dq_score(org_unit=org_unit, period=period)
            alert_result = await self.alert_engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.EXECUTIVE_SUMMARY,
                content="Unable to compile the executive summary for this selection.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
            )

        validation_results = [
            result
            for result in result_set.results
            if result.category == IndicatorCategory.WHO_VALIDATION
        ]
        validation_lines = "\n".join(
            f"- {result.indicator_name}: {self._format_value(result.result_value, result.result_type)} "
            f"(target: {self._format_value(result.target, result.result_type)}, "
            f"meets target: {'yes' if result.meets_target else 'no'})"
            for result in validation_results
        )
        supply_entries = self._build_supply_status_entries(result_set)
        supply_lines = "\n".join(f"- {entry['name']}: {entry['status']}" for entry in supply_entries)

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_executive_summary(
                    validation_results=validation_results,
                    alert_result=alert_result,
                    dq_score=dq_score,
                    supply_entries=supply_entries,
                )
                if self.settings.llm_fallback_enabled
                else "AI executive summary generation is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.EXECUTIVE_SUMMARY,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"dq_score": dq_score["score"], "dq_grade": dq_score["grade"]},
            )

        prompt = build_executive_summary_prompt(
            org_unit=org_unit,
            period=period,
            validation_lines=validation_lines,
            critical_alerts=alert_result.summary.critical_count,
            warning_alerts=alert_result.summary.warning_count,
            dq_score=f"{dq_score['score']:.1f}",
            dq_grade=dq_score["grade"],
            dq_grade_label=dq_score["grade_label"],
            supply_lines=supply_lines,
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.EXECUTIVE_SUMMARY,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"dq_score": dq_score["score"], "dq_grade": dq_score["grade"]},
            )
        except Exception as exc:
            logger.warning("Executive summary LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.EXECUTIVE_SUMMARY,
                content=self._fallback_executive_summary(
                    validation_results=validation_results,
                    alert_result=alert_result,
                    dq_score=dq_score,
                    supply_entries=supply_entries,
                ),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"dq_score": dq_score["score"], "dq_grade": dq_score["grade"]},
            )

    async def generate_recommendations(
        self,
        *,
        indicator_id: str,
        org_unit: str,
        period: str,
    ) -> InsightEnvelope:
        """Generate practical recommendations for one indicator."""
        try:
            result = await self.calculator.calculate_single(indicator_id, org_unit, period)
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.RECOMMENDATION,
                content=f"Unable to generate recommendations for {indicator_id}.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
                metadata={"indicator_id": indicator_id},
            )

        related_alerts: list[str] = []
        try:
            alert_result = await self.alert_engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
            related_alerts = [
                alert.title
                for alert in alert_result.alerts
                if alert.indicator_id == indicator_id
            ]
        except Exception:
            related_alerts = []

        dq_issues: list[str] = []
        try:
            dq_result = await self.dq_engine.run_checks(org_unit=org_unit, period=period)
            dq_issues = self._filter_dq_issues_for_indicator(dq_result.findings, indicator_id)
        except Exception:
            dq_issues = []

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_recommendations(result)
                if self.settings.llm_fallback_enabled
                else "AI recommendation generation is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.RECOMMENDATION,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"indicator_id": indicator_id},
            )

        gap = result.target_gap
        prompt = build_recommendation_prompt(
            indicator_name=result.indicator_name,
            category=self._category_label(result.category),
            current_value=self._format_value(result.result_value, result.result_type),
            target_value=self._format_value(result.target, result.result_type),
            gap_value=self._format_value(gap, result.result_type),
            org_unit=result.org_unit_name or org_unit,
            period=period,
            related_alerts=", ".join(related_alerts[:5]),
            dq_issues=", ".join(dq_issues[:5]),
        )
        try:
            llm_response = await self._call_llm(user_prompt=prompt)
            return self._new_insight(
                insight_type=InsightType.RECOMMENDATION,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"indicator_id": indicator_id},
            )
        except Exception as exc:
            logger.warning("Recommendation LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.RECOMMENDATION,
                content=self._fallback_recommendations(result),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"indicator_id": indicator_id},
            )

    def _filter_dq_issues_for_indicator(self, findings: list[DQFinding], indicator_id: str) -> list[str]:
        """Extract DQ findings explicitly tied to an indicator."""
        issues: list[str] = []
        for finding in findings:
            if finding.indicator_id == indicator_id:
                issues.append(finding.message)
                continue
            metadata_values = " ".join(str(value) for value in finding.metadata.values())
            if indicator_id in metadata_values:
                issues.append(finding.message)
        return issues

    async def generate_qa_response(
        self,
        *,
        question: str,
        org_unit: str,
        period: str,
    ) -> InsightEnvelope:
        """Answer a question using current-period indicators, alerts, and DQ findings only."""
        try:
            result_set = await self.calculator.calculate_all(org_unit=org_unit, period=period)
            dq_result = await self.dq_engine.run_checks(org_unit=org_unit, period=period)
            dq_score = await self.dq_engine.get_dq_score(org_unit=org_unit, period=period)
        except Exception as exc:
            return self._new_insight(
                insight_type=InsightType.QA_RESPONSE,
                content="Unable to gather the current-session data needed for Q&A.",
                org_unit=org_unit,
                period=period,
                status=InsightStatus.ERROR,
                error_message=str(exc),
                metadata={"question": question},
            )

        try:
            alert_result = await self.alert_engine.evaluate_alerts(org_unit=org_unit, period=period, include_dq=True)
            alert_lines = "\n".join(
                f"- [{alert.severity.value.upper()}] {alert.title}: {alert.message}"
                for alert in alert_result.alerts[:10]
            )
        except Exception:
            alert_lines = (
                "- Monthly alert synthesis is unavailable for this period or selection."
            )

        indicator_lines = "\n".join(
            self._format_result_context_line(result)
            for result in result_set.results[:20]
        )
        dq_lines = (
            f"- Score: {dq_score['score']:.1f}/100\n"
            f"- Grade: {dq_score['grade']} ({dq_score['grade_label']})\n"
            f"- Critical findings: {dq_result.summary.critical_count}\n"
            f"- Warning findings: {dq_result.summary.warning_count}"
        )

        if not self._llm_available():
            status = InsightStatus.FALLBACK if self.settings.llm_fallback_enabled else InsightStatus.DISABLED
            content = (
                self._fallback_qa_response(question)
                if self.settings.llm_fallback_enabled
                else "AI Q&A is currently disabled."
            )
            return self._new_insight(
                insight_type=InsightType.QA_RESPONSE,
                content=content,
                org_unit=org_unit,
                period=period,
                status=status,
                metadata={"question": question},
            )

        prompt = build_qa_prompt(
            question=question,
            org_unit=org_unit,
            period=period,
            indicator_lines=indicator_lines,
            alert_lines=alert_lines,
            dq_lines=dq_lines,
        )
        try:
            llm_response = await self._call_llm(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT_QA,
            )
            return self._new_insight(
                insight_type=InsightType.QA_RESPONSE,
                content=llm_response.content,
                org_unit=org_unit,
                period=period,
                status=InsightStatus.SUCCESS,
                model_used=llm_response.model,
                tokens_used=llm_response.tokens_used,
                metadata={"question": question},
            )
        except Exception as exc:
            logger.warning("Q&A LLM call failed: %s", exc)
            return self._new_insight(
                insight_type=InsightType.QA_RESPONSE,
                content=self._fallback_qa_response(question),
                org_unit=org_unit,
                period=period,
                status=InsightStatus.FALLBACK,
                error_message=str(exc),
                metadata={"question": question},
            )
