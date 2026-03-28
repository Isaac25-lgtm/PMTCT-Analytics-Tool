"""Admin routes for diagnostics, cache control, and config validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.config_validator import ConfigValidator
from app.admin.diagnostics import SystemDiagnostics
from app.api.deps import (
    AppCache,
    Audit,
    CurrentSession,
    OptionalSession,
    require_permission,
)
from app.api.routes.pages import build_page_context
from app.auth.permissions import Permission, check_permission
from app.auth.roles import get_role_from_session
from app.core.cache import clear_all_caches, get_app_cache, get_session_store
from app.core.session import get_session_manager

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")


def _is_admin(session: OptionalSession) -> bool:
    if not session:
        return False
    role_info = get_role_from_session(session)
    if not role_info:
        return False
    return check_permission(role_info, Permission.SYSTEM_ADMIN).granted


def _namespace_counts(
    cache_keys: list[str],
    *,
    session_store: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in cache_keys:
        if session_store:
            parts = key.split(":", 2)
            namespace = (
                parts[2].split(":", 1)[0]
                if len(parts) == 3 and parts[2]
                else "default"
            )
        else:
            namespace = key.split(":", 1)[0] if ":" in key else "default"
        counts[namespace] = counts.get(namespace, 0) + 1
    return counts


def _clear_namespace(namespace: str) -> dict[str, int]:
    app_cache = get_app_cache()
    session_store = get_session_store()
    app_count = app_cache.delete_pattern(f"{namespace}:")

    session_count = 0
    for key in session_store.keys():
        parts = key.split(":", 2)
        if len(parts) == 3 and parts[2].startswith(f"{namespace}:"):
            if session_store.delete(key):
                session_count += 1

    return {
        "app": app_count,
        "sessions": session_count,
    }


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    session: OptionalSession,
) -> HTMLResponse:
    """Render the admin dashboard for system administrators."""
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    if not _is_admin(session):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    diagnostics = SystemDiagnostics()
    validator = ConfigValidator()
    diagnostics_snapshot = await diagnostics.get_system_status()
    context = build_page_context(request, session)
    context.update(
        {
            "active_page": "admin",
            "system_status": diagnostics_snapshot,
            "dhis2_probe": {
                "status": (
                    "configured"
                    if diagnostics_snapshot["configuration"]["dhis2_configured"]
                    else "not_configured"
                ),
            },
            "config_summary": validator.summarize(),
            "config_results": validator.validate_all(),
        }
    )
    return templates.TemplateResponse(request, "admin.html", context)


@router.get(
    "/status",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def system_status(
    _session: CurrentSession,
) -> dict[str, Any]:
    """Return current diagnostics and DHIS2 reachability."""
    diagnostics = SystemDiagnostics()
    payload = await diagnostics.get_system_status()
    payload["dhis2"] = await diagnostics.check_dhis2_connectivity()
    return payload


@router.get(
    "/cache",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def cache_details(
    _session: CurrentSession,
    cache: AppCache,
) -> dict[str, Any]:
    """Return app and session-store cache statistics."""
    session_store = get_session_store()
    return {
        "application": {
            "enabled": cache.enabled,
            "max_size": cache.max_size,
            "default_ttl": cache.default_ttl,
            "stats": cache.stats.to_dict(),
            "namespaces": _namespace_counts(cache.keys()),
        },
        "session_store": {
            "enabled": session_store.enabled,
            "max_size": session_store.max_size,
            "default_ttl": session_store.default_ttl,
            "stats": session_store.stats.to_dict(),
            "namespaces": _namespace_counts(session_store.keys(), session_store=True),
        },
    }


@router.post(
    "/cache/clear",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def clear_cache(
    session: CurrentSession,
    audit: Audit,
    namespace: str | None = Query(
        default=None,
        description="Optional cache namespace prefix",
    ),
) -> dict[str, Any]:
    """Clear all caches or one namespace across app and session stores."""
    if namespace:
        cleared = _clear_namespace(namespace)
        total = sum(cleared.values())
        audit.log_cache_cleared(
            user_id=session.credentials.user_id or "unknown",
            username=session.credentials.user_name or session.credentials.username or "unknown",
            scope="namespace",
            cleared_count=total,
            namespace=namespace,
        )
        return {
            "message": f"Cleared namespace '{namespace}'",
            "namespace": namespace,
            "cleared": cleared,
            "total_cleared": total,
        }

    cleared = clear_all_caches()
    total = sum(cleared.values())
    audit.log_cache_cleared(
        user_id=session.credentials.user_id or "unknown",
        username=session.credentials.user_name or session.credentials.username or "unknown",
        scope="all",
        cleared_count=total,
    )
    return {
        "message": "Cleared all caches",
        "cleared": cleared,
        "total_cleared": total,
    }


@router.get(
    "/config/validate",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def validate_config(
    session: CurrentSession,
    audit: Audit,
) -> dict[str, Any]:
    """Validate all repository YAML configuration files."""
    validator = ConfigValidator()
    results = validator.validate_all()
    summary = validator.summarize()
    audit.log_config_validated(
        user_id=session.credentials.user_id or "unknown",
        username=session.credentials.user_name or session.credentials.username or "unknown",
        valid=bool(summary["valid"]),
        files_checked=int(summary["files_checked"]),
        error_count=int(summary["error_count"]),
        warning_count=int(summary["warning_count"]),
    )
    return {
        "summary": summary,
        "results": results,
    }


@router.get(
    "/sessions",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def active_sessions(
    session: CurrentSession,
) -> dict[str, Any]:
    """Return active session summaries for administrators."""
    manager = get_session_manager()
    items = []
    for session_id, user_session in manager._sessions.items():
        credentials = user_session.credentials
        role_info = get_role_from_session(user_session)
        items.append(
            {
                "session_id": session_id,
                "is_current": session_id == session.session_id,
                "user_name": credentials.user_name if credentials else None,
                "user_id": credentials.user_id if credentials else None,
                "role": role_info.role.value if role_info else None,
                "created_at": user_session.created_at.isoformat(),
                "expires_at": user_session.expires_at.isoformat(),
                "org_unit_count": len(credentials.org_units) if credentials else 0,
            }
        )
    return {
        "active_sessions": len(items),
        "sessions": sorted(items, key=lambda item: item["created_at"]),
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/sessions/{session_id}/terminate",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def terminate_session(
    session_id: str,
    session: CurrentSession,
    audit: Audit,
) -> dict[str, Any]:
    """Terminate another user's in-memory session."""
    if session_id == session.session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot terminate the current session",
        )

    manager = get_session_manager()
    user_session = manager.peek_session(session_id)
    if user_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    terminated_username = (
        user_session.credentials.user_name
        if user_session.credentials
        else None
    )
    manager.destroy_session(session_id)
    audit.log_session_terminated(
        user_id=session.credentials.user_id or "unknown",
        username=session.credentials.user_name or session.credentials.username or "unknown",
        terminated_session_id=session_id,
        terminated_username=terminated_username,
    )
    return {
        "message": "Session terminated",
        "session_id": session_id,
    }
