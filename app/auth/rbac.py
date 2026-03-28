"""
Role-based access control helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

from app.auth.permissions import (
    Permission,
    PermissionCheckResult,
    check_any_permission,
    check_permission,
    get_user_permissions,
)
from app.auth.roles import Role, UserRoleInfo, get_role_from_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorizationContext:
    """Context captured for a single authorization decision."""

    role_info: UserRoleInfo
    granted_permissions: set[Permission]
    org_unit_uid: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None


@dataclass(frozen=True)
class AuthorizationResult:
    """Outcome of a full authorization check."""

    authorized: bool
    context: AuthorizationContext
    permission_result: Optional[PermissionCheckResult] = None
    org_unit_allowed: bool = True
    denial_reason: Optional[str] = None


class PermissionDeniedError(Exception):
    """Raised when a required permission is missing."""

    def __init__(self, permission: Permission, role: Role, reason: Optional[str] = None):
        self.permission = permission
        self.role = role
        self.reason = reason or f"Role {role.value} lacks permission {permission.value}"
        super().__init__(self.reason)


class RBACEngine:
    """Resolve permissions and direct org-unit access for the active session."""

    def __init__(self, session: object):
        self.session = session
        role_info = get_role_from_session(session)
        if role_info is None:
            logger.warning("Role info missing from session; denying non-viewer access by default")
            role_info = UserRoleInfo(user_id="unknown", username="unknown", role=Role.VIEWER)
        self.role_info = role_info

    @property
    def user_id(self) -> str:
        return self.role_info.user_id

    @property
    def username(self) -> str:
        return self.role_info.username

    @property
    def role(self) -> Role:
        return self.role_info.role

    @property
    def is_super_admin(self) -> bool:
        return self.role_info.is_super_admin

    @property
    def permissions(self) -> set[Permission]:
        return get_user_permissions(self.role_info)

    def has_permission(self, permission: Permission) -> bool:
        return check_permission(self.role_info, permission).granted

    def has_any_permission(self, permissions: set[Permission]) -> bool:
        return check_any_permission(self.role_info, permissions).granted

    def has_org_unit_access(self, org_unit_uid: str) -> bool:
        return self.role_info.has_org_unit_access(org_unit_uid)

    def authorize(
        self,
        permission: Permission,
        org_unit_uid: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> AuthorizationResult:
        """Authorize a permission and optional direct org-unit access."""
        context = AuthorizationContext(
            role_info=self.role_info,
            granted_permissions=self.permissions,
            org_unit_uid=org_unit_uid,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        permission_result = check_permission(self.role_info, permission)
        if not permission_result.granted:
            return AuthorizationResult(
                authorized=False,
                context=context,
                permission_result=permission_result,
                denial_reason=permission_result.reason,
            )

        if org_unit_uid and not self.has_org_unit_access(org_unit_uid):
            return AuthorizationResult(
                authorized=False,
                context=context,
                permission_result=permission_result,
                org_unit_allowed=False,
                denial_reason=f"Access denied to org unit {org_unit_uid}",
            )

        return AuthorizationResult(
            authorized=True,
            context=context,
            permission_result=permission_result,
        )

    def require(self, permission: Permission, org_unit_uid: Optional[str] = None) -> None:
        """Require permission or raise a typed error."""
        result = self.authorize(permission, org_unit_uid=org_unit_uid)
        if not result.authorized:
            raise PermissionDeniedError(
                permission=permission,
                role=self.role,
                reason=result.denial_reason,
            )


def get_rbac_engine(session: object) -> RBACEngine:
    """Create an RBAC engine for the current session."""
    return RBACEngine(session)
