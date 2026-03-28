"""
Trend calculation helpers for multi-period indicator analysis.

This module only computes period ranges, labels, and trend statistics.
Indicator metadata stays in the registry and indicator values continue to come
from the Prompt 3 calculator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Optional

from app.indicators.models import ResultType


class TrendDirection(str, Enum):
    """Direction of movement across a trend window."""

    UP = "up"
    DOWN = "down"
    STABLE = "stable"
    UNKNOWN = "unknown"


@dataclass
class PeriodValue:
    """A single indicator value for one monthly period."""

    period: str
    period_label: str
    value: Optional[float]
    numerator: Optional[float]
    denominator: Optional[float]
    is_valid: bool


@dataclass
class TrendSummary:
    """Summary statistics for a multi-period indicator trend."""

    direction: TrendDirection
    start_value: Optional[float]
    end_value: Optional[float]
    absolute_change: Optional[float]
    percent_change: Optional[float]
    average: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    valid_periods: int
    total_periods: int
    meets_target_count: int
    target: Optional[float]


@dataclass
class IndicatorTrend:
    """Complete trend payload for one indicator."""

    indicator_id: str
    indicator_name: str
    category: str
    target: Optional[float]
    result_type: ResultType
    values: list[PeriodValue]
    summary: TrendSummary


class TrendService:
    """Compute monthly period windows and trend summaries."""

    STABILITY_THRESHOLD = 2.0

    @staticmethod
    def validate_monthly_period(period: str) -> str:
        """Validate a monthly DHIS2 period string in YYYYMM format."""
        normalized = str(period).strip()
        if len(normalized) != 6 or not normalized.isdigit():
            raise ValueError("end_period must use YYYYMM monthly format")

        month = int(normalized[4:6])
        if month < 1 or month > 12:
            raise ValueError("end_period must contain a valid month between 01 and 12")

        return normalized

    @classmethod
    def generate_monthly_periods(cls, end_period: str, num_periods: int) -> list[str]:
        """Generate monthly periods in chronological order, oldest first."""
        if num_periods < 1:
            raise ValueError("num_periods must be at least 1")

        validated_period = cls.validate_monthly_period(end_period)
        year = int(validated_period[:4])
        month = int(validated_period[4:6])

        periods: list[str] = []
        for _ in range(num_periods):
            periods.append(f"{year}{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1

        periods.reverse()
        return periods

    @staticmethod
    def format_period_label(period: str) -> str:
        """Format a YYYYMM period as Mon YYYY."""
        validated_period = TrendService.validate_monthly_period(period)
        year = int(validated_period[:4])
        month = int(validated_period[4:6])
        month_names = [
            "",
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        return f"{month_names[month]} {year}"

    @classmethod
    def build_monthly_period_options(
        cls,
        count: int = 12,
        *,
        today: date | None = None,
    ) -> list[dict[str, str]]:
        """Build period dropdown options, most recent first."""
        current_date = today or date.today()
        end_period = f"{current_date.year}{current_date.month:02d}"
        periods = cls.generate_monthly_periods(end_period=end_period, num_periods=count)
        return [{"id": period, "name": cls.format_period_label(period)} for period in reversed(periods)]

    def calculate_trend_summary(
        self,
        values: list[PeriodValue],
        target: Optional[float] = None,
    ) -> TrendSummary:
        """Compute trend statistics from period values."""
        valid_values = [value for value in values if value.is_valid and value.value is not None]
        total_periods = len(values)

        if not valid_values:
            return TrendSummary(
                direction=TrendDirection.UNKNOWN,
                start_value=None,
                end_value=None,
                absolute_change=None,
                percent_change=None,
                average=None,
                minimum=None,
                maximum=None,
                valid_periods=0,
                total_periods=total_periods,
                meets_target_count=0,
                target=target,
            )

        numeric_values = [value.value for value in valid_values if value.value is not None]
        start_value = numeric_values[0]
        end_value = numeric_values[-1]
        absolute_change = end_value - start_value

        if start_value == 0:
            percent_change = 100.0 if end_value != 0 else 0.0
        else:
            percent_change = ((end_value - start_value) / start_value) * 100

        if abs(percent_change) <= self.STABILITY_THRESHOLD:
            direction = TrendDirection.STABLE
        elif percent_change > 0:
            direction = TrendDirection.UP
        else:
            direction = TrendDirection.DOWN

        meets_target_count = 0
        if target is not None:
            meets_target_count = sum(1 for numeric_value in numeric_values if numeric_value >= target)

        return TrendSummary(
            direction=direction,
            start_value=start_value,
            end_value=end_value,
            absolute_change=absolute_change,
            percent_change=percent_change,
            average=sum(numeric_values) / len(numeric_values),
            minimum=min(numeric_values),
            maximum=max(numeric_values),
            valid_periods=len(numeric_values),
            total_periods=total_periods,
            meets_target_count=meets_target_count,
            target=target,
        )

    def build_indicator_trend(
        self,
        indicator_id: str,
        indicator_name: str,
        category: str,
        target: Optional[float],
        result_type: ResultType,
        period_results: list[tuple[str, Any]],
    ) -> IndicatorTrend:
        """Build a complete trend object from calculated period results."""
        values: list[PeriodValue] = []

        for period, result in period_results:
            values.append(
                PeriodValue(
                    period=period,
                    period_label=self.format_period_label(period),
                    value=result.result_value if result and result.is_valid else None,
                    numerator=result.numerator_value if result else None,
                    denominator=result.denominator_value if result else None,
                    is_valid=bool(result and result.is_valid),
                )
            )

        return IndicatorTrend(
            indicator_id=indicator_id,
            indicator_name=indicator_name,
            category=category,
            target=target,
            result_type=result_type,
            values=values,
            summary=self.calculate_trend_summary(values, target),
        )
