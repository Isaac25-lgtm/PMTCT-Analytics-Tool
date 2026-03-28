"""
Caching wrapper around the live DHIS2Connector.

The wrapper preserves the real connector interface used in this repo instead of
inventing Prompt 14-only methods.
"""

from __future__ import annotations

from typing import Any, Optional

from app.connectors.dhis2_connector import DHIS2Connector
from app.connectors.schemas import (
    AnalyticsResponse,
    CategoryOptionComboMeta,
    CompletionStatus,
    DataElementMeta,
    OrgUnit,
)
from app.core.cache import InMemoryCache, SessionCache, get_app_cache, get_session_cache
from app.core.cache_keys import CacheKeys, get_cache_ttl


class CachedDHIS2Connector:
    """Transparent cache wrapper for metadata and read-only data requests."""

    def __init__(
        self,
        connector: DHIS2Connector,
        session_id: str,
        *,
        app_cache: InMemoryCache | None = None,
        session_cache: SessionCache | None = None,
    ) -> None:
        self._connector = connector
        self._app_cache = app_cache or get_app_cache()
        self._session_cache = session_cache or get_session_cache(session_id)

    async def __aenter__(self) -> "CachedDHIS2Connector":
        await self._connector.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._connector.__aexit__(exc_type, exc_val, exc_tb)

    async def close(self) -> None:
        await self._connector.close()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._connector, item)

    async def get_data_values(
        self,
        data_elements: list[str],
        org_unit: str,
        period: str,
        include_children: bool = False,
        *,
        use_cache: bool = True,
    ) -> dict[str, float | None]:
        if not use_cache:
            return await self._connector.get_data_values(
                data_elements=data_elements,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            )
        key = CacheKeys.data_values(data_elements, org_unit, period, include_children)
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_data_values(
                data_elements=data_elements,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            ),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_data_value(
        self,
        data_element: str,
        org_unit: str,
        period: str,
        include_children: bool = False,
        *,
        use_cache: bool = True,
    ) -> float | None:
        if not use_cache:
            return await self._connector.get_data_value(
                data_element=data_element,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            )
        key = CacheKeys.data_value(data_element, org_unit, period, include_children)
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_data_value(
                data_element=data_element,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            ),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_disaggregated_values(
        self,
        data_element: str,
        category_option_combos: list[str],
        org_unit: str,
        period: str,
        *,
        use_cache: bool = True,
    ) -> dict[str, float | None]:
        if not use_cache:
            return await self._connector.get_disaggregated_values(
                data_element=data_element,
                category_option_combos=category_option_combos,
                org_unit=org_unit,
                period=period,
            )
        key = CacheKeys.disaggregated_values(
            data_element,
            category_option_combos,
            org_unit,
            period,
        )
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_disaggregated_values(
                data_element=data_element,
                category_option_combos=category_option_combos,
                org_unit=org_unit,
                period=period,
            ),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_an21_pos_total(
        self,
        org_unit: str,
        period: str,
        *,
        use_cache: bool = True,
    ) -> float:
        if not use_cache:
            return await self._connector.get_an21_pos_total(org_unit=org_unit, period=period)
        key = CacheKeys.an21_pos_total(org_unit, period)
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_an21_pos_total(org_unit=org_unit, period=period),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_analytics(
        self,
        data_elements: list[str],
        org_units: list[str],
        periods: list[str],
        include_children: bool = False,
        *,
        use_cache: bool = True,
    ) -> AnalyticsResponse:
        if not use_cache:
            return await self._connector.get_analytics(
                data_elements=data_elements,
                org_units=org_units,
                periods=periods,
                include_children=include_children,
            )
        key = CacheKeys.analytics(data_elements, org_units, periods, include_children)
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_analytics(
                data_elements=data_elements,
                org_units=org_units,
                periods=periods,
                include_children=include_children,
            ),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_reporting_completeness(
        self,
        dataset_uid: str,
        org_unit: str,
        period: str,
        include_children: bool = False,
        *,
        use_cache: bool = True,
    ) -> CompletionStatus:
        if not use_cache:
            return await self._connector.get_reporting_completeness(
                dataset_uid=dataset_uid,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            )
        key = CacheKeys.reporting_completeness(
            dataset_uid,
            org_unit,
            period,
            include_children,
        )
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.get_reporting_completeness(
                dataset_uid=dataset_uid,
                org_unit=org_unit,
                period=period,
                include_children=include_children,
            ),
            ttl=get_cache_ttl("aggregate"),
        )

    async def get_data_element(self, uid: str, *, use_cache: bool = True) -> DataElementMeta:
        if not use_cache:
            return await self._connector.get_data_element(uid)
        key = CacheKeys.data_element_meta(uid)
        return await self._app_cache.get_or_set_async(
            key,
            lambda: self._connector.get_data_element(uid),
            ttl=get_cache_ttl("metadata"),
        )

    async def get_category_option_combo(
        self,
        uid: str,
        *,
        use_cache: bool = True,
    ) -> CategoryOptionComboMeta:
        if not use_cache:
            return await self._connector.get_category_option_combo(uid)
        key = CacheKeys.category_option_combo(uid)
        return await self._app_cache.get_or_set_async(
            key,
            lambda: self._connector.get_category_option_combo(uid),
            ttl=get_cache_ttl("metadata"),
        )

    async def validate_uids(
        self,
        uids: list[str],
        *,
        use_cache: bool = True,
    ) -> dict[str, bool | str]:
        if not use_cache:
            return await self._connector.validate_uids(uids)
        key = CacheKeys.validate_uids(uids)
        return await self._app_cache.get_or_set_async(
            key,
            lambda: self._connector.validate_uids(uids),
            ttl=get_cache_ttl("metadata"),
        )

    async def get_org_unit(self, uid: str, *, use_cache: bool = True) -> OrgUnit:
        if not use_cache:
            return await self._connector.get_org_unit(uid)
        key = CacheKeys.org_unit_metadata(uid)
        return await self._app_cache.get_or_set_async(
            key,
            lambda: self._connector.get_org_unit(uid),
            ttl=get_cache_ttl("metadata"),
        )

    async def get_org_unit_hierarchy(
        self,
        root_uid: str,
        max_level: Optional[int] = None,
        *,
        use_cache: bool = True,
    ) -> list[OrgUnit]:
        if not use_cache:
            return await self._connector.get_org_unit_hierarchy(
                root_uid=root_uid,
                max_level=max_level,
            )
        key = CacheKeys.org_unit_hierarchy(root_uid, max_level)
        return await self._app_cache.get_or_set_async(
            key,
            lambda: self._connector.get_org_unit_hierarchy(root_uid=root_uid, max_level=max_level),
            ttl=get_cache_ttl("hierarchy"),
        )

    async def get_user_org_units(self) -> list[OrgUnit]:
        return await self._connector.get_user_org_units()

    async def search_org_units(
        self,
        query: str,
        *,
        max_results: int = 20,
        use_cache: bool = True,
    ) -> list[OrgUnit]:
        if not use_cache:
            return await self._connector.search_org_units(query, max_results=max_results)
        key = CacheKeys.org_unit_search(query, None, max_results)
        return await self._session_cache.get_or_set_async(
            key,
            lambda: self._connector.search_org_units(query, max_results=max_results),
            ttl=get_cache_ttl("hierarchy"),
        )

    def invalidate_metadata(self) -> int:
        deleted = 0
        deleted += self._app_cache.delete_pattern("orgunit:")
        deleted += self._app_cache.delete_pattern("metadata:")
        return deleted

    def invalidate_session_data(self) -> int:
        return self._session_cache.delete_pattern("dhis2:")


def build_cached_connector(session) -> CachedDHIS2Connector:
    """Construct the live cached connector for one authenticated session."""
    return CachedDHIS2Connector(
        connector=DHIS2Connector(session),
        session_id=session.session_id,
    )
