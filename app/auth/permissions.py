"""
Permission definitions and role-based permission checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import logging
from typing import Optional

from app.auth.roles import Role, UserRoleInfo, load_rbac_config

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    """Application permissions."""

    VIEW_INDICATORS = "view_indicators"
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_DATA_QUALITY = "view_data_quality"
    VIEW_ALERTS = "view_alerts"
    VIEW_TRENDS = "view_trends"
    VIEW_ORG_UNITS = "view_org_units"
    EXPORT_REPORTS = "export_reports"
    EXPORT_PDF = "export_pdf"
    USE_AI_INSIGHTS = "use_ai_insights"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    MANAGE_ALERTS = "manage_alerts"
    SYSTEM_ADMIN = "system_admin"


DEFAULT_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW_INDICATORS,
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_DATA_QUALITY,
        Permission.VIEW_ALERTS,
        Permission.VIEW_TRENDS,
        Permission.VIEW_ORG_UNITS,
    },
    Role.ANALYST: {
        Permission.VIEW_INDICATORS,
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_DATA_QUALITY,
        Permission.VIEW_ALERTS,
        Permission.VIEW_TRENDS,
        Permission.VIEW_ORG_UNITS,
        Permission.EXPORT_REPORTS,
        Permission.EXPORT_PDF,
        Permission.USE_AI_INSIGHTS,
    },
    Role.DATA_MANAGER: {
        Permission.VIEW_INDICATORS,
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_DATA_QUALITY,
        Permission.VIEW_ALERTS,
        Permission.VIEW_TRENDS,
        Permission.VIEW_ORG_UNITS,
        Permission.EXPORT_REPORTS,
        Permission.EXPORT_PDF,
        Permission.USE_AI_INSIGHTS,
        Permission.VIEW_AUDIT_LOGS,
        Permission.MANAGE_ALERTS,
    },
    Role.ADMIN: set(Permission),
}


@lru_cache
def get_role_permissions() -> dict[Role, set[Permission]]:
    """Return the configured role-permission mapping."""
    config_permissions = load_rbac_config().get("permissions", {})
    if not config_permissions:
        return {role: set(permissions) for role, permissions in DEFAULT_ROLE_PERMISSIONS.items()}

    role_permissions: dict[Role, set[Permission]] = {role: set() for role in Role}
    for permission_name, payload in config_permissions.items():
        try:
            permission = Permission(permission_name)
        except ValueError:
            logger.warning("Ignoring unknown permission in RBAC config: %s", permission_name)
            continue

        for role_name in payload.get("roles", []) or []:
            try:
                role_permissions[Role(role_name)].add(permission)
            except ValueError:
                logger.warning("Ignoring unknown role %s for permission %s", role_name, permission_name)

    if not any(role_permissions.values()):
        return {role: set(permissions) for role, permissions in DEFAULT_ROLE_PERMISSIONS.items()}
    return role_permissions


@dataclass(frozen=True)
class PermissionCheckResult:
    """Outcome of a permission check."""

    granted: bool
    permission: Optional[Permission]
    role: Optional[Role] = None
    reason: Optional[str] = None


def check_permission(role_info: UserRoleInfo, permission: Permission) -> PermissionCheckResult:
    """Check whether the resolved role grants the requested permission."""
    if role_info.is_super_admin:
        return PermissionCheckResult(
            granted=True,
            permission=permission,
            role=role_info.role,
            reason="Super admin access",
        )

    role_permissions = get_role_permissions().get(role_info.role, set())
    if permission in role_permissions:
        return PermissionCheckResult(
            granted=True,
            permission=permission,
            role=role_info.role,
            reason=f"Role {role_info.role.value} has permission",
        )

    return PermissionCheckResult(
        granted=False,
        permission=permission,
        role=role_info.role,
        reason=f"Role {role_info.role.value} lacks permission {permission.value}",
    )


def check_any_permission(
    role_info: UserRoleInfo,
    permissions: set[Permission],
) -> PermissionCheckResult:
    """Check whether the user has any permission in the provided set."""
    if role_info.is_super_admin:
        permission = next(iter(permissions), None)
        return PermissionCheckResult(
            granted=True,
            permission=permission,
            role=role_info.role,
            reason="Super admin access",
        )

    role_permissions = get_role_permissions().get(role_info.role, set())
    for permission in permissions:
        if permission in role_permissions:
            return PermissionCheckResult(
                granted=True,
                permission=permission,
                role=role_info.role,
                reason=f"Role {role_info.role.value} has permission {permission.value}",
            )

    return PermissionCheckResult(
        granted=False,
        permission=None,
        role=role_info.role,
        reason=(
            f"Role {role_info.role.value} lacks any of: "
            f"{', '.join(sorted(permission.value for permission in permissions))}"
        ),
    )


def get_user_permissions(role_info: UserRoleInfo) -> set[Permission]:
    """Return the effective permission set for the resolved role."""
    if role_info.is_super_admin:
        return set(Permission)
    return set(get_role_permissions().get(role_info.role, set()))
