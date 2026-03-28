"""
API tests for organisation-unit hierarchy routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.deps as deps
from app.services.org_unit_service import Breadcrumb, OrgUnitNode, OrgUnitSearchResult


def make_service_mock() -> MagicMock:
    """Create a reusable org-unit service mock."""
    service = MagicMock()
    service.get_user_roots = AsyncMock(return_value=[])
    service.validate_user_access = AsyncMock(return_value=True)
    service.search = AsyncMock(return_value=[])
    service.get_node_with_context = AsyncMock(return_value=None)
    service.get_children = AsyncMock(return_value=(None, []))
    service.get_breadcrumbs = AsyncMock(return_value=[])
    return service


@pytest.mark.api
class TestOrgUnitRoutes:
    def test_roots_require_auth(self, client) -> None:
        response = client.get("/api/org-units/roots")

        assert response.status_code == 401

    def test_search_route_is_not_shadowed_by_uid(self, authenticated_client) -> None:
        service = make_service_mock()
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get("/api/org-units/search", params={"q": "Mulago"})
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == {"results": [], "query": "Mulago", "total_count": 0}
        service.search.assert_awaited_once()

    def test_search_validates_root_uid_access(self, authenticated_client) -> None:
        service = make_service_mock()
        service.validate_user_access = AsyncMock(return_value=False)
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get(
                "/api/org-units/search",
                params={"q": "Mulago", "root_uid": "blocked-root"},
            )
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 403
        assert "do not have access" in response.json()["detail"].lower()
        service.search.assert_not_called()

    def test_children_route_validates_access(self, authenticated_client) -> None:
        service = make_service_mock()
        service.validate_user_access = AsyncMock(return_value=False)
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get("/api/org-units/district1/children")
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 403

    def test_selector_partial_renders_separate_open_and_select_actions(self, authenticated_client) -> None:
        service = make_service_mock()
        service.get_user_roots = AsyncMock(
            return_value=[
                OrgUnitNode(
                    uid="district1",
                    name="Kampala District",
                    level=3,
                    level_name="District",
                    children_count=12,
                    has_children=True,
                )
            ]
        )
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get("/api/org-units/htmx/selector")
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Browse hierarchy" in response.text
        assert "Open" in response.text
        assert "Select" in response.text
        assert "event.currentTarget" not in response.text

    def test_search_results_partial_returns_html(self, authenticated_client) -> None:
        service = make_service_mock()
        service.search = AsyncMock(
            return_value=[
                OrgUnitSearchResult(
                    uid="facility1",
                    name="Mulago Hospital",
                    level=5,
                    level_name="Facility",
                    path_display="Central Region > Kampala District > Mulago Division",
                    match_score=0.9,
                )
            ]
        )
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get(
                "/api/org-units/htmx/search-results",
                params={"q": "Mulago"},
            )
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Mulago Hospital" in response.text
        assert "Central Region &gt; Kampala District &gt; Mulago Division" in response.text

    def test_breadcrumb_partial_keeps_input_name_contract(self, authenticated_client) -> None:
        service = make_service_mock()
        service.get_breadcrumbs = AsyncMock(
            return_value=[
                Breadcrumb(uid="district1", name="Kampala District", level=3, level_name="District"),
                Breadcrumb(uid="hsd1", name="Makindye HSD", level=4, level_name="Sub-county/HSD", is_current=True),
            ]
        )
        authenticated_client.app.dependency_overrides[deps.get_org_unit_service] = lambda: service

        try:
            response = authenticated_client.get(
                "/api/org-units/htmx/breadcrumbs/hsd1",
                params={"input_name": "filter_org_unit", "component_id": "filter-selector"},
            )
        finally:
            authenticated_client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert "filter_org_unit" in response.text
        assert "filter-selector-list" in response.text
