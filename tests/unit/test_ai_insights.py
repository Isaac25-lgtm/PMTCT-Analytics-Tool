"""
Unit tests for Prompt 11 AI insights services.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.indicators.models import IndicatorCategory, ResultType
from app.services.ai_insights import AIInsightsEngine, Insight, InsightEnvelope, InsightStatus
from app.services.ai_prompts import InsightType, get_cascade_definition
from app.services.dq_rules import DQCategory, DQFinding, DQSeverity
from app.services.llm_provider import GeminiProvider, OpenAIProvider, get_llm_provider
from tests.conftest import build_result


def sample_settings(**overrides):
    """Build a lightweight settings stub for AI tests."""
    defaults = {
        "llm_enabled": True,
        "llm_fallback_enabled": True,
        "llm_max_content_length": 5000,
        "llm_max_tokens": 512,
        "llm_temperature": 0.3,
        "llm_timeout_seconds": 30,
        "llm_provider": "openai",
        "llm_api_key": "test-key",
        "llm_model": "gpt-4o-mini",
        "llm_base_url": None,
        "llm_azure_endpoint": None,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def sample_insight_envelope(
    insight_type: InsightType = InsightType.INDICATOR_INTERPRETATION,
    *,
    content: str = "Sample insight content",
) -> InsightEnvelope:
    """Build a deterministic insight envelope for route tests."""
    return InsightEnvelope(
        insight=Insight(
            insight_id="ins-12345678",
            insight_type=insight_type,
            content=content,
            org_unit="akV6429SUqu",
            period="202401",
            status=InsightStatus.SUCCESS,
            created_at=datetime.now(UTC),
        )
    )


@pytest.mark.unit
class TestLLMProviderFactory:
    def test_get_provider_returns_none_when_disabled(self) -> None:
        with patch("app.services.llm_provider.get_settings", return_value=sample_settings(llm_enabled=False)):
            assert get_llm_provider() is None

    def test_get_provider_returns_openai_provider(self) -> None:
        with patch("app.services.llm_provider.get_settings", return_value=sample_settings()):
            provider = get_llm_provider()

        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_returns_gemini_provider(self) -> None:
        with patch(
            "app.services.llm_provider.get_settings",
            return_value=sample_settings(llm_provider="gemini", llm_model="gemini-3-flash-preview"),
        ):
            provider = get_llm_provider()

        assert isinstance(provider, GeminiProvider)
        assert provider.model == "gemini-3-flash-preview"
        assert provider.api_url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


@pytest.mark.unit
class TestAIInsightsEngine:
    @pytest.mark.asyncio
    async def test_indicator_fallback_without_llm(
        self,
        mock_calculator,
        sample_percentage_indicator,
    ) -> None:
        engine = AIInsightsEngine(
            calculator=mock_calculator,
            llm_provider=None,
            settings=sample_settings(llm_enabled=False, llm_fallback_enabled=True),
            registry=MagicMock(get=MagicMock(return_value=sample_percentage_indicator)),
        )

        response = await engine.generate_indicator_insight(
            indicator_id="VAL-02",
            org_unit="akV6429SUqu",
            period="202401",
        )

        assert response.insight.status == InsightStatus.FALLBACK
        assert "HIV Testing Coverage" in response.insight.content
        assert response.insight.metadata["indicator_id"] == "VAL-02"

    def test_format_result_context_line_handles_missing_value(self) -> None:
        engine = AIInsightsEngine(
            calculator=MagicMock(),
            llm_provider=None,
            settings=sample_settings(llm_enabled=False),
            registry=MagicMock(),
        )
        result = build_result(
            "VAL-01",
            "ANC Coverage",
            IndicatorCategory.WHO_VALIDATION,
            result_value=None,
            result_type=ResultType.PERCENTAGE,
            target=95.0,
        )

        line = engine._format_result_context_line(result)

        assert "N/A" in line
        assert "target: 95.0%" in line

    def test_supply_status_entries_use_real_supply_meanings(self, supply_result_set) -> None:
        engine = AIInsightsEngine(
            calculator=MagicMock(),
            llm_provider=None,
            settings=sample_settings(llm_enabled=False),
            registry=MagicMock(),
        )

        entries = engine._build_supply_status_entries(supply_result_set)

        assert entries[0]["name"] == "HBsAg kits"
        assert "45.0 days of use available" in entries[0]["status"]
        assert "12 kits consumed" in entries[0]["status"]
        assert entries[1]["name"] == "HIV/Syphilis duo kits"
        assert "low stock at 9.0 days of use" in entries[1]["status"]
        assert "2 stockout days reported" in entries[1]["status"]

    @pytest.mark.asyncio
    async def test_close_awaits_provider_close(self) -> None:
        provider = AsyncMock()
        engine = AIInsightsEngine(
            calculator=MagicMock(),
            llm_provider=provider,
            settings=sample_settings(llm_enabled=True),
            registry=MagicMock(),
        )

        await engine.close()

        provider.close.assert_awaited_once()

    def test_get_cascade_definition_supports_prompt_11_cascades(self) -> None:
        assert get_cascade_definition("hiv") is not None
        assert get_cascade_definition("syphilis") is not None
        assert get_cascade_definition("hbv") is not None
        assert get_cascade_definition("unknown") is None

    @pytest.mark.asyncio
    async def test_filter_dq_issues_for_indicator_matches_indicator_id(self) -> None:
        engine = AIInsightsEngine(
            calculator=MagicMock(),
            llm_provider=None,
            settings=sample_settings(llm_enabled=False),
            registry=MagicMock(),
        )
        findings = [
            DQFinding(
                rule_id="DQ-001",
                rule_name="Negative values",
                severity=DQSeverity.CRITICAL,
                category=DQCategory.CONSISTENCY,
                message="Negative value detected",
                org_unit="akV6429SUqu",
                period="202401",
                indicator_id="VAL-01",
            ),
            DQFinding(
                rule_id="DQ-002",
                rule_name="Repeated values",
                severity=DQSeverity.WARNING,
                category=DQCategory.OUTLIER,
                message="Repeated values detected",
                org_unit="akV6429SUqu",
                period="202401",
                metadata={"indicator": "VAL-01"},
            ),
        ]

        issues = engine._filter_dq_issues_for_indicator(findings, "VAL-01")

        assert issues == ["Negative value detected", "Repeated values detected"]
