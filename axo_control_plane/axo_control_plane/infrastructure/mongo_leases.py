from __future__ import annotations

import time
from typing import Callable

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application.ports import Leases


class MongoLeases(Leases):
    """A lease = one document {_id: name, holder, expires_at} in the shared
    Mongo. Taking it is one conditional update (free, expired, or already
    ours), so only one Control Plane copy holds a given name at a time; a
    copy that dies simply stops renewing and another takes over."""

    def __init__(self, db: Database, now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["leases"]
        self._now_fn = now_fn

    def acquire(self, name: str, holder: str, seconds: float) -> bool:
        now = self._now_fn()
        try:
            result = self._col.update_one(
                {"_id": name, "$or": [{"holder": holder}, {"expires_at": {"$lt": now}}]},
                {"$set": {"holder": holder, "expires_at": now + seconds}},
                upsert=True,
            )
            return result.matched_count == 1 or result.upserted_id is not None
        except DuplicateKeyError:
            # The upsert raced with another holder's live lease.
            return False

    def release(self, name: str, holder: str) -> None:
        self._col.delete_one({"_id": name, "holder": holder})
