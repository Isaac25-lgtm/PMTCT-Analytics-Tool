"""
Unit tests for the organisation-unit hierarchy service.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.schemas import OrgUnit
from app.services.org_unit_service import OrgUnitHierarchyConfig, OrgUnitNode, OrgUnitService


def _mock_cached_connector(connector: AsyncMock) -> AsyncMock:
    """Wrap a mocked connector as the cached connector context manager."""
    connector.__aenter__ = AsyncMock(return_value=connector)
    connector.__aexit__ = AsyncMock(return_value=False)
    return connector


@pytest.fixture
def hierarchy_config() -> OrgUnitHierarchyConfig:
    """Return a default hierarchy config without touching the shared singleton."""
    config = OrgUnitHierarchyConfig()
    config._load_defaults()
    config._loaded = True
    return config


@pytest.fixture
def sample_hierarchy() -> dict[str, OrgUnit]:
    """Sample five-level org-unit hierarchy for Uganda."""
    return {
        "region": OrgUnit(
            uid="region1",
            name="Central Region",
            level=2,
            children=[
                OrgUnit(uid="district1", name="Kampala District"),
                OrgUnit(uid="district2", name="Wakiso District"),
            ],
        ),
        "district": OrgUnit(
            uid="district1",
            name="Kampala District",
            level=3,
            parent_uid="region1",
            parent_name="Central Region",
            children=[OrgUnit(uid="hsd1", name="Makindye HSD")],
        ),
        "hsd": OrgUnit(
            uid="hsd1",
            name="Makindye HSD",
            level=4,
            parent_uid="district1",
            parent_name="Kampala District",
            children=[OrgUnit(uid="facility1", name="Mulago Hospital")],
        ),
        "facility": OrgUnit(
            uid="facility1",
            name="Mulago Hospital",
            level=5,
            parent_uid="hsd1",
            parent_name="Makindye HSD",
            children=[],
        ),
    }


@pytest.mark.unit
class TestOrgUnitHierarchyConfig:
    def test_default_levels_are_available(self, hierarchy_config: OrgUnitHierarchyConfig) -> None:
        assert hierarchy_config.get_level(1).name == "National"
        assert hierarchy_config.get_level(5).name == "Facility"

    def test_drill_down_stops_at_facility(self, hierarchy_config: OrgUnitHierarchyConfig) -> None:
        assert hierarchy_config.can_drill_down(4) is True
        assert hierarchy_config.can_drill_down(5) is False


@pytest.mark.unit
class TestOrgUnitService:
    @pytest.mark.asyncio
    async def test_get_user_roots_marks_assigned_units(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
    ) -> None:
        connector = _mock_cached_connector(AsyncMock())
        connector.get_user_org_units.return_value = [
            OrgUnit(uid="district1", name="Kampala District", level=3),
        ]

        with patch("app.services.org_unit_service.build_cached_connector", return_value=connector):
            service = OrgUnitService(valid_session, config=hierarchy_config)
            roots = await service.get_user_roots()

        assert len(roots) == 1
        assert roots[0].uid == "district1"
        assert roots[0].is_user_root is True
        assert roots[0].level_name == "District"

    @pytest.mark.asyncio
    async def test_search_across_all_user_roots(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
    ) -> None:
        connector = _mock_cached_connector(AsyncMock())
        connector.search_org_units.return_value = [
            OrgUnit(
                uid="facility2",
                name="Entebbe Hospital",
                level=5,
                parent_uid="district2",
                parent_name="Wakiso District",
            ),
        ]
        org_unit_map = {
            "facility2": OrgUnit(uid="facility2", name="Entebbe Hospital", level=5, parent_uid="district2"),
            "district2": OrgUnit(uid="district2", name="Wakiso District", level=3, parent_uid="region1"),
            "region1": OrgUnit(uid="region1", name="Central Region", level=2),
        }

        async def get_org_unit(uid: str) -> OrgUnit:
            return org_unit_map[uid]

        connector.get_org_unit.side_effect = get_org_unit

        with patch("app.services.org_unit_service.build_cached_connector", return_value=connector):
            service = OrgUnitService(valid_session, config=hierarchy_config)
            results = await service.search("Entebbe")

        assert len(results) == 1
        assert results[0].uid == "facility2"
        assert results[0].name == "Entebbe Hospital"

    @pytest.mark.asyncio
    async def test_search_builds_full_path_display(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
        sample_hierarchy: dict[str, OrgUnit],
    ) -> None:
        connector = _mock_cached_connector(AsyncMock())
        connector.search_org_units.return_value = [sample_hierarchy["facility"]]
        org_unit_map = {
            "facility1": sample_hierarchy["facility"],
            "hsd1": sample_hierarchy["hsd"],
            "district1": sample_hierarchy["district"],
            "region1": sample_hierarchy["region"],
        }

        async def get_org_unit(uid: str) -> OrgUnit:
            return org_unit_map[uid]

        connector.get_org_unit.side_effect = get_org_unit

        with patch("app.services.org_unit_service.build_cached_connector", return_value=connector):
            service = OrgUnitService(valid_session, config=hierarchy_config)
            results = await service.search("Mulago")

        assert len(results) == 1
        assert results[0].path_display == "Central Region > Kampala District > Makindye HSD"

    @pytest.mark.asyncio
    async def test_get_children_enforces_max_children_display(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
    ) -> None:
        hierarchy_config._max_children_display = 1
        parent = OrgUnit(
            uid="region1",
            name="Central Region",
            level=2,
            children=[
                OrgUnit(uid="district1", name="Kampala District"),
                OrgUnit(uid="district2", name="Wakiso District"),
            ],
        )

        connector = _mock_cached_connector(AsyncMock())
        connector.get_org_unit.return_value = parent

        with patch("app.services.org_unit_service.build_cached_connector", return_value=connector):
            service = OrgUnitService(valid_session, config=hierarchy_config)
            parent_node, children = await service.get_children("region1", include_parent=True)

        assert parent_node is not None
        assert parent_node.children_count == 2
        assert len(children) == 1
        assert children[0].uid == "district1"

    @pytest.mark.asyncio
    async def test_validate_user_access_for_descendant(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
        sample_hierarchy: dict[str, OrgUnit],
    ) -> None:
        connector = _mock_cached_connector(AsyncMock())
        connector.get_user_org_units.return_value = [
            OrgUnit(uid="district1", name="Kampala District", level=3),
        ]

        async def get_org_unit(uid: str) -> OrgUnit:
            mapping = {
                "facility1": sample_hierarchy["facility"],
                "hsd1": sample_hierarchy["hsd"],
                "district1": sample_hierarchy["district"],
            }
            return mapping[uid]

        connector.get_org_unit.side_effect = get_org_unit

        with patch("app.services.org_unit_service.build_cached_connector", return_value=connector):
            service = OrgUnitService(valid_session, config=hierarchy_config)
            has_access = await service.validate_user_access("facility1")

        assert has_access is True

    @pytest.mark.asyncio
    async def test_validate_user_access_allows_globally_visible_org_unit(
        self,
        valid_session,
        hierarchy_config: OrgUnitHierarchyConfig,
    ) -> None:
        service = OrgUnitService(valid_session, config=hierarchy_config)
        service._ensure_user_roots = AsyncMock(
            return_value=[OrgUnit(uid="district1", name="Kampala District", level=3)]
        )
        service._build_path = AsyncMock(return_value=["region9", "district9"])
        service._fetch_org_unit = AsyncMock(
            return_value=OrgUnit(uid="district9", name="Gulu District", level=3)
        )

        has_access = await service.validate_user_access("district9")

        assert has_access is True


@pytest.mark.unit
class TestOrgUnitNode:
    def test_from_org_unit_populates_context(self, hierarchy_config: OrgUnitHierarchyConfig) -> None:
        org_unit = OrgUnit(
            uid="district1",
            name="Kampala District",
            level=3,
            parent_uid="region1",
            parent_name="Central Region",
            children=[],
        )

        node = OrgUnitNode.from_org_unit(
            org_unit,
            hierarchy_config.get_level(3),
            is_user_root=True,
            path=["region1", "district1"],
        )

        assert node.uid == "district1"
        assert node.level_name == "District"
        assert node.is_user_root is True
        assert node.path == ["region1", "district1"]
