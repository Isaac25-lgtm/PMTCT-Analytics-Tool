"""
Unit tests for Prompt 10 alert rule helpers.
"""

from __future__ import annotations

from app.indicators.models import IndicatorCategory
from app.services.alert_rules import (
    Alert,
    AlertCategory,
    AlertSeverity,
    AlertThreshold,
    AlertType,
    format_alert_message,
)
from tests.conftest import TEST_ORG_UNIT, TEST_PERIOD, build_result


class TestAlertRules:
    def test_threshold_can_read_numerator_value(self) -> None:
        threshold = AlertThreshold(
            threshold_id="SUPPLY-STOCKOUT",
            name="Stockout",
            description="Stock on hand is zero.",
            indicator_ids=["SUP-05"],
            alert_type=AlertType.STOCKOUT,
            severity=AlertSeverity.CRITICAL,
            category=AlertCategory.SUPPLY,
            operator="eq",
            value=0.0,
            value_source="numerator_value",
        )
        result = build_result(
            "SUP-05",
            "HBsAg Days of Use",
            IndicatorCategory.SUPPLY,
            result_value=0.0,
            numerator_value=0.0,
        )

        observed = threshold.observed_value(result)

        assert observed == 0.0
        assert threshold.evaluate(observed, None) is True

    def test_format_alert_message_uses_template_fields(self) -> None:
        title, message = format_alert_message(
            AlertType.CRITICAL_BELOW_TARGET,
            indicator_name="ANC Coverage",
            value=60.0,
            target=95.0,
        )

        assert "ANC Coverage" in title
        assert "60.0%" in message
        assert "95%" in message

    def test_acknowledge_sets_timestamp(self) -> None:
        alert = Alert(
            alert_id="alert-1",
            alert_type=AlertType.LOW_STOCK,
            severity=AlertSeverity.WARNING,
            category=AlertCategory.SUPPLY,
            title="Low stock",
            message="Low stock detected",
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
        )

        alert.acknowledge()

        assert alert.acknowledged is True
        assert alert.acknowledged_at is not None
