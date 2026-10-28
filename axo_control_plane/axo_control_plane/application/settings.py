from __future__ import annotations

import time
from typing import Any, Callable, Dict, Mapping

from pymongo.database import Database

from axo_control_plane.domain import settings as S

PLATFORM_DEFAULTS_ID = "user_defaults"


class UserSettings:
    """Each person's settings (``user_settings``, one document per person:
    only the fields they chose) and the platform defaults the Platform Admin
    sets (``platform_settings``). A field nobody chose follows the platform
    default, so changing a default reaches everyone who didn't pick their own."""

    def __init__(self, db: Database, now_fn: Callable[[], float] = time.time) -> None:
        self._own = db["user_settings"]
        self._platform = db["platform_settings"]
        self._now_fn = now_fn

    # ── platform defaults ────────────────────────────────────────────────────
    def defaults(self) -> Dict[str, Any]:
        doc = self._platform.find_one({"_id": PLATFORM_DEFAULTS_ID}) or {}
        return S.effective(doc.get("values") or {}, S.PLATFORM_DEFAULTS)

    def set_defaults(self, changes: Mapping[str, Any], by: str) -> Dict[str, Any]:
        """``None`` puts a field back to the built-in default."""
        checked = S.validate(changes)
        self._apply(self._platform, PLATFORM_DEFAULTS_ID, checked, by)
        return self.describe_defaults()

    def describe_defaults(self) -> Dict[str, Any]:
        return {"defaults": self.defaults(), "built_in": dict(S.PLATFORM_DEFAULTS),
                "settings": [s.to_dict() for s in S.SETTINGS]}

    # ── one person ───────────────────────────────────────────────────────────
    def own(self, user_id: str) -> Dict[str, Any]:
        doc = self._own.find_one({"_id": user_id}) or {}
        return {k: v for k, v in (doc.get("values") or {}).items() if k in S.BY_NAME and v is not None}

    def of(self, user_id: str) -> Dict[str, Any]:
        """What applies to that person."""
        return S.effective(self.own(user_id), self.defaults())

    def describe(self, user_id: str) -> Dict[str, Any]:
        """For the settings page: what applies, what they chose themselves,
        the defaults, and every setting with its choices."""
        own, defaults = self.own(user_id), self.defaults()
        return {"settings": S.effective(own, defaults), "own": own, "defaults": defaults,
                "fields": [s.to_dict() for s in S.SETTINGS]}

    def update(self, user_id: str, changes: Mapping[str, Any]) -> Dict[str, Any]:
        """Changes only the fields sent; ``None`` = follow the default again."""
        self._apply(self._own, user_id, S.validate(changes), user_id)
        return self.describe(user_id)

    def forget(self, user_id: str) -> None:
        self._own.delete_one({"_id": user_id})

    def _apply(self, col, doc_id: str, checked: Dict[str, Any], by: str) -> None:
        if not checked:
            return
        sets = {f"values.{k}": v for k, v in checked.items() if v is not None}
        unsets = {f"values.{k}": "" for k, v in checked.items() if v is None}
        update: Dict[str, Any] = {"$set": {**sets, "updated_at": self._now_fn(), "updated_by": by}}
        if unsets:
            update["$unset"] = unsets
        col.update_one({"_id": doc_id}, update, upsert=True)
