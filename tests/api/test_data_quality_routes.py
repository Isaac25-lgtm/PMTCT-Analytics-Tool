"""
API tests for Prompt 9 data-quality routes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.data_quality import DQResult, DQResultSummary
from app.services.dq_rules import DQCategory, DQFinding, DQSeverity
from tests.conftest import TEST_ORG_UNIT, TEST_PERIOD


def sample_dq_result() -> DQResult:
    """Return a deterministic DQ result payload for route tests."""
    return DQResult(
        org_unit=TEST_ORG_UNIT,
        period=TEST_PERIOD,
        checked_at=datetime.now(UTC),
        summary=DQResultSummary(
            total_checks=6,
            passed=4,
            critical_count=1,
            warning_count=1,
            info_count=0,
        ),
        findings=[
            DQFinding(
                rule_id="DQ-001",
                rule_name="Negative Value Check",
                severity=DQSeverity.CRITICAL,
                category=DQCategory.CONSISTENCY,
                message="Negative value detected: -3",
                org_unit=TEST_ORG_UNIT,
                period=TEST_PERIOD,
                indicator_id="SUP-01",
                current_value=-3,
                expected_range=">= 0",
            ),
            DQFinding(
                rule_id="DQ-006",
                rule_name="Cascade Consistency",
                severity=DQSeverity.WARNING,
                category=DQCategory.CASCADE,
                message="HIV-02 exceeds HIV-01",
                org_unit=TEST_ORG_UNIT,
                period=TEST_PERIOD,
                indicator_id="HIV-02",
            ),
        ],
        indicators_checked=["HIV-01", "HIV-02", "SUP-01"],
    )


@pytest.mark.api
class TestDataQualityJsonRoutes:
    def test_check_requires_auth(self, client) -> None:
        response = client.post(
            "/api/data-quality/check",
            json={"org_unit": TEST_ORG_UNIT, "period": TEST_PERIOD},
        )

        assert response.status_code == 401

    def test_check_returns_json(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.data_quality.DataQualityEngine.run_checks",
            new=AsyncMock(return_value=sample_dq_result()),
        ):
            response = client.post(
                "/api/data-quality/check",
                json={
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                    "include_historical": False,
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert data["org_unit"] == TEST_ORG_UNIT
        assert data["summary"]["critical_count"] == 1
        assert data["findings"][0]["rule_id"] == "DQ-001"

    def test_score_returns_json(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.data_quality.DataQualityEngine.get_dq_score",
            new=AsyncMock(
                return_value={
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                    "score": 83.0,
                    "grade": "B",
                    "grade_label": "Good",
                    "summary": {
                        "total_checks": 10,
                        "passed": 6,
                        "failed": 4,
                        "critical_count": 1,
                        "warning_count": 2,
                        "info_count": 1,
                        "pass_rate": 60.0,
                        "has_critical": True,
                    },
                }
            ),
        ):
            response = client.get(
                f"/api/data-quality/score?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["score"] == 83.0
        assert data["grade"] == "B"

    def test_rules_returns_enabled_rules(
        self,
        client,
        valid_session,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session)

        response = client.get("/api/data-quality/rules")

        assert response.status_code == 200
        data = response.json()
        assert "rules" in data
        assert any(rule["rule_id"] == "DQ-001" for rule in data["rules"])


@pytest.mark.api
class TestDataQualityHtmxRoutes:
    def test_htmx_results_returns_html(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.data_quality.DataQualityEngine.run_checks",
            new=AsyncMock(return_value=sample_dq_result()),
        ):
            response = client.get(
                f"/api/data-quality/htmx/results?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Negative Value Check" in response.text
        assert "Critical" in response.text

    def test_htmx_score_card_returns_html(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)
        with patch(
            "app.api.routes.data_quality.DataQualityEngine.get_dq_score",
            new=AsyncMock(
                return_value={
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                    "score": 92.0,
                    "grade": "A",
                    "grade_label": "Excellent",
                    "summary": {
                        "total_checks": 12,
                        "passed": 12,
                        "failed": 0,
                        "critical_count": 0,
                        "warning_count": 0,
                        "info_count": 0,
                        "pass_rate": 100.0,
                        "has_critical": False,
                    },
                }
            ),
        ):
            response = client.get(
                f"/api/data-quality/htmx/score-card?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Data quality score" in response.text
        assert "92.0%" in response.text
