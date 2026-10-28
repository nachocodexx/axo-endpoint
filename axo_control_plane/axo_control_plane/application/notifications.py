from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from pymongo import DESCENDING
from pymongo.database import Database

from axo_control_plane.application.settings import UserSettings
from axo_control_plane.domain import settings as S
from axo_control_plane.domain.errors import InvalidError

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
BILL_CLOSED = "bill_closed"
RENEWAL_SOON = "renewal_soon"
RENTAL_ENDING = "rental_ending"
PLAN_CHANGE_SCHEDULED = "plan_change_scheduled"
BUDGET_WARNING = "budget_warning"

# The notifications page's type chips: each group = its types.
GROUPS: Dict[str, List[str]] = {
    "people": [INVITATION_RECEIVED, INVITATION_ANSWERED, TRANSFER_REQUESTED, TRANSFER_COMPLETED,
               TEAM_PURCHASE, PURCHASE_PENDING, PURCHASE_ANSWERED],
    "ves": [VE_STATE, RENTAL_ENDING, PLAN_CHANGE_SCHEDULED],
    "jobs": [JOB_FINISHED],
    "billing": [BILL_CLOSED, RENEWAL_SOON, BUDGET_WARNING, LIMIT_WARNING],
}
KEEP_DAYS = 90


def bill_viewers(db: Database, owner: Dict[str, Any]) -> List[str]:
    """Who sees a payer's bills, and so gets its billing notices
    (upgrade.md 13.6 / 13.7): the person, or the Team Owner + Team Admins."""
    if (owner or {}).get("kind") == "team":
        team = db["teams"].find_one({"_id": owner.get("id")}, {"owner_id": 1, "members": 1}) or {}
        admins = [uid for uid, role in (team.get("members") or {}).items() if role in ("admin", "owner")]
        return sorted({team.get("owner_id"), *admins} - {None})
    return [owner["id"]] if (owner or {}).get("id") else []


class Notifications:
    """The bell in the UI: one document per person per notice. The UI reads
    them (and gets new ones live over the CP's own WebSocket).

    Dates and amounts are never baked into the text: the text is a template
    (``"{ve} renews on {renews_at}"``) and ``fields`` hold the values
    (``settings.date(ts)`` / ``settings.money(cents)``). Each person's
    ``text`` is written in their own settings when sent (time zone, date and
    number format); the UI writes it again from ``template`` + ``fields``,
    so a later change of settings shows there too."""

    def __init__(self, db: Database, now_fn: Callable[[], float] = time.time,
                 settings: Optional[UserSettings] = None) -> None:
        self._col = db["notifications"]
        self._now_fn = now_fn
        self._settings = settings or UserSettings(db, now_fn)

    def notify(self, user_ids: Iterable[str], kind: str, text: str, data: Optional[Dict[str, Any]] = None,
               dedup_key: Optional[str] = None, fields: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
        """``dedup_key``: at most one notice with that key per person
        (e.g. "90% of Free requests used in 2026-10"). ``fields``: the
        ``{name}`` placeholders of ``text``."""
        sent = 0
        for user_id in {u for u in user_ids if u}:
            doc = {"_id": str(uuid.uuid4()), "user_id": user_id, "kind": kind, "text": text,
                   "data": dict(data or {}), "read": False, "created_at": self._now_fn()}
            if fields:
                doc.update(template=text, fields=dict(fields),
                           text=S.render(text, fields, self._settings.of(user_id)))
            if dedup_key:
                doc["dedup_key"] = dedup_key
                if self._col.find_one({"user_id": user_id, "dedup_key": dedup_key}):
                    continue
            self._col.insert_one(doc)
            sent += 1
        return sent

    def list(self, user_id: str, unread_only: bool = False, limit: int = 50,
             after: Optional[float] = None, before: Optional[float] = None, before_id: Optional[str] = None,
             groups: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Newest first. ``before`` + ``before_id`` = the last notice shown
        (the page's "Load more"); ``groups`` = keys of ``GROUPS``."""
        query = self._query(user_id, unread_only, groups)
        if after is not None:
            query["created_at"] = {"$gt": after}
        if before is not None:
            query["$or"] = [{"created_at": {"$lt": before}},
                            {"created_at": before, "_id": {"$lt": before_id or ""}}]
        docs = self._col.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).limit(limit)
        return [_public(d) for d in docs]

    def unread_count(self, user_id: str) -> int:
        return self._col.count_documents({"user_id": user_id, "read": False})

    def mark_read(self, user_id: str, notification_ids: Optional[List[str]] = None,
                  groups: Optional[List[str]] = None) -> int:
        """The given notices, or every unread one (of ``groups`` when given)."""
        query = self._query(user_id, True, groups)
        if notification_ids:
            query["_id"] = {"$in": notification_ids}
        return self._col.update_many(query, {"$set": {"read": True}}).modified_count

    def purge(self, keep_days: int = KEEP_DAYS) -> int:
        """Notices older than ``keep_days`` are deleted for good."""
        cutoff = self._now_fn() - keep_days * 86400
        return self._col.delete_many({"created_at": {"$lt": cutoff}}).deleted_count

    @staticmethod
    def _query(user_id: str, unread_only: bool, groups: Optional[List[str]]) -> Dict[str, Any]:
        query: Dict[str, Any] = {"user_id": user_id}
        if unread_only:
            query["read"] = False
        if groups:
            unknown = [g for g in groups if g not in GROUPS]
            if unknown:
                raise InvalidError(f"unknown notification group(s): {', '.join(unknown)}")
            query["kind"] = {"$in": [k for g in groups for k in GROUPS[g]]}
        return query


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "dedup_key")}
    out["notification_id"] = doc["_id"]
    return out
