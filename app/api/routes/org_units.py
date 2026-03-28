"""
Organisation-unit hierarchy API routes.
"""

from __future__ import annotations

import html
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.deps import CurrentSession, OrgUnitSvc, require_permission
from app.auth.audit import get_audit_logger
from app.auth.permissions import Permission
from app.services.org_unit_service import Breadcrumb, OrgUnitNode, OrgUnitSearchResult, get_hierarchy_config

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/org-units",
    tags=["Org Units"],
    dependencies=[Depends(require_permission(Permission.VIEW_ORG_UNITS))],
)
templates = Jinja2Templates(directory="app/templates")


def render_error(message: str, *, status_code: int = 400) -> HTMLResponse:
    """Return a lightweight error block for HTMX callers."""
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<div class='rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800'>"
            f"{html.escape(message)}"
            "</div>"
        ),
    )


class OrgUnitNodeResponse(BaseModel):
    """Serialised org-unit node."""

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
    path: list[str] = Field(default_factory=list)

    @classmethod
    def from_node(cls, node: OrgUnitNode) -> "OrgUnitNodeResponse":
        return cls(
            uid=node.uid,
            name=node.name,
            level=node.level,
            level_name=node.level_name,
            parent_uid=node.parent_uid,
            parent_name=node.parent_name,
            children_count=node.children_count,
            has_children=node.has_children,
            is_user_root=node.is_user_root,
            is_leaf=node.is_leaf,
            path=node.path,
        )


class BreadcrumbResponse(BaseModel):
    """Serialised breadcrumb item."""

    uid: str
    name: str
    level: int
    level_name: str
    is_current: bool
    is_clickable: bool

    @classmethod
    def from_breadcrumb(cls, breadcrumb: Breadcrumb) -> "BreadcrumbResponse":
        return cls(
            uid=breadcrumb.uid,
            name=breadcrumb.name,
            level=breadcrumb.level,
            level_name=breadcrumb.level_name,
            is_current=breadcrumb.is_current,
            is_clickable=breadcrumb.is_clickable,
        )


class SearchResultResponse(BaseModel):
    """Serialised search result."""

    uid: str
    name: str
    level: int
    level_name: str
    path_display: str
    match_score: float

    @classmethod
    def from_result(cls, result: OrgUnitSearchResult) -> "SearchResultResponse":
        return cls(
            uid=result.uid,
            name=result.name,
            level=result.level,
            level_name=result.level_name,
            path_display=result.path_display,
            match_score=result.match_score,
        )


class OrgUnitListResponse(BaseModel):
    """List of org-unit nodes."""

    org_units: list[OrgUnitNodeResponse]
    total_count: int


class ChildrenResponse(BaseModel):
    """Children response with truncation metadata."""

    parent: Optional[OrgUnitNodeResponse] = None
    children: list[OrgUnitNodeResponse]
    can_drill_down: bool
    children_count: int
    returned_count: int
    truncated: bool = False
    max_children_display: int


class BreadcrumbsResponse(BaseModel):
    """Breadcrumb response."""

    breadcrumbs: list[BreadcrumbResponse]
    current_uid: str


class SearchResponse(BaseModel):
    """Search response."""

    results: list[SearchResultResponse]
    query: str
    total_count: int


@router.get("/roots", response_model=OrgUnitListResponse)
async def get_user_roots(service: OrgUnitSvc) -> OrgUnitListResponse:
    """Return the user's accessible root org units."""
    nodes = await service.get_user_roots()
    return OrgUnitListResponse(
        org_units=[OrgUnitNodeResponse.from_node(node) for node in nodes],
        total_count=len(nodes),
    )


@router.get("/search", response_model=SearchResponse)
async def search_org_units(
    session: CurrentSession,
    service: OrgUnitSvc,
    q: str = Query(..., min_length=2, description="Search query"),
    root_uid: str | None = Query(default=None, description="Optional accessible root to constrain the search"),
    max_results: int = Query(default=20, ge=1, le=100),
) -> SearchResponse:
    """Search org units across all accessible roots or within a validated root."""
    if root_uid and not await service.validate_user_access(root_uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=root_uid,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organisation unit",
        )

    results = await service.search(
        q,
        root_uid=root_uid,
        max_results=max_results,
    )
    return SearchResponse(
        results=[SearchResultResponse.from_result(result) for result in results],
        query=q,
        total_count=len(results),
    )


@router.get("/htmx/selector", response_class=HTMLResponse, include_in_schema=False)
async def htmx_org_unit_selector(
    request: Request,
    service: OrgUnitSvc,
    selected_uid: str | None = Query(default=None),
    input_name: str = Query(default="org_unit"),
    component_id: str | None = Query(default=None),
    include_children_option: bool = Query(default=True),
) -> HTMLResponse:
    """Render the hierarchy selector component."""
    component_id = component_id or f"{input_name}-selector"
    roots = await service.get_user_roots()
    selected_node = None
    breadcrumbs: list[Breadcrumb] = []

    if selected_uid and await service.validate_user_access(selected_uid):
        selected_node = await service.get_node_with_context(selected_uid)
        breadcrumbs = await service.get_breadcrumbs(selected_uid)

    return templates.TemplateResponse(
        request,
        "components/org_unit_selector.html",
        {
            "request": request,
            "roots": roots,
            "selected_node": selected_node,
            "breadcrumbs": breadcrumbs,
            "input_name": input_name,
            "component_id": component_id,
            "include_children_option": include_children_option,
        },
    )


