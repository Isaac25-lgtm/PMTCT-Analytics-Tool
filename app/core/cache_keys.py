"""
Consistent cache-key helpers for Prompt 14.

The live repo uses separate application and session cache stores, so keys focus
on resource names and deterministic parameters rather than embedding the entire
scope into every key.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from app.core.config import get_settings, load_yaml_config


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact(value[key]) for key in sorted(value)}
    if isinstance(value, set):
        return [_compact(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_compact(item) for item in value]
    return value


def hash_params(params: dict[str, Any]) -> str:
    """Return a short deterministic hash for a parameter dictionary."""
    payload = json.dumps(_compact(params), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def make_key(namespace: str, resource: str, params: dict[str, Any] | None = None) -> str:
    """Build a stable cache key."""
    if not params:
        return f"{namespace}:{resource}"
    return f"{namespace}:{resource}:{hash_params(params)}"


def _load_cache_yaml() -> dict[str, Any]:
    try:
        return load_yaml_config("cache.yaml") or {}
    except FileNotFoundError:
        return {}


def get_cache_ttl(name: str) -> int:
    """Return the effective TTL for a cache category."""
    settings = get_settings()
    yaml_config = _load_cache_yaml()
    ttl_config = yaml_config.get("ttl", {})
    fallback = {
        "default": settings.cache_default_ttl,
        "metadata": settings.cache_metadata_ttl,
        "hierarchy": settings.cache_hierarchy_ttl,
        "aggregate": settings.cache_aggregate_ttl,
        "indicators": settings.cache_indicator_ttl,
        "trends": settings.cache_trend_ttl,
        "insights": settings.cache_insight_ttl,
        "data_quality": settings.cache_data_quality_ttl,
        "alerts": settings.cache_alert_ttl,
    }
    return int(ttl_config.get(name, fallback.get(name, settings.cache_default_ttl)))


class CacheKeys:
    """Domain-specific key builders used across cached services and routes."""

    @staticmethod
    def org_unit_hierarchy(root_uid: str, max_level: int | None = None) -> str:
        return make_key("orgunit", "hierarchy", {"root_uid": root_uid, "max_level": max_level})

    @staticmethod
    def org_unit_metadata(uid: str) -> str:
        return make_key("orgunit", "metadata", {"uid": uid})

    @staticmethod
    def org_unit_user_roots() -> str:
        return make_key("orgunit", "user_roots")

    @staticmethod
    def org_unit_children(parent_uid: str, include_parent: bool) -> str:
        return make_key("orgunit", "children", {"parent_uid": parent_uid, "include_parent": include_parent})

    @staticmethod
    def org_unit_node(uid: str) -> str:
        return make_key("orgunit", "node", {"uid": uid})

    @staticmethod
    def org_unit_breadcrumbs(uid: str, limit_to_user_access: bool) -> str:
        return make_key(
            "orgunit",
            "breadcrumbs",
            {"uid": uid, "limit_to_user_access": limit_to_user_access},
        )

    @staticmethod
    def org_unit_search(query: str, root_uid: str | None, max_results: int) -> str:
        return make_key(
            "orgunit",
            "search",
            {"query": query.strip().lower(), "root_uid": root_uid, "max_results": max_results},
        )

    @staticmethod
    def org_unit_access(uid: str) -> str:
        return make_key("orgunit", "access", {"uid": uid})

    @staticmethod
    def data_values(
        data_elements: Sequence[str],
        org_unit: str,
        period: str,
        include_children: bool,
    ) -> str:
        return make_key(
            "dhis2",
            "data_values",
            {
                "data_elements": sorted(data_elements),
                "org_unit": org_unit,
                "period": period,
                "include_children": include_children,
            },
        )

    @staticmethod
    def data_value(
        data_element: str,
        org_unit: str,
        period: str,
        include_children: bool,
    ) -> str:
        return make_key(
            "dhis2",
            "data_value",
            {
                "data_element": data_element,
                "org_unit": org_unit,
                "period": period,
                "include_children": include_children,
            },
        )

    @staticmethod
    def disaggregated_values(
        data_element: str,
        category_option_combos: Sequence[str],
        org_unit: str,
        period: str,
    ) -> str:
        return make_key(
            "dhis2",
            "disaggregated",
            {
                "data_element": data_element,
                "category_option_combos": sorted(category_option_combos),
                "org_unit": org_unit,
                "period": period,
            },
        )

    @staticmethod
    def an21_pos_total(org_unit: str, period: str) -> str:
        return make_key("dhis2", "an21_pos", {"org_unit": org_unit, "period": period})

    @staticmethod
    def analytics(
        data_elements: Sequence[str],
        org_units: Sequence[str],
        periods: Sequence[str],
        include_children: bool,
    ) -> str:
        return make_key(
            "dhis2",
            "analytics",
            {
                "data_elements": sorted(data_elements),
                "org_units": sorted(org_units),
                "periods": list(periods),
                "include_children": include_children,
            },
        )

    @staticmethod
    def reporting_completeness(
        dataset_uid: str,
        org_unit: str,
        period: str,
        include_children: bool,
    ) -> str:
        return make_key(
            "dhis2",
            "reporting_completeness",
            {
                "dataset_uid": dataset_uid,
                "org_unit": org_unit,
                "period": period,
                "include_children": include_children,
            },
        )

    @staticmethod
    def data_element_meta(uid: str) -> str:
        return make_key("metadata", "data_element", {"uid": uid})

    @staticmethod
    def category_option_combo(uid: str) -> str:
        return make_key("metadata", "category_option_combo", {"uid": uid})

    @staticmethod
    def validate_uids(uids: Sequence[str]) -> str:
        return make_key("metadata", "validate_uids", {"uids": sorted(uids)})

    @staticmethod
    def indicator_single(
        indicator_id: str,
        org_unit: str,
        period: str,
        org_unit_name: str | None,
        include_children: bool,
        population_value: int | None,
    ) -> str:
        return make_key(
            "indicator",
            "single",
            {
                "indicator_id": indicator_id,
                "org_unit": org_unit,
                "period": period,
                "org_unit_name": org_unit_name,
                "include_children": include_children,
                "population_value": population_value,
            },
        )

    @staticmethod
    def indicator_batch(
        org_unit: str,
        period: str,
        org_unit_name: str | None,
        include_children: bool,
        categories: Sequence[str] | None,
        population_value: int | None,
    ) -> str:
        return make_key(
            "indicator",
            "batch",
            {
                "org_unit": org_unit,
                "period": period,
                "org_unit_name": org_unit_name,
                "include_children": include_children,
                "categories": list(categories or []),
                "population_value": population_value,
            },
        )

    @staticmethod
    def trend_analysis(
        org_unit: str,
        end_period: str,
        num_periods: int,
        indicator_ids: Sequence[str],
    ) -> str:
        return make_key(
            "trend",
            "analysis",
            {
                "org_unit": org_unit,
                "end_period": end_period,
                "num_periods": num_periods,
                "indicator_ids": list(indicator_ids),
            },
        )

    @staticmethod
    def data_quality(
        org_unit: str,
        period: str,
        indicator_ids: Sequence[str] | None,
        include_historical: bool,
        historical_periods: int,
    ) -> str:
        return make_key(
            "dq",
            "results",
            {
                "org_unit": org_unit,
                "period": period,
                "indicator_ids": list(indicator_ids or []),
                "include_historical": include_historical,
                "historical_periods": historical_periods,
            },
        )

    @staticmethod
    def data_quality_score(org_unit: str, period: str) -> str:
        return make_key("dq", "score", {"org_unit": org_unit, "period": period})

    @staticmethod
    def ai_insight(
        insight_type: str,
        org_unit: str,
        period: str,
        *,
        indicator_id: str | None = None,
        history_depth: str | None = None,
        cascade: str | None = None,
        question: str | None = None,
    ) -> str:
        return make_key(
            "insight",
            insight_type,
            {
                "org_unit": org_unit,
                "period": period,
                "indicator_id": indicator_id,
                "history_depth": history_depth,
                "cascade": cascade,
                "question": question.strip().lower() if question else None,
            },
        )
