from __future__ import annotations

from typing import FrozenSet, Iterable, Optional

from axo_control_plane.domain.errors import InvalidError

# ── VE roles, lowest to highest ──────────────────────────────────────────────
VIEWER = "viewer"
DEVELOPER = "developer"
ADMIN = "admin"
OWNER = "owner"
VE_ROLES = (VIEWER, DEVELOPER, ADMIN, OWNER)

# ── bucket roles ──────────────────────────────────────────────────────────────
READER = "reader"
WRITER = "writer"
BUCKET_ROLES = (READER, WRITER, OWNER)

# ── team roles ────────────────────────────────────────────────────────────────
MEMBER = "member"
TEAM_ROLES = (MEMBER, ADMIN, OWNER)

# ── platform roles ────────────────────────────────────────────────────────────
PLATFORM_ADMIN = "admin"
PLATFORM_USER = "user"
PLATFORM_GUEST = "guest"
PLATFORM_ROLES = (PLATFORM_GUEST, PLATFORM_USER, PLATFORM_ADMIN)

# Xolo ACL keeps a fixed permission list (read, write, update, delete,
# sys:manage). The roles are stored as cumulative sets, so Xolo's union of a
# person's own grants and their teams' grants yields the strongest role. The
# owner is Xolo's ACL owner (is_owner), never inferred from permissions.
XOLO_PERMISSIONS = {
    VIEWER: frozenset({"read"}),
    DEVELOPER: frozenset({"read", "write", "update"}),
    ADMIN: frozenset({"read", "write", "update", "delete"}),
}
XOLO_BUCKET_PERMISSIONS = {
    READER: frozenset({"read"}),
    WRITER: frozenset({"read", "write"}),
}
ALL_GRANTABLE = frozenset({"read", "write", "update", "delete"})


def rank(role: Optional[str], order=VE_ROLES) -> int:
    """-1 for no role."""
    return order.index(role) if role in order else -1


def at_least(role: Optional[str], minimum: str, order=VE_ROLES) -> bool:
    return rank(role, order) >= rank(minimum, order)


def strongest(roles: Iterable[Optional[str]], order=VE_ROLES) -> Optional[str]:
    best: Optional[str] = None
    for role in roles:
        if rank(role, order) > rank(best, order):
            best = role
    return best


def weakest(a: Optional[str], b: Optional[str], order=VE_ROLES) -> Optional[str]:
    if a is None or b is None:
        return None
    return a if rank(a, order) <= rank(b, order) else b


def ve_role_from_permissions(permissions: Iterable[str], is_owner: bool = False) -> Optional[str]:
    if is_owner:
        return OWNER
    perms: FrozenSet[str] = frozenset(permissions)
    for role in (ADMIN, DEVELOPER, VIEWER):
        if XOLO_PERMISSIONS[role] <= perms:
            return role
    return None


def bucket_role_from_permissions(permissions: Iterable[str], is_owner: bool = False) -> Optional[str]:
    if is_owner:
        return OWNER
    perms = frozenset(permissions)
    for role in (WRITER, READER):
        if XOLO_BUCKET_PERMISSIONS[role] <= perms:
            return role
    return None


def check_ve_role(role: str) -> str:
    if role not in (VIEWER, DEVELOPER, ADMIN):
        raise InvalidError("a VE role is viewer, developer or admin (owner changes by transfer)")
    return role


def check_bucket_role(role: str) -> str:
    if role not in (READER, WRITER):
        raise InvalidError("a bucket role is reader or writer (owner changes by transfer)")
    return role


def may_assign_ve_role(assigner_role: Optional[str], role: str) -> bool:
    """Only the Owner assigns Admin; Owner and Admin assign Developer / Viewer."""
    if role == ADMIN:
        return assigner_role == OWNER
    return at_least(assigner_role, ADMIN)


def ve_resource(virtual_environment_id: str) -> str:
    return f"ve:{virtual_environment_id}"


def bucket_resource(bucket_id: str) -> str:
    return f"bucket:{bucket_id}"
