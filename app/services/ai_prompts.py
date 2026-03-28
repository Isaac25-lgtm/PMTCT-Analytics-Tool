"""
Prompt templates and cascade definitions for Prompt 11 AI insights.

Each prompt explicitly keeps the model grounded in the current request payload
only. No prompt relies on memory across requests or sessions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class InsightType(str, Enum):
    """Supported Prompt 11 insight types."""

    INDICATOR_INTERPRETATION = "indicator_interpretation"
    CASCADE_ANALYSIS = "cascade_analysis"
    ALERT_SYNTHESIS = "alert_synthesis"
    DQ_EXPLANATION = "dq_explanation"
    EXECUTIVE_SUMMARY = "executive_summary"
    RECOMMENDATION = "recommendation"
    QA_RESPONSE = "qa_response"


SYSTEM_PROMPT_BASE = """
You are a PMTCT Triple Elimination analytics assistant for Uganda Ministry of Health.

Rules:
- Use ONLY the data in this request.
- You have no memory across sessions or previous conversations.
- Be concise, practical, and suitable for program managers.
- Highlight target gaps, supply issues, and data-quality caveats clearly.
- Do not provide patient-level clinical advice.
- If the data is insufficient, say so plainly.
""".strip()


SYSTEM_PROMPT_QA = """
You answer questions about PMTCT programme data for the current request only.

Rules:
- Use ONLY the supplied indicator, alert, and data-quality context.
- If the question cannot be answered from the supplied data, say that clearly.
- Do not imply any cross-session or historical memory beyond what is shown here.
- Do not provide patient-level clinical advice.
""".strip()


INDICATOR_TEMPLATE = """
Explain this single programme indicator in plain language.

Indicator: {indicator_name}
Category: {category}
Description: {description}
Current result: {current_value}
Target: {target_value}
Meets target: {meets_target}
Numerator: {numerator}
Denominator: {denominator}
Organisation unit: {org_unit}
Period: {period}
Trend context: {trend_context}

Write 2 short paragraphs:
1. Explain what the current result means.
2. Explain the main programme implication and one practical follow-up action.
""".strip()


CASCADE_TEMPLATE = """
Review this PMTCT cascade and identify the main bottleneck.

Cascade: {cascade_name}
Organisation unit: {org_unit}
Period: {period}

Steps:
{cascade_steps}

Return:
SUMMARY: 2-3 sentences on overall performance
BOTTLENECK: the weakest step or biggest drop-off
RECOMMENDATION: one actionable next step
""".strip()


ALERT_TEMPLATE = """
Summarise these monthly programme alerts for management.

Organisation unit: {org_unit}
Period: {period}
Critical alerts: {critical_count}
Warning alerts: {warning_count}
Informational alerts: {info_count}

Alert details:
{alert_lines}

Return:
SITUATION: 2 sentences on the overall picture
PRIORITIES:
1. first priority
2. second priority
3. third priority
PATTERNS: recurring themes or "None identified"
""".strip()


DQ_TEMPLATE = """
Explain these data-quality findings in plain language.

Organisation unit: {org_unit}
Period: {period}
DQ score: {score}
DQ grade: {grade} ({grade_label})

Findings:
{finding_lines}

Return:
STATUS: a short summary of data quality
CRITICAL ISSUE: the main issue to address first
FIX: specific remediation steps
""".strip()


EXECUTIVE_SUMMARY_TEMPLATE = """
Write an executive summary for PMTCT Triple Elimination programme review.

Organisation unit: {org_unit}
Period: {period}

WHO validation indicators:
{validation_lines}

Monthly alerts:
- Critical: {critical_alerts}
- Warning: {warning_alerts}

Data quality:
- Score: {dq_score}
- Grade: {dq_grade} ({dq_grade_label})

Supply status:
{supply_lines}

Write 3 short paragraphs:
1. Overall performance
2. Main risks or gaps
3. 2-3 specific next steps for programme management
""".strip()


RECOMMENDATION_TEMPLATE = """
Provide practical recommendations for this indicator.

Indicator: {indicator_name}
Category: {category}
Current result: {current_value}
Target: {target_value}
Gap to target: {gap_value}
Organisation unit: {org_unit}
Period: {period}
Related alerts: {related_alerts}
Related data-quality issues: {dq_issues}

Return a numbered list with 3 to 5 recommendations.
Keep each recommendation specific and feasible for district or facility teams.
""".strip()


QA_TEMPLATE = """
Answer this question using ONLY the supplied current-session context.

Question: {question}
Organisation unit: {org_unit}
Period: {period}

Indicator context:
{indicator_lines}

Alert context:
{alert_lines}

Data-quality context:
{dq_lines}

