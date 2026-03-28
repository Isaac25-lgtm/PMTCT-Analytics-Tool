"""
API tests for Prompt 10 alert routes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.alert_engine import AlertResult
from app.services.alert_rules import Alert, AlertCategory, AlertSeverity, AlertType
from tests.conftest import TEST_ORG_UNIT, TEST_PERIOD


def sample_alert_result(*, acknowledged: bool = False) -> AlertResult:
    """Return a deterministic alert result payload for route tests."""
    alerts = [
        Alert(
            alert_id="alert-critical",
            alert_type=AlertType.STOCKOUT,
            severity=AlertSeverity.CRITICAL,
            category=AlertCategory.SUPPLY,
            title="Stockout: HBsAg kits",
            message="HBsAg kits stock on hand is zero.",
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            indicator_id="SUP-05",
            current_value=0.0,
            threshold_value=0.0,
            acknowledged=acknowledged,
            acknowledged_at=datetime.now(UTC) if acknowledged else None,
        ),
        Alert(
            alert_id="alert-warning",
            alert_type=AlertType.BELOW_TARGET,
            severity=AlertSeverity.WARNING,
            category=AlertCategory.INDICATOR,
            title="ANC Coverage below target",
            message="ANC Coverage is below target.",
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            indicator_id="VAL-01",
            current_value=82.0,
            threshold_value=95.0,
            target_value=95.0,
        ),
    ]
    return AlertResult(
        org_unit=TEST_ORG_UNIT,
        period=TEST_PERIOD,
        evaluated_at=datetime.now(UTC),
        alerts=alerts,
    )


@pytest.mark.api
class TestAlertJsonRoutes:
    def test_get_alerts_requires_auth(self, client) -> None:
        response = client.get(f"/api/alerts?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}")

        assert response.status_code == 401

    def test_get_alerts_returns_filtered_json(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.alerts.AlertEngine.evaluate_alerts",
            new=AsyncMock(return_value=sample_alert_result()),
        ):
            response = client.get(
                f"/api/alerts?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}&severity=warning"
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert data["summary"]["total_alerts"] == 1
        assert data["summary"]["warning_count"] == 1
        assert data["alerts"][0]["severity"] == "warning"

    def test_get_alert_summary_returns_json(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.alerts.AlertEngine.evaluate_alerts",
            new=AsyncMock(return_value=sample_alert_result()),
        ):
            response = client.get(f"/api/alerts/summary?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}")

        assert response.status_code == 200
        data = response.json()
        assert data["critical_count"] == 1
        assert data["warning_count"] == 1

    def test_get_alert_thresholds_returns_configured_thresholds(
        self,
        client,
        valid_session,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session)

        response = client.get("/api/alerts/thresholds")

        assert response.status_code == 200
        data = response.json()
        assert "thresholds" in data
        assert any(threshold["threshold_id"] == "SUPPLY-STOCKOUT" for threshold in data["thresholds"])

    def test_acknowledge_returns_json_for_non_htmx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post("/api/alerts/alert-critical/acknowledge")

        assert response.status_code == 200
        data = response.json()
        assert data == {"alert_id": "alert-critical", "acknowledged": True}


@pytest.mark.api
class TestAlertHtmxRoutes:
    def test_htmx_list_returns_html(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.alerts.AlertEngine.evaluate_alerts",
            new=AsyncMock(return_value=sample_alert_result()),
        ):
            response = client.get(
                f"/api/alerts/htmx/list?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Stockout: HBsAg kits" in response.text
        assert "Critical alerts" in response.text

    def test_htmx_badge_returns_html(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.alerts.AlertEngine.evaluate_alerts",
            new=AsyncMock(return_value=sample_alert_result()),
        ):
            response = client.get(
                f"/api/alerts/htmx/badge?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Alert summary" in response.text
        assert "Critical: 1" in response.text

    def test_htmx_acknowledge_returns_refreshed_html_and_trigger(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.alerts.AlertEngine.evaluate_alerts",
            new=AsyncMock(return_value=sample_alert_result(acknowledged=True)),
        ):
            response = client.post(
                "/api/alerts/alert-critical/acknowledge",
                data={
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                    "severity": "",
                    "category": "",
                    "include_acknowledged": "true",
                },
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["HX-Trigger"] == "refresh-alert-badge"
        assert "Acknowledged" in response.text
