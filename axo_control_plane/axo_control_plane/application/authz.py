from __future__ import annotations

from typing import Optional

from axo_control_plane.application.access import AccessResolver
from axo_control_plane.application.people import Actor
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ForbiddenError, NotFoundError


class Authorizer:
    """A caller's effective role, applying SDK-key limits on top of Xolo's
    answer: a key works only in its VE (if it names one) and never above its
    own maximum role nor above the person's current role."""

    def __init__(self, access: AccessResolver) -> None:
        self._access = access

    def ve_role(self, actor: Actor, ve_id: str) -> Optional[str]:
        if actor.key_id is not None and actor.key_ve_id and actor.key_ve_id != ve_id:
            return None
        role = self._access.ve_role(actor.user_id, ve_id)
        if actor.is_guest:
            role = R.weakest(role, R.VIEWER)          # guests are only ever viewers
        if actor.key_id is not None:
            role = R.weakest(role, actor.key_max_role)
        return role

    def require_ve(self, actor: Actor, ve_id: str, minimum: str) -> str:
        if actor.password_change_required:
            raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
        role = self.ve_role(actor, ve_id)
        if role is None:
            # Don't reveal that a VE exists to someone with no access to it.
            raise NotFoundError("no such VE")
        if not R.at_least(role, minimum):
            raise ForbiddenError(f"this needs the {minimum} role in this VE (you are {role})",
                                 details={"needed": minimum, "role": role})
        return role

    def bucket_role(self, actor: Actor, bucket_id: str) -> Optional[str]:
        role = self._access.bucket_role(actor.user_id, bucket_id)
        if actor.is_guest:
            role = R.weakest(role, R.READER, R.BUCKET_ROLES)
        if actor.key_id is not None:
            # A key's maximum VE role caps bucket use too: viewer keys only read.
            if not R.at_least(actor.key_max_role, R.DEVELOPER):
                role = R.weakest(role, R.READER, R.BUCKET_ROLES)
        return role

    def require_bucket(self, actor: Actor, bucket_id: str, minimum: str) -> str:
        if actor.password_change_required:
            raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")
        role = self.bucket_role(actor, bucket_id)
        if role is None:
            raise NotFoundError("no such bucket")
        if not R.at_least(role, minimum, R.BUCKET_ROLES):
            raise ForbiddenError(f"this needs the {minimum} role on this bucket (you are {role})")
        return role
