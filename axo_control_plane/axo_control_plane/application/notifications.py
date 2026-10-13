from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from pymongo import DESCENDING
from pymongo.database import Database

# Notification types (the UI picks an icon/text per type).
INVITATION_RECEIVED = "invitation_received"
INVITATION_ANSWERED = "invitation_answered"
TRANSFER_REQUESTED = "transfer_requested"
TRANSFER_COMPLETED = "transfer_completed"
TEAM_PURCHASE = "team_purchase"
PURCHASE_PENDING = "purchase_pending"
PURCHASE_ANSWERED = "purchase_answered"
VE_STATE = "ve_state"
LIMIT_WARNING = "limit_warning"
JOB_FINISHED = "job_finished"


class Notifications:
    """The bell in the UI: one document per person per notice. The UI reads
    them (and gets new ones live over the CP's own WebSocket)."""

    def __init__(self, db: Database, now_fn: Callable[[], float] = time.time) -> None:
        self._col = db["notifications"]
        self._now_fn = now_fn

    def notify(self, user_ids: Iterable[str], kind: str, text: str, data: Optional[Dict[str, Any]] = None,
               dedup_key: Optional[str] = None) -> int:
        """``dedup_key``: at most one notice with that key per person
        (e.g. "90% of Free requests used in 2026-10")."""
        sent = 0
        for user_id in {u for u in user_ids if u}:
            doc = {"_id": str(uuid.uuid4()), "user_id": user_id, "kind": kind, "text": text,
                   "data": dict(data or {}), "read": False, "created_at": self._now_fn()}
            if dedup_key:
                doc["dedup_key"] = dedup_key
                if self._col.find_one({"user_id": user_id, "dedup_key": dedup_key}):
                    continue
            self._col.insert_one(doc)
            sent += 1
        return sent

    def list(self, user_id: str, unread_only: bool = False, limit: int = 50,
             after: Optional[float] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"user_id": user_id}
        if unread_only:
            query["read"] = False
        if after is not None:
            query["created_at"] = {"$gt": after}
        return [_public(d) for d in self._col.find(query).sort("created_at", DESCENDING).limit(limit)]

    def unread_count(self, user_id: str) -> int:
        return self._col.count_documents({"user_id": user_id, "read": False})

    def mark_read(self, user_id: str, notification_ids: Optional[List[str]] = None) -> int:
        query: Dict[str, Any] = {"user_id": user_id, "read": False}
        if notification_ids:
            query["_id"] = {"$in": notification_ids}
        return self._col.update_many(query, {"$set": {"read": True}}).modified_count


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "dedup_key")}
    out["notification_id"] = doc["_id"]
    return out
