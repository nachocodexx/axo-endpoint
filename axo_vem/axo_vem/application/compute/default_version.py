from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from pymongo.collection import Collection

from axo_vem.domain.errors import ConflictError, NotFoundError

DEFAULT_VERSION_CHANGED = "FunctionDefaultVersionChanged"


class FunctionDefaults:
    """Which version of a function runs when none is named (the Run form,
    a choreography step without a version). Kept only when someone picked
    one (``function_defaults``, one doc per function); otherwise it's the
    newest version. A new version becomes the default again (the record is
    dropped on registration); a deleted chosen version gives way to the
    newest one left. Each change is a row in the function's history."""

    def __init__(self, functions: Collection, defaults: Collection, activity: Any = None,
                 virtual_environment_id: Optional[str] = None, now_fn: Callable[[], float] = time.time) -> None:
        self._functions = functions
        self._defaults = defaults
        self._activity = activity             # MongoActivityRepository
        self._ve_id = virtual_environment_id
        self._now_fn = now_fn

    def version_of(self, function_id: str) -> Optional[int]:
        """The default version (None = no live version at all)."""
        return self.of_many([function_id]).get(function_id)

    def of_many(self, function_ids: Iterable[str]) -> Dict[str, int]:
        ids = list(set(function_ids))
        live: Dict[str, List[int]] = {}
        for doc in self._functions.find({"function_id": {"$in": ids}, "deleted_at": None}, {"function_id": 1, "version": 1}):
            live.setdefault(doc["function_id"], []).append(int(doc["version"]))
        chosen = {d["_id"]: int(d["version"]) for d in self._defaults.find({"_id": {"$in": ids}})}
        out: Dict[str, int] = {}
        for fid, versions in live.items():
            out[fid] = chosen[fid] if chosen.get(fid) in versions else max(versions)
        return out

    def set(self, function_id: str, version: int, by: Optional[str] = None,
            by_name: Optional[str] = None) -> Dict[str, Any]:
        doc = self._functions.find_one({"_id": f"{function_id}:{version}"})
        if doc is None:
            raise NotFoundError("function version not found")
        if doc.get("deleted_at") is not None:
            raise ConflictError(f"v{version} is deleted: it can't be the default")
        before = self.version_of(function_id)
        newest = max(int(d["version"]) for d in self._functions.find({"function_id": function_id, "deleted_at": None},
                                                                   {"version": 1}))
        if version == newest:
            self._defaults.delete_one({"_id": function_id})       # follows the newest from now on
        else:
            self._defaults.replace_one({"_id": function_id}, {"version": version, "set_by": by,
                                                               "set_at": self._now_fn()}, upsert=True)
        if before != version:
            self._record(function_id, doc.get("name"), version, before, by, by_name)
        return {"function_id": function_id, "default_version": version}

    def follow_newest(self, function_id: str) -> None:
        """A new version was registered: it becomes the default."""
        self._defaults.delete_one({"_id": function_id})

    def _record(self, function_id: str, name: Optional[str], version: int, before: Optional[int],
                by: Optional[str], by_name: Optional[str]) -> None:
        if self._activity is None:
            return
        self._activity.record(DEFAULT_VERSION_CHANGED, {
            "event_id": uuid.uuid4().hex, "created_at": datetime.fromtimestamp(self._now_fn(), tz=timezone.utc).isoformat(),
            "user_id": by, "virtual_environment_id": self._ve_id, "function_id": function_id, "name": name,
            "version": version, "previous_version": before, "changed_by_name": by_name,
        })
