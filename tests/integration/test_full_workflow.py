"""End-to-end workflow tests against the live app and mock DHIS2."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestFullWorkflowIntegration:
    def test_dashboard_and_scorecard_workflow(self, integration_client) -> None:
        page = integration_client.get("/dashboard")
        assert page.status_code == 200

        report = integration_client.post(
            "/api/reports/scorecard",
            json={
                "org_unit": "OU_FACILITY_1",
                "period": "202401",
                "expected_pregnancies": 200,
            },
        )
        assert report.status_code == 200
        report_json = report.json()
        assert "summary" in report_json
        assert len(report_json["indicators"]) >= 1

    def test_supply_and_health_workflow(self, integration_client) -> None:
        page = integration_client.get("/supply")
        assert page.status_code == 200

        report = integration_client.post(
            "/api/reports/supply-status",
            json={"org_unit": "OU_FACILITY_1", "period": "202401"},
        )
        assert report.status_code == 200
        assert "summary" in report.json()

        health = integration_client.get("/health/live")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
