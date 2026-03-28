"""
Organisation-unit hierarchy service.

Wraps the Prompt 2 connector methods with hierarchy navigation, breadcrumb,
search, and access-validation logic that stays scoped to the active session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.connectors.cached_connector import build_cached_connector
from app.connectors.schemas import OrgUnit
from app.core.config import load_yaml_config
from app.core.session import UserSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HierarchyLevel:
    """Definition of a configured organisation-unit level."""

    level: int
    name: str
    short_name: str
    can_aggregate_children: bool
    typical_count: int


@dataclass
class OrgUnitNode:
    """Organisation unit plus navigation context for the UI layer."""

    uid: str
    name: str
    level: int
    level_name: str
    parent_uid: Optional[str] = None
    parent_name: Optional[str] = None
    children_count: int = 0
    has_children: bool = False
    is_user_root: bool = False
    is_leaf: bool = False
    path: list[str] = field(default_factory=list)

    @classmethod
    def from_org_unit(
        cls,
        org_unit: OrgUnit,
        level_config: HierarchyLevel,
        *,
        is_user_root: bool = False,
        path: Optional[list[str]] = None,
    ) -> "OrgUnitNode":
        """Create a UI node from an OrgUnit instance."""
        children_count = len(org_unit.children)
        return cls(
            uid=org_unit.uid,
            name=org_unit.name,
            level=org_unit.level or level_config.level,
            level_name=level_config.name,
            parent_uid=org_unit.parent_uid,
            parent_name=org_unit.parent_name,
            children_count=children_count,
            has_children=children_count > 0 or level_config.can_aggregate_children,
            is_user_root=is_user_root,
            is_leaf=not level_config.can_aggregate_children,
            path=path or [],
        )


@dataclass(frozen=True)
class Breadcrumb:
    """Breadcrumb item used in hierarchy navigation."""

    uid: str
    name: str
    level: int
    level_name: str
    is_current: bool = False
    is_clickable: bool = True


@dataclass(frozen=True)
class OrgUnitSearchResult:
    """Search result decorated with hierarchy context."""

    uid: str
    name: str
    level: int
    level_name: str
    path_display: str
    match_score: float = 1.0


class OrgUnitHierarchyConfig:
    """Load and expose the Uganda DHIS2 hierarchy configuration."""

    def __init__(self) -> None:
        self._levels: dict[int, HierarchyLevel] = {}
        self._max_children_display = 50
        self._aggregation_levels: list[int] = [1, 2, 3, 4]
        self._drill_down_levels: list[int] = [1, 2, 3, 4]
        self._loaded = False

    def load(self, filename: str = "org_hierarchy.yaml") -> None:
        """Load hierarchy settings from config, falling back to defaults."""
        try:
            config = load_yaml_config(filename) or {}
        except FileNotFoundError:
            logger.warning("Org hierarchy config missing; using defaults")
            self._load_defaults()
            self._loaded = True
            return

        hierarchy = config.get("hierarchy", {})
        levels = hierarchy.get("levels", {})
        self._levels = {}
        for raw_level, payload in levels.items():
            level = int(raw_level)
            self._levels[level] = HierarchyLevel(
                level=level,
                name=payload.get("name", f"Level {level}"),
                short_name=payload.get("short_name", f"L{level}"),
                can_aggregate_children=payload.get("can_aggregate_children", level < 5),
                typical_count=payload.get("typical_count", 0),
            )

        if not self._levels:
            self._load_defaults()

        self._max_children_display = int(hierarchy.get("max_children_display", 50))
        self._aggregation_levels = [int(level) for level in hierarchy.get("aggregation_levels", [1, 2, 3, 4])]
        self._drill_down_levels = [int(level) for level in hierarchy.get("drill_down_levels", [1, 2, 3, 4])]
        self._loaded = True

    def _load_defaults(self) -> None:
        """Load the default Uganda hierarchy when config is unavailable."""
        defaults = [
            (1, "National", "Nat", True, 1),
            (2, "Regional", "Reg", True, 5),
            (3, "District", "Dist", True, 135),
            (4, "Sub-county/HSD", "HSD", True, 1500),
            (5, "Facility", "Fac", False, 7000),
        ]
        self._levels = {
            level: HierarchyLevel(
                level=level,
                name=name,
                short_name=short_name,
                can_aggregate_children=can_aggregate_children,
                typical_count=typical_count,
            )
            for level, name, short_name, can_aggregate_children, typical_count in defaults
        }
        self._max_children_display = 50
        self._aggregation_levels = [1, 2, 3, 4]
        self._drill_down_levels = [1, 2, 3, 4]

    def get_level(self, level: int) -> HierarchyLevel:
        """Return the configured level or a safe fallback."""
        if not self._loaded:
            self.load()
        return self._levels.get(
            level,
            HierarchyLevel(
                level=level,
                name=f"Level {level}",
                short_name=f"L{level}",
                can_aggregate_children=level < 5,
                typical_count=0,
            ),
        )

    @property
    def max_children_display(self) -> int:
        if not self._loaded:
            self.load()
        return self._max_children_display

    def can_drill_down(self, level: int) -> bool:
        if not self._loaded:
            self.load()
        return level in self._drill_down_levels

    def can_aggregate(self, level: int) -> bool:
        if not self._loaded:
            self.load()
        return level in self._aggregation_levels


_hierarchy_config: Optional[OrgUnitHierarchyConfig] = None


def get_hierarchy_config() -> OrgUnitHierarchyConfig:
    """Return the shared hierarchy configuration."""
    global _hierarchy_config
    if _hierarchy_config is None:
        _hierarchy_config = OrgUnitHierarchyConfig()
        _hierarchy_config.load()
    return _hierarchy_config


class OrgUnitService:
    """Session-scoped org-unit navigation service."""

    def __init__(
        self,
        session: UserSession,
        *,
        config: Optional[OrgUnitHierarchyConfig] = None,
    ) -> None:
        self._session = session
        self._config = config or get_hierarchy_config()
        self._org_unit_cache: dict[str, OrgUnit] = {}
        self._hierarchy_cache: dict[str, dict[str, OrgUnit]] = {}
        self._user_roots: Optional[list[OrgUnit]] = None

    async def get_user_roots(self) -> list[OrgUnitNode]:
        """Return the user's assigned org units as navigation roots."""
        roots = await self._ensure_user_roots()
        nodes = []
        for root in roots:
            level_config = self._config.get_level(root.level or 1)
            nodes.append(
                OrgUnitNode.from_org_unit(
                    root,
                    level_config,
                    is_user_root=True,
                    path=[root.uid],
                )
            )
        return sorted(nodes, key=lambda node: (node.level, node.name.lower()))

    async def get_children(
        self,
        parent_uid: str,
        *,
        include_parent: bool = False,
    ) -> tuple[Optional[OrgUnitNode], list[OrgUnitNode]]:
        """Return a parent's children, capped by max_children_display."""
        parent = await self._fetch_org_unit(parent_uid)
        parent_level = parent.level or 1
        parent_path = await self._build_path(parent_uid)
        parent_node = None

        if include_parent:
            parent_node = OrgUnitNode.from_org_unit(
                parent,
                self._config.get_level(parent_level),
                is_user_root=await self._is_user_root(parent_uid),
                path=parent_path,
            )

        child_level = parent_level + 1
        child_level_config = self._config.get_level(child_level)
        visible_children = sorted(parent.children, key=lambda child: child.name.lower())[
            : self._config.max_children_display
        ]
        child_nodes = [
            OrgUnitNode(
                uid=child.uid,
                name=child.name,
                level=child_level,
                level_name=child_level_config.name,
                parent_uid=parent_uid,
                parent_name=parent.name,
                children_count=0,
                has_children=self._config.can_drill_down(child_level),
                is_user_root=False,
                is_leaf=not child_level_config.can_aggregate_children,
                path=parent_path + [child.uid],
            )
            for child in visible_children
        ]
        return parent_node, child_nodes

    async def get_node_with_context(self, uid: str) -> OrgUnitNode:
        """Return a single org unit enriched with hierarchy context."""
        org_unit = await self._fetch_org_unit(uid)
        return OrgUnitNode.from_org_unit(
            org_unit,
            self._config.get_level(org_unit.level or 1),
            is_user_root=await self._is_user_root(uid),
            path=await self._build_path(uid),
        )

    async def get_breadcrumbs(
        self,
        uid: str,
        *,
        limit_to_user_access: bool = True,
    ) -> list[Breadcrumb]:
        """Return root-to-current breadcrumbs for an org unit."""
        user_roots = {root.uid for root in await self._ensure_user_roots()}
        current_uid = uid
        visited: set[str] = set()
        crumbs: list[Breadcrumb] = []

        while current_uid and current_uid not in visited:
            visited.add(current_uid)
            org_unit = await self._fetch_org_unit(current_uid)
            level = org_unit.level or 0
            crumbs.append(
                Breadcrumb(
                    uid=org_unit.uid,
                    name=org_unit.name,
                    level=level,
                    level_name=self._config.get_level(level).name,
                    is_current=current_uid == uid,
                    is_clickable=current_uid != uid,
                )
            )
            if limit_to_user_access and current_uid in user_roots:
                break
            current_uid = org_unit.parent_uid

        crumbs.reverse()
        return crumbs

    async def search(
        self,
        query: str,
        *,
        root_uid: Optional[str] = None,
        max_results: int = 20,
    ) -> list[OrgUnitSearchResult]:
        """Search accessible org units by name and return breadcrumb context."""
        normalized = query.strip()
        if len(normalized) < 2:
            return []

        if root_uid:
            root_uids = [root_uid]
        else:
            root_uids = [root.uid for root in await self._ensure_user_roots()]

        if not root_uids:
            return []

        results: dict[str, OrgUnitSearchResult] = {}
        for accessible_root in root_uids:
            hierarchy_map = await self._get_hierarchy_map(accessible_root)
            for org_unit in hierarchy_map.values():
                if normalized.lower() not in org_unit.name.lower():
                    continue
                level = org_unit.level or 0
                result = OrgUnitSearchResult(
                    uid=org_unit.uid,
                    name=org_unit.name,
                    level=level,
                    level_name=self._config.get_level(level).name,
                    path_display=self._build_path_display(
                        org_unit.uid,
                        hierarchy_map=hierarchy_map,
                        stop_uid=accessible_root,
                    ),
                    match_score=self._calculate_match_score(org_unit.name, normalized),
                )
                existing = results.get(result.uid)
                if existing is None or result.match_score > existing.match_score:
                    results[result.uid] = result

        ordered = sorted(
            results.values(),
            key=lambda result: (-result.match_score, result.level, result.name.lower()),
        )
        return ordered[:max_results]

    async def validate_user_access(self, uid: str) -> bool:
        """Return True when the requested org unit is within the user's scope."""
        user_roots = await self._ensure_user_roots()
        root_uids = {root.uid for root in user_roots}
        if uid in root_uids:
            return True
        path = await self._build_path(uid)
        return any(root_uid in path for root_uid in root_uids)

    async def _ensure_user_roots(self) -> list[OrgUnit]:
        if self._user_roots is None:
            async with build_cached_connector(self._session) as connector:
                self._user_roots = await connector.get_user_org_units()
            for org_unit in self._user_roots:
                self._org_unit_cache[org_unit.uid] = org_unit
        return self._user_roots or []

    async def _is_user_root(self, uid: str) -> bool:
        return any(root.uid == uid for root in await self._ensure_user_roots())

    async def _fetch_org_unit(self, uid: str) -> OrgUnit:
        cached = self._org_unit_cache.get(uid)
        if cached is not None:
            return cached
        async with build_cached_connector(self._session) as connector:
            org_unit = await connector.get_org_unit(uid)
        self._org_unit_cache[uid] = org_unit
        return org_unit

    async def _get_hierarchy_map(self, root_uid: str) -> dict[str, OrgUnit]:
        cached = self._hierarchy_cache.get(root_uid)
        if cached is not None:
            return cached

        async with build_cached_connector(self._session) as connector:
            hierarchy_units = await connector.get_org_unit_hierarchy(root_uid)

        hierarchy_map = {org_unit.uid: org_unit for org_unit in hierarchy_units}
        if root_uid not in hierarchy_map:
            hierarchy_map[root_uid] = await self._fetch_org_unit(root_uid)
        self._hierarchy_cache[root_uid] = hierarchy_map
        self._org_unit_cache.update(hierarchy_map)
        return hierarchy_map

    async def _build_path(self, uid: str) -> list[str]:
        path: list[str] = []
        visited: set[str] = set()
        current_uid = uid

        while current_uid and current_uid not in visited:
            visited.add(current_uid)
            org_unit = await self._fetch_org_unit(current_uid)
            path.append(org_unit.uid)
            current_uid = org_unit.parent_uid

        path.reverse()
        return path

    def _build_path_display(
        self,
        uid: str,
        *,
        hierarchy_map: dict[str, OrgUnit],
        stop_uid: Optional[str] = None,
    ) -> str:
        ancestor_names: list[str] = []
        current_uid = hierarchy_map.get(uid).parent_uid if uid in hierarchy_map else None
        visited: set[str] = set()

        while current_uid and current_uid not in visited:
            visited.add(current_uid)
            org_unit = hierarchy_map.get(current_uid)
            if org_unit is None:
                org_unit = self._org_unit_cache.get(current_uid)
            if org_unit is None:
                break
            ancestor_names.append(org_unit.name)
            if stop_uid and current_uid == stop_uid:
                break
            current_uid = org_unit.parent_uid

        ancestor_names.reverse()
        return " > ".join(ancestor_names)

    @staticmethod
    def _calculate_match_score(name: str, query: str) -> float:
        lowered_name = name.lower()
        lowered_query = query.lower()
        if lowered_name == lowered_query:
            return 1.0
        if lowered_name.startswith(lowered_query):
            return 0.9
        if lowered_query in lowered_name:
            return 0.7
        return 0.5


def build_org_unit_service(session: UserSession) -> OrgUnitService:
    """Construct an organisation-unit service for the active session."""
    return OrgUnitService(session=session)
