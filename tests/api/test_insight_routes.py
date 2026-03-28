"""
API tests for Prompt 11 insight routes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_insights import AIInsightsEngine, Insight, InsightEnvelope, InsightStatus
from app.services.ai_prompts import InsightType
from app.services.llm_provider import LLMProvider
from tests.conftest import TEST_ORG_UNIT, TEST_PERIOD


def sample_envelope(
    insight_type: InsightType,
    *,
    content: str = "Sample Prompt 11 content",
) -> InsightEnvelope:
    """Build a deterministic insight response."""
    return InsightEnvelope(
        insight=Insight(
            insight_id="ins-abcdef12",
            insight_type=insight_type,
            content=content,
            org_unit=TEST_ORG_UNIT,
            period=TEST_PERIOD,
            status=InsightStatus.SUCCESS,
            created_at=datetime.now(UTC),
        )
    )


@pytest.mark.api
class TestInsightJsonRoutes:
    def test_indicator_json_requires_auth(self, client) -> None:
        response = client.post(
            "/api/insights/indicator",
            json={
                "indicator_id": "VAL-01",
                "org_unit": TEST_ORG_UNIT,
                "period": TEST_PERIOD,
            },
        )

        assert response.status_code == 401

    def test_indicator_json_returns_json(
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
            "app.api.routes.insights.AIInsightsEngine.generate_indicator_insight",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.INDICATOR_INTERPRETATION,
                    content="ANC coverage is below target.",
                )
            ),
        ):
            response = client.post(
                "/api/insights/indicator",
                json={
                    "indicator_id": "VAL-01",
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                    "history_depth": "12m",
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert data["insight"]["insight_type"] == "indicator_interpretation"
        assert "below target" in data["insight"]["content"]

    def test_recommendations_json_returns_json(
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
            "app.api.routes.insights.AIInsightsEngine.generate_recommendations",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.RECOMMENDATION,
                    content="1. Conduct supportive supervision.\n2. Review commodities.",
                )
            ),
        ):
            response = client.post(
                "/api/insights/recommendations",
                json={
                    "indicator_id": "VAL-01",
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "supportive supervision" in response.json()["insight"]["content"]


@pytest.mark.api
class TestInsightHtmxRoutes:
    def test_htmx_indicator_card_returns_html(
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
            "app.api.routes.insights.AIInsightsEngine.generate_indicator_insight",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.INDICATOR_INTERPRETATION,
                    content="ANC coverage remains below the validation threshold.",
                )
            ),
        ):
            response = client.get(
                f"/api/insights/htmx/indicator-card?indicator_id=VAL-01&org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}&history_depth=12m",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Indicator insight" in response.text
        assert "validation threshold" in response.text

    def test_htmx_alerts_returns_html(
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
            "app.api.routes.insights.AIInsightsEngine.generate_alert_insight",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.ALERT_SYNTHESIS,
                    content="SITUATION: Two warning alerts need review.",
                )
            ),
        ):
            response = client.get(
                f"/api/insights/htmx/alerts?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Alert synthesis" in response.text
        assert "Two warning alerts need review." in response.text

    def test_htmx_recommendations_returns_html(
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
            "app.api.routes.insights.AIInsightsEngine.generate_recommendations",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.RECOMMENDATION,
                    content="1. Verify source data.\n2. Follow up with facilities.",
                )
            ),
        ):
            response = client.get(
                f"/api/insights/htmx/recommendations?indicator_id=VAL-01&org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Actionable recommendations" in response.text
        assert "Verify source data." in response.text

    def test_htmx_executive_summary_returns_html(
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
            "app.api.routes.insights.AIInsightsEngine.generate_executive_summary",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.EXECUTIVE_SUMMARY,
                    content="Overall performance is mixed, with supply risk concentrated in duo kits.",
                )
            ),
        ):
            response = client.get(
                f"/api/insights/htmx/executive-summary?org_unit={TEST_ORG_UNIT}&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Executive summary" in response.text
        assert "supply risk concentrated" in response.text

    def test_htmx_executive_summary_falls_back_to_session_org_unit(
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
            "app.api.routes.insights.AIInsightsEngine.generate_executive_summary",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.EXECUTIVE_SUMMARY,
                    content="Fallback org-unit selection worked.",
                )
            ),
        ) as generate_summary:
            response = client.get(
                f"/api/insights/htmx/executive-summary?org_unit=&period={TEST_PERIOD}",
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert "Fallback org-unit selection worked." in response.text
        generate_summary.assert_awaited_once_with(org_unit=TEST_ORG_UNIT, period=TEST_PERIOD)

    def test_htmx_qa_returns_html(
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
            "app.api.routes.insights.AIInsightsEngine.generate_qa_response",
            new=AsyncMock(
                return_value=sample_envelope(
                    InsightType.QA_RESPONSE,
                    content="The main current risk is low duo-kit stock.",
                )
            ),
        ):
            response = client.post(
                "/api/insights/htmx/qa",
                data={
                    "question": "What is the main current risk?",
                    "org_unit": TEST_ORG_UNIT,
                    "period": TEST_PERIOD,
                },
                headers={"HX-Request": "true"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "main current risk" in response.text.lower()
        assert "current-session only" in response.text.lower()


@pytest.mark.asyncio
async def test_get_insights_engine_closes_provider(
    valid_session,
    mock_calculator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AsyncMock(spec=LLMProvider)
    monkeypatch.setattr("app.api.routes.alerts._alert_engines", {})
    monkeypatch.setattr("app.api.routes.insights.get_llm_provider", lambda: provider)
    monkeypatch.setattr(
        "app.api.routes.insights.get_session_alert_engine",
        lambda calculator, session: MagicMock(),
    )

    from app.api.routes.insights import get_insights_engine

    dependency = get_insights_engine(valid_session, mock_calculator)
    engine = await anext(dependency)

    assert isinstance(engine, AIInsightsEngine)

    await dependency.aclose()
    provider.close.assert_awaited_once()