@router.get("/htmx/children/{uid}", response_class=HTMLResponse, include_in_schema=False)
async def htmx_children_list(
    request: Request,
    uid: str,
    session: CurrentSession,
    service: OrgUnitSvc,
    input_name: str = Query(default="org_unit"),
    component_id: str | None = Query(default=None),
) -> HTMLResponse:
    """Render a child list for drill-down navigation."""
    if not await service.validate_user_access(uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=uid,
        )
        return render_error("Access denied for this organisation unit.", status_code=403)

    component_id = component_id or f"{input_name}-selector"
    parent_node, children = await service.get_children(uid, include_parent=True)
    breadcrumbs = await service.get_breadcrumbs(uid)
    config = get_hierarchy_config()
    total_children = parent_node.children_count if parent_node else len(children)

    return templates.TemplateResponse(
        request,
        "components/org_unit_children.html",
        {
            "request": request,
            "parent": parent_node,
            "children": children,
            "breadcrumbs": breadcrumbs,
            "input_name": input_name,
            "component_id": component_id,
            "truncated": total_children > len(children),
            "max_children_display": config.max_children_display,
        },
    )


@router.get("/htmx/breadcrumbs/{uid}", response_class=HTMLResponse, include_in_schema=False)
async def htmx_breadcrumbs(
    request: Request,
    uid: str,
    session: CurrentSession,
    service: OrgUnitSvc,
    input_name: str = Query(default="org_unit"),
    component_id: str | None = Query(default=None),
) -> HTMLResponse:
    """Render the breadcrumb partial for the requested org unit."""
    if not await service.validate_user_access(uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=uid,
        )
        return HTMLResponse(status_code=403, content="")

    component_id = component_id or f"{input_name}-selector"
    breadcrumbs = await service.get_breadcrumbs(uid)
    return templates.TemplateResponse(
        request,
        "components/org_unit_breadcrumbs.html",
        {
            "request": request,
            "breadcrumbs": breadcrumbs,
            "input_name": input_name,
            "component_id": component_id,
        },
    )


@router.get("/htmx/search-results", response_class=HTMLResponse, include_in_schema=False)
async def htmx_search_results(
    request: Request,
    session: CurrentSession,
    service: OrgUnitSvc,
    q: str = Query(default="", min_length=0),
    root_uid: str | None = Query(default=None),
    input_name: str = Query(default="org_unit"),
    component_id: str | None = Query(default=None),
) -> HTMLResponse:
    """Render accessible search results for the selector modal."""
    component_id = component_id or f"{input_name}-selector"
    if not q or len(q.strip()) < 2:
        return HTMLResponse(content="")

    if root_uid and not await service.validate_user_access(root_uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=root_uid,
        )
        return render_error("Access denied for the selected search scope.", status_code=403)

    results = await service.search(q, root_uid=root_uid, max_results=15)
    return templates.TemplateResponse(
        request,
        "components/org_unit_search_results.html",
        {
            "request": request,
            "results": results,
            "query": q,
            "input_name": input_name,
            "component_id": component_id,
        },
    )


@router.get("/{uid}", response_model=OrgUnitNodeResponse)
async def get_org_unit(
    uid: str,
    session: CurrentSession,
    service: OrgUnitSvc,
) -> OrgUnitNodeResponse:
    """Return a single org unit enriched with hierarchy context."""
    if not await service.validate_user_access(uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=uid,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organisation unit",
        )
    node = await service.get_node_with_context(uid)
    return OrgUnitNodeResponse.from_node(node)


@router.get("/{uid}/children", response_model=ChildrenResponse)
async def get_children(
    uid: str,
    session: CurrentSession,
    service: OrgUnitSvc,
    include_parent: bool = Query(default=False),
) -> ChildrenResponse:
    """Return a limited list of children for an accessible org unit."""
    if not await service.validate_user_access(uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=uid,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organisation unit",
        )

    parent_node, children = await service.get_children(uid, include_parent=True)
    config = get_hierarchy_config()
    total_children = parent_node.children_count if parent_node else len(children)
    parent_level = parent_node.level if parent_node else 0

    return ChildrenResponse(
        parent=OrgUnitNodeResponse.from_node(parent_node) if include_parent and parent_node else None,
        children=[OrgUnitNodeResponse.from_node(child) for child in children],
        can_drill_down=config.can_drill_down(parent_level),
        children_count=total_children,
        returned_count=len(children),
        truncated=total_children > len(children),
        max_children_display=config.max_children_display,
    )


@router.get("/{uid}/breadcrumbs", response_model=BreadcrumbsResponse)
async def get_breadcrumbs(
    uid: str,
    session: CurrentSession,
    service: OrgUnitSvc,
) -> BreadcrumbsResponse:
    """Return breadcrumbs for an accessible org unit."""
    if not await service.validate_user_access(uid):
        credentials = session.credentials
        get_audit_logger().log_org_unit_access_denied(
            user_id=credentials.user_id or "unknown",
            username=credentials.user_name or credentials.username or "unknown",
            org_unit_uid=uid,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organisation unit",
        )

    breadcrumbs = await service.get_breadcrumbs(uid)
    return BreadcrumbsResponse(
        breadcrumbs=[BreadcrumbResponse.from_breadcrumb(crumb) for crumb in breadcrumbs],
        current_uid=uid,
    )
