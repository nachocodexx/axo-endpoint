from __future__ import annotations

import re
import secrets
from typing import Optional, Tuple

from axo_control_plane.domain.errors import InvalidError

# Users and teams share ONE list of names (like GitHub users/organizations):
# a bucket "ds/reports" must clearly mean the user or team called "ds".
OWNER_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
# The platform's own buckets live under this owner; nobody can take it.
PLATFORM_OWNER = "axo"
RESERVED_OWNER_NAMES = frozenset({PLATFORM_OWNER, "admin", "root", "system", "platform", "api", "www", "help"})

BUCKET_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9])?$")
VE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # no 0/o/1/l


def check_owner_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not OWNER_NAME_RE.match(name):
        raise InvalidError("names use 1-39 lowercase letters, digits or '-', not starting or ending with '-'")
    if name in RESERVED_OWNER_NAMES:
        raise InvalidError(f"the name {name!r} is reserved")
    return name


def check_ve_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not VE_NAME_RE.match(name):
        raise InvalidError("a VE name uses 1-32 lowercase letters, digits or '-', not starting or ending with '-'")
    return name


def new_ve_id(name: str) -> str:
    """name + a short random part, e.g. ``ml-prod-7f3k``: used in container
    names, the VE network and the code bucket, so it stays short and safe."""
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(4))
    return f"{check_ve_name(name)}-{suffix}"


def check_bucket_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not BUCKET_NAME_RE.match(name) or ".." in name:
        raise InvalidError("a bucket name uses 1-63 lowercase letters, digits, '.', '_' or '-'")
    return name


def full_bucket_name(owner_name: str, bucket_name: str) -> str:
    return f"{owner_name}/{bucket_name}"


def split_bucket_name(full_name: str) -> Tuple[str, str]:
    """``"alice/reports"`` -> ("alice", "reports"). A bucket is always named
    in full; a short name would mean a different bucket per caller."""
    owner, sep, name = (full_name or "").strip().lower().partition("/")
    if not sep or not owner or not name or "/" in name:
        raise InvalidError(f"bucket names are written as owner/bucket, got {full_name!r}")
    if owner != PLATFORM_OWNER and not OWNER_NAME_RE.match(owner):
        raise InvalidError(f"invalid owner in bucket name {full_name!r}")
    return owner, check_bucket_name(name)


def code_bucket_for(virtual_environment_id: str) -> str:
    """The platform bucket holding a VE's function code."""
    return full_bucket_name(PLATFORM_OWNER, f"ve-{virtual_environment_id}-functions")


def results_bucket_for(virtual_environment_id: str) -> str:
    """The platform bucket holding every result of a VE's jobs (kept per job)."""
    return full_bucket_name(PLATFORM_OWNER, f"ve-{virtual_environment_id}-results")


def objects_bucket_for(virtual_environment_id: str) -> str:
    """The platform bucket holding a VE's big active-object values (the
    endpoints' ``axo_shared.active_objects.state.objects_bucket_name``)."""
    return full_bucket_name(PLATFORM_OWNER, f"ve-{virtual_environment_id}-objects")


def ve_of_objects_bucket(full_name: str) -> Optional[str]:
    """``axo/ve-<id>-objects`` -> ``<id>`` (else None)."""
    prefix, suffix = f"{PLATFORM_OWNER}/ve-", "-objects"
    if full_name and full_name.startswith(prefix) and full_name.endswith(suffix):
        return full_name[len(prefix):-len(suffix)] or None
    return None


def ve_of_results_bucket(full_name: str) -> Optional[str]:
    """``axo/ve-<id>-results`` -> ``<id>`` (else None)."""
    prefix, suffix = f"{PLATFORM_OWNER}/ve-", "-results"
    if full_name and full_name.startswith(prefix) and full_name.endswith(suffix):
        return full_name[len(prefix):-len(suffix)] or None
    return None