Answer directly. If the data is insufficient, say what is missing.
""".strip()


CASCADE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "hiv": {
        "name": "HIV PMTCT cascade",
        "indicators": ["VAL-02", "HIV-02", "VAL-03", "HIV-05", "HIV-06", "HIV-07"],
        "positivity_indicators": {"HIV-02"},
    },
    "syphilis": {
        "name": "Syphilis PMTCT cascade",
        "indicators": ["VAL-04", "SYP-01", "VAL-05"],
        "positivity_indicators": {"SYP-01"},
    },
    "hbv": {
        "name": "HBV PMTCT cascade",
        "indicators": ["HBV-01", "HBV-02", "HBV-05", "VAL-06"],
        "positivity_indicators": {"HBV-02"},
    },
}


def get_cascade_definition(cascade: str) -> dict[str, Any] | None:
    """Return one of the supported Prompt 11 cascades."""
    return CASCADE_DEFINITIONS.get(cascade.strip().lower())


def build_indicator_prompt(
    *,
    indicator_name: str,
    category: str,
    description: str,
    current_value: str,
    target_value: str,
    meets_target: str,
    numerator: str,
    denominator: str,
    org_unit: str,
    period: str,
    trend_context: str,
) -> str:
    """Build the indicator interpretation prompt."""
    return INDICATOR_TEMPLATE.format(
        indicator_name=indicator_name,
        category=category,
        description=description or "No description available.",
        current_value=current_value,
        target_value=target_value,
        meets_target=meets_target,
        numerator=numerator,
        denominator=denominator,
        org_unit=org_unit,
        period=period,
        trend_context=trend_context or "No trend context available.",
    )


def build_cascade_prompt(
    *,
    cascade_name: str,
    org_unit: str,
    period: str,
    cascade_steps: str,
) -> str:
    """Build the cascade analysis prompt."""
    return CASCADE_TEMPLATE.format(
        cascade_name=cascade_name,
        org_unit=org_unit,
        period=period,
        cascade_steps=cascade_steps or "- No cascade data available.",
    )


def build_alert_prompt(
    *,
    org_unit: str,
    period: str,
    critical_count: int,
    warning_count: int,
    info_count: int,
    alert_lines: str,
) -> str:
    """Build the alert synthesis prompt."""
    return ALERT_TEMPLATE.format(
        org_unit=org_unit,
        period=period,
        critical_count=critical_count,
        warning_count=warning_count,
        info_count=info_count,
        alert_lines=alert_lines or "- No active alerts.",
    )


def build_dq_prompt(
    *,
    org_unit: str,
    period: str,
    score: str,
    grade: str,
    grade_label: str,
    finding_lines: str,
) -> str:
    """Build the DQ explanation prompt."""
    return DQ_TEMPLATE.format(
        org_unit=org_unit,
        period=period,
        score=score,
        grade=grade,
        grade_label=grade_label,
        finding_lines=finding_lines or "- No findings.",
    )


def build_executive_summary_prompt(
    *,
    org_unit: str,
    period: str,
    validation_lines: str,
    critical_alerts: int,
    warning_alerts: int,
    dq_score: str,
    dq_grade: str,
    dq_grade_label: str,
    supply_lines: str,
) -> str:
    """Build the executive summary prompt."""
    return EXECUTIVE_SUMMARY_TEMPLATE.format(
        org_unit=org_unit,
        period=period,
        validation_lines=validation_lines or "- No validation indicators available.",
        critical_alerts=critical_alerts,
        warning_alerts=warning_alerts,
        dq_score=dq_score,
        dq_grade=dq_grade,
        dq_grade_label=dq_grade_label,
        supply_lines=supply_lines or "- No supply status available.",
    )


def build_recommendation_prompt(
    *,
    indicator_name: str,
    category: str,
    current_value: str,
    target_value: str,
    gap_value: str,
    org_unit: str,
    period: str,
    related_alerts: str,
    dq_issues: str,
) -> str:
    """Build the recommendation prompt."""
    return RECOMMENDATION_TEMPLATE.format(
        indicator_name=indicator_name,
        category=category,
        current_value=current_value,
        target_value=target_value,
        gap_value=gap_value,
        org_unit=org_unit,
        period=period,
        related_alerts=related_alerts or "None",
        dq_issues=dq_issues or "None",
    )


def build_qa_prompt(
    *,
    question: str,
    org_unit: str,
    period: str,
    indicator_lines: str,
    alert_lines: str,
    dq_lines: str,
) -> str:
    """Build the grounded Q&A prompt."""
    return QA_TEMPLATE.format(
        question=question,
        org_unit=org_unit,
        period=period,
        indicator_lines=indicator_lines or "- No indicator context available.",
        alert_lines=alert_lines or "- No alert context available.",
        dq_lines=dq_lines or "- No data-quality context available.",
    )
