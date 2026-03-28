"""
Role definitions and DHIS2 authority mapping.

Roles are resolved from DHIS2 metadata at login time and kept only in the
active in-memory session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import logging
from typing import Any, Optional

from app.core.config import load_yaml_config

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Application roles ordered from least to most privileged."""

    VIEWER = "viewer"
    ANALYST = "analyst"
    DATA_MANAGER = "data_manager"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        return {
            Role.VIEWER: 1,
            Role.ANALYST: 2,
            Role.DATA_MANAGER: 3,
            Role.ADMIN: 4,
        }[self]

    def __ge__(self, other: "Role") -> bool:
        return self.level >= other.level

    def __gt__(self, other: "Role") -> bool:
        return self.level > other.level

    def __le__(self, other: "Role") -> bool:
        return self.level <= other.level

    def __lt__(self, other: "Role") -> bool:
        return self.level < other.level


@dataclass(frozen=True)
class RoleMapping:
    """Map a DHIS2 authority set to an application role."""

    role: Role
    required_authorities: set[str]
    any_of: bool = True

    def matches(self, user_authorities: set[str]) -> bool:
        """Return True when the user's authorities satisfy the mapping."""
        if not self.required_authorities:
            return True
        if self.any_of:
            return bool(self.required_authorities & user_authorities)
        return self.required_authorities <= user_authorities


DEFAULT_ROLE_MAPPINGS: list[RoleMapping] = [
    RoleMapping(
        role=Role.ADMIN,
        required_authorities={"ALL", "F_SYSTEM_SETTING", "M_dhis-web-settings"},
    ),
    RoleMapping(
        role=Role.DATA_MANAGER,
        required_authorities={
            "F_DATAVALUE_ADD",
            "F_DATAELEMENT_PUBLIC_ADD",
            "F_DATASET_PUBLIC_ADD",
            "F_INDICATOR_PUBLIC_ADD",
            "M_dhis-web-data-administration",
        },
    ),
    RoleMapping(
        role=Role.ANALYST,
        required_authorities={
            "F_EXPORT_DATA",
            "F_PERFORM_ANALYTICS",
            "F_EXPORT_EVENTS",
            "M_dhis-web-pivot",
            "M_dhis-web-data-visualizer",
        },
    ),
    RoleMapping(role=Role.VIEWER, required_authorities=set()),
]


@dataclass
class UserRoleInfo:
    """Resolved role and raw DHIS2 authorities for the current session."""

    user_id: str
    username: str
    role: Role
    dhis2_authorities: set[str] = field(default_factory=set)
    org_unit_uids: set[str] = field(default_factory=set)
    is_super_admin: bool = False

    def has_authority(self, authority: str) -> bool:
        if self.is_super_admin:
            return True
        return authority in self.dhis2_authorities

    def has_any_authority(self, authorities: set[str]) -> bool:
        if self.is_super_admin:
            return True
        return bool(self.dhis2_authorities & authorities)

    def has_org_unit_access(self, org_unit_uid: str) -> bool:
        if self.is_super_admin:
            return True
        return org_unit_uid in self.org_unit_uids


@lru_cache
def load_rbac_config() -> dict[str, Any]:
    """Load the shared RBAC YAML config once."""
    try:
        return load_yaml_config("rbac.yaml") or {}
    except FileNotFoundError:
        logger.warning("RBAC config missing; using in-code defaults")
        return {}
    except Exception as exc:
        logger.warning("Failed to load RBAC config, using defaults: %s", exc)
        return {}


@lru_cache
def get_role_mappings() -> tuple[RoleMapping, ...]:
    """Return configured role mappings, highest privilege first."""
    config_roles = load_rbac_config().get("roles", {})
    if not config_roles:
        return tuple(DEFAULT_ROLE_MAPPINGS)

    mappings: list[tuple[int, RoleMapping]] = []
    for role_name, payload in config_roles.items():
        try:
            role = Role(role_name)
        except ValueError:
            logger.warning("Ignoring unknown RBAC role in config: %s", role_name)
            continue

        authorities = {
            str(authority)
            for authority in payload.get("dhis2_authorities", []) or []
            if authority
        }
        mappings.append(
            (
                int(payload.get("level", role.level)),
                RoleMapping(
                    role=role,
                    required_authorities=authorities,
                    any_of=bool(payload.get("any_of", True)),
                ),
            )
        )

    if not mappings:
        return tuple(DEFAULT_ROLE_MAPPINGS)

    mappings.sort(key=lambda item: item[0], reverse=True)
    return tuple(mapping for _, mapping in mappings)


def resolve_user_role(
    user_id: str,
    username: str,
    authorities: list[str] | None,
    org_units: list[dict[str, Any]] | None,
) -> UserRoleInfo:
    """Resolve the highest application role for a DHIS2-authenticated user."""
    authority_set = {str(authority) for authority in (authorities or []) if authority}
    org_unit_uids = {
        str(org_unit.get("id") or org_unit.get("uid"))
        for org_unit in (org_units or [])
        if org_unit and (org_unit.get("id") or org_unit.get("uid"))
    }
    is_super_admin = "ALL" in authority_set

    resolved_role = Role.VIEWER
    for mapping in get_role_mappings():
        if mapping.matches(authority_set):
            resolved_role = mapping.role
            break

    role_info = UserRoleInfo(
        user_id=user_id,
        username=username,
        role=resolved_role,
        dhis2_authorities=authority_set,
        org_unit_uids=org_unit_uids,
        is_super_admin=is_super_admin,
    )
    logger.info(
        "Resolved role for user %s: %s (super_admin=%s)",
        username,
        resolved_role.value,
        is_super_admin,
    )
    return role_info


def store_role_in_session(session: Any, role_info: UserRoleInfo) -> None:
    """Persist role info in the in-memory session."""
    session.user_data["role_info"] = role_info


def _coerce_role_info(raw_value: Any) -> Optional[UserRoleInfo]:
    if isinstance(raw_value, UserRoleInfo):
        return raw_value
    if isinstance(raw_value, dict):
        try:
            return UserRoleInfo(
                user_id=str(raw_value.get("user_id", "")),
                username=str(raw_value.get("username", "")),
                role=Role(raw_value.get("role", Role.VIEWER.value)),
                dhis2_authorities={
                    str(item)
                    for item in raw_value.get("dhis2_authorities", [])
                    if item
                },
                org_unit_uids={
                    str(item)
                    for item in raw_value.get("org_unit_uids", [])
                    if item
                },
                is_super_admin=bool(raw_value.get("is_super_admin", False)),
            )
        except Exception:
            return None
    return None


def get_role_from_session(session: Any) -> Optional[UserRoleInfo]:
    """Read role info from session, deriving it from credentials when absent."""
    role_info = _coerce_role_info(getattr(session, "user_data", {}).get("role_info"))
    if role_info:
        return role_info

    credentials = getattr(session, "credentials", None)
    if credentials is None:
        return None

    derived = resolve_user_role(
        user_id=credentials.user_id or "unknown",
        username=credentials.user_name or credentials.username or "unknown",
        authorities=getattr(credentials, "authorities", []),
        org_units=getattr(credentials, "org_units", []),
    )
    store_role_in_session(session, derived)
    return derived
