"""
Cross-request caching wrapper for the Prompt 12 org-unit service.
"""

from __future__ import annotations

from typing import Any

from app.core.cache import SessionCache, get_session_cache
from app.core.cache_keys import CacheKeys, get_cache_ttl
from app.services.org_unit_service import (
    Breadcrumb,
    OrgUnitNode,
    OrgUnitSearchResult,
    OrgUnitService,
    build_org_unit_service,
)


class CachedOrgUnitService:
    """Wrap OrgUnitService with session-scoped cross-request caching."""

    def __init__(
        self,
        service: OrgUnitService,
        session_id: str,
        *,
        cache: SessionCache | None = None,
    ) -> None:
        self._service = service
        self._cache = cache or get_session_cache(session_id)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._service, item)

    async def get_user_roots(self, *, use_cache: bool = True) -> list[OrgUnitNode]:
        if not use_cache:
            return await self._service.get_user_roots()
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_user_roots(),
            self._service.get_user_roots,
            ttl=get_cache_ttl("hierarchy"),
        )

    async def get_children(
        self,
        parent_uid: str,
        *,
        include_parent: bool = False,
        use_cache: bool = True,
    ) -> tuple[OrgUnitNode | None, list[OrgUnitNode]]:
        if not use_cache:
            return await self._service.get_children(parent_uid, include_parent=include_parent)
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_children(parent_uid, include_parent),
            lambda: self._service.get_children(parent_uid, include_parent=include_parent),
            ttl=get_cache_ttl("hierarchy"),
        )

    async def get_node_with_context(self, uid: str, *, use_cache: bool = True) -> OrgUnitNode:
        if not use_cache:
            return await self._service.get_node_with_context(uid)
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_node(uid),
            lambda: self._service.get_node_with_context(uid),
            ttl=get_cache_ttl("hierarchy"),
        )

    async def get_breadcrumbs(
        self,
        uid: str,
        *,
        limit_to_user_access: bool = True,
        use_cache: bool = True,
    ) -> list[Breadcrumb]:
        if not use_cache:
            return await self._service.get_breadcrumbs(
                uid,
                limit_to_user_access=limit_to_user_access,
            )
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_breadcrumbs(uid, limit_to_user_access),
            lambda: self._service.get_breadcrumbs(
                uid,
                limit_to_user_access=limit_to_user_access,
            ),
            ttl=get_cache_ttl("hierarchy"),
        )

    async def search(
        self,
        query: str,
        *,
        root_uid: str | None = None,
        max_results: int = 20,
        use_cache: bool = True,
    ) -> list[OrgUnitSearchResult]:
        if not use_cache:
            return await self._service.search(query, root_uid=root_uid, max_results=max_results)
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_search(query, root_uid, max_results),
            lambda: self._service.search(query, root_uid=root_uid, max_results=max_results),
            ttl=get_cache_ttl("hierarchy"),
        )

    async def validate_user_access(self, uid: str, *, use_cache: bool = True) -> bool:
        if not use_cache:
            return await self._service.validate_user_access(uid)
        return await self._cache.get_or_set_async(
            CacheKeys.org_unit_access(uid),
            lambda: self._service.validate_user_access(uid),
            ttl=get_cache_ttl("hierarchy"),
        )

    def invalidate(self) -> int:
        return self._cache.delete_pattern("orgunit:")


def build_cached_org_unit_service(session) -> CachedOrgUnitService:
    """Construct the live cached org-unit service for one authenticated session."""
    return CachedOrgUnitService(
        service=build_org_unit_service(session),
        session_id=session.session_id,
    )
