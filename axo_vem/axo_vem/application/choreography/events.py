"""Events: what starts choreographies besides a person pressing Run.

An event has the CloudEvents shape (``id``, ``type``, ``source``, ``time``,
``subject``, ``data``). People send their own (``client.emit`` / ``POST
/events``, Developer and above); the platform sends ``file.written`` (a new
file in a bucket a trigger watches). Each one is kept in the VE's Events
list with the runs it started; the same id twice starts nothing new.

Event types: the built-in ones, the ones people describe on the Events
page, and any type sent for the first time (found on its own, its fields
taken from that first event). "Used before" suggestions are counted from
the newest version of every choreography -- nothing is stored for them.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pymongo import DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from axo_vem.domain.choreography.repository import ChoreographyRepository
from axo_vem.domain.choreography.triggers import BUILT_IN_EVENT_TYPES, event_type_problem
from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from axo_vem.domain.events import models
from axo_vem.domain.visibility import Viewer

EVENT_DATA_MAX_BYTES = 256 * 1024
PAGE_MAX = 200
FIELD_TYPES = ("text", "number", "true/false", "list", "object", "any")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "any"


class EventsService:
    def __init__(self, events: Collection, types: Collection, repository: ChoreographyRepository,
                 on_event: Callable[..., List[Dict[str, Any]]], virtual_environment_id: Optional[str] = None,
                 now_fn: Callable[[], str] = _now) -> None:
        self._events = events
        self._types = types
        self._repository = repository
        # the engine's on_event(event, allowed_users=..., source_choreography_id=...)
        self._on_event = on_event
        self._ve = virtual_environment_id
        self._now = now_fn
        self._events.create_index([("time", DESCENDING), ("_id", DESCENDING)])
        self._events.create_index("visible_to")
        self._events.create_index("type")

    # ── sending ──────────────────────────────────────────────────────────────
    def emit(self, *, sender: Dict[str, Any], event_type: str, data: Any = None, event_id: Optional[str] = None,
             source: Optional[str] = None, subject: Optional[str] = None) -> Dict[str, Any]:
        """``sender``: the job record's ``started_by_*`` shape plus
        ``user_id`` (who the call acts for)."""
        problem = event_type_problem(event_type, built_in_ok=False)
        if problem:
            raise InvalidRequestError(problem, error_name="EVENT_TYPE_INVALID")
        if len(json.dumps(data, default=str)) > EVENT_DATA_MAX_BYTES:
            raise InvalidRequestError(f"An event's data must be at most {EVENT_DATA_MAX_BYTES // 1024} KB; put "
                                      "bigger data in a bucket and send its name", error_name="VEM_INVALID")
        user_id = sender.get("user_id")
        event = {"id": event_id or uuid.uuid4().hex, "type": event_type, "specversion": "1.0",
                 "source": source or f"{sender.get('started_by_type') or 'user'}:{sender.get('started_by_id') or user_id}",
                 "subject": subject, "time": self._now(), "data": data}
        record = {"_id": event["id"], **event, "sent_by": user_id, "sent_by_type": sender.get("started_by_type"),
                  "sent_by_id": sender.get("started_by_id"), "sent_by_name": sender.get("started_by_name"),
                  "runs": [], "visible_to": [user_id] if user_id else []}
        try:
            self._events.insert_one(record)
        except DuplicateKeyError:
            return {**self._public(self._events.find_one({"_id": event["id"]}) or record), "duplicate": True}
        runs = self._on_event(event)
        self._finish(event["id"], runs, [user_id] if user_id else [])
        self._seen(event_type, data, user_id)
        return {**self._public(self._events.find_one({"_id": event["id"]}) or record), "duplicate": False}

    def file_written(self, event: Dict[str, Any], runs: List[Dict[str, Any]]) -> None:
        """Keeps a ``file.written`` event the platform sent (with the runs it
        started); seen by the people whose triggers it matched."""
        record = {"_id": event["id"], **event, "specversion": "1.0", "sent_by": None, "sent_by_type": "platform",
                  "sent_by_id": None, "sent_by_name": None, "runs": [], "visible_to": []}
        try:
            self._events.insert_one(record)
        except DuplicateKeyError:
            pass
        self._finish(event["id"], runs, [])

    def _finish(self, event_id: str, runs: List[Dict[str, Any]], visible_to: List[str]) -> None:
        creators = set()
        for choreography in {r["choreography_id"] for r in runs if r.get("choreography_id")}:
            found = self._repository.get(choreography)
            for trigger in (found.triggers if found is not None else []):
                if any(r.get("trigger_id") == trigger.get("trigger_id") for r in runs) and trigger.get("created_by"):
                    creators.add(trigger["created_by"])
        self._events.update_one({"_id": event_id}, {
            "$addToSet": {"runs": {"$each": runs}, "visible_to": {"$each": sorted(set(visible_to) | creators)}}})

    def _seen(self, event_type: str, data: Any, user_id: Optional[str]) -> None:
        """A type sent for the first time is listed on its own, with the
        fields of that first event."""
        fields = ([{"name": k, "type": json_type(v), "description": ""} for k, v in data.items()]
                  if isinstance(data, dict) else [])
        now = self._now()
        self._types.update_one({"_id": event_type}, {
            "$setOnInsert": {"description": "", "fields": fields, "found": True, "created_by": user_id,
                             "created_at": now},
            "$set": {"last_seen": now}, "$inc": {"count": 1}}, upsert=True)

    # ── reading ──────────────────────────────────────────────────────────────
    def list(self, viewer: Viewer, *, event_type: Optional[str] = None, before: Optional[str] = None,
             before_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """Newest first; a VE Admin / Owner sees every event, anyone else
        the ones they sent and the ones that started their triggers."""
        query: Dict[str, Any] = {}
        if event_type:
            query["type"] = event_type
        if not viewer.sees_all:
            query["visible_to"] = viewer.user_id
        if before is not None:
            after: List[Dict[str, Any]] = [{"time": {"$lt": before}}]
            if before_id is not None:
                after.append({"time": before, "_id": {"$lt": before_id}})
            query = {"$and": [query, {"$or": after}]} if query else {"$or": after}
        limit = max(1, min(limit, PAGE_MAX))
        docs = list(self._events.find(query).sort([("time", DESCENDING), ("_id", DESCENDING)]).limit(limit + 1))
        more = len(docs) > limit
        docs = docs[:limit]
        last = docs[-1] if docs else None
        return {"events": [self._public(d) for d in docs],
                "next": {"before": last["time"], "before_id": last["_id"]} if more and last else None}

    def get(self, viewer: Viewer, event_id: str) -> Dict[str, Any]:
        doc = self._events.find_one({"_id": event_id})
        if doc is None or not (viewer.sees_all or viewer.user_id in (doc.get("visible_to") or [])):
            raise NotFoundError(f"No event {event_id}", error_name="EVENT_NOT_FOUND")
        return self._public(doc)

    @staticmethod
    def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in doc.items() if k not in ("_id", "visible_to")}

    def delete_before(self, cutoff: str) -> int:
        """For good: events older than the VE's retention (HistoryCleanup)."""
        return self._events.delete_many({"time": {"$lt": cutoff}}).deleted_count

    # ── event types ──────────────────────────────────────────────────────────
    def types(self) -> List[Dict[str, Any]]:
        """Built-in types first, then the VE's own (described or found), each
        with the choreographies whose newest version it starts."""
        used: Dict[str, List[Dict[str, Any]]] = {}
        for choreography in self._repository.list():
            for trigger in choreography.triggers:
                used.setdefault(trigger.get("event_type") or models.FILE_WRITTEN, []).append(
                    {"choreography": choreography.name, "choreography_id": choreography.choreography_id,
                     "trigger_id": trigger.get("trigger_id")})
        out = [{**t, "used_by": used.get(t["type"], []), "count": None, "last_seen": None}
               for t in BUILT_IN_EVENT_TYPES]
        for doc in self._types.find().sort("_id", 1):
            out.append({"type": doc["_id"], "built_in": False, "description": doc.get("description") or "",
                        "fields": doc.get("fields") or [], "found": bool(doc.get("found")),
                        "created_by": doc.get("created_by"), "created_at": doc.get("created_at"),
                        "count": doc.get("count") or 0, "last_seen": doc.get("last_seen"),
                        "used_by": used.get(doc["_id"], [])})
        return out

    def save_type(self, *, user_id: str, event_type: str, description: str = "",
                  fields: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Describes a type (new, or one found from an event): what it means
        and the fields its data has -- what the trigger dialog offers."""
        problem = event_type_problem(event_type, built_in_ok=False)
        if problem:
            raise InvalidRequestError(problem, error_name="EVENT_TYPE_INVALID")
        cleaned = []
        for field in fields or []:
            name = str(field.get("name") or "").strip()
            if not name:
                raise InvalidRequestError("every field needs a name", error_name="VEM_INVALID")
            kind = field.get("type") or "any"
            if kind not in FIELD_TYPES:
                raise InvalidRequestError(f"a field's type is one of {', '.join(FIELD_TYPES)}",
                                          error_name="VEM_INVALID")
            cleaned.append({"name": name, "type": kind, "description": str(field.get("description") or "")})
        now = self._now()
        self._types.update_one({"_id": event_type}, {
            "$set": {"description": description or "", "fields": cleaned, "found": False, "updated_by": user_id,
                     "updated_at": now},
            "$setOnInsert": {"created_by": user_id, "created_at": now, "count": 0}}, upsert=True)
        return next(t for t in self.types() if t["type"] == event_type)

    def delete_type(self, *, event_type: str) -> Dict[str, Any]:
        """Only a type no choreography's newest version starts on."""
        found = next((t for t in self.types() if t["type"] == event_type and not t.get("built_in")), None)
        if found is None:
            raise NotFoundError(f"No event type {event_type}", error_name="EVENT_TYPE_NOT_FOUND")
        if found["used_by"]:
            names = ", ".join(sorted({u["choreography"] for u in found["used_by"]}))
            raise ConflictError(f"{event_type} starts {names}: remove those triggers first",
                                error_name="EVENT_TYPE_IN_USE")
        self._types.delete_one({"_id": event_type})
        return {"type": event_type, "deleted": True}

    # ── "used before" suggestions ────────────────────────────────────────────
    def suggestions(self) -> Dict[str, Any]:
        """The event types and the conditions (arrows' and triggers') used in
        the newest version of every choreography, most used first."""
        types: Dict[str, Dict[str, Any]] = {}
        conditions: Dict[str, Dict[str, Any]] = {}

        def add_condition(condition: Optional[models.EdgeCondition], where: str, name: str,
                          event_type: Optional[str] = None) -> None:
            if condition is None:
                return
            value = condition.model_dump(mode="json")
            key = json.dumps({"where": where, "type": event_type, "condition": value}, sort_keys=True)
            row = conditions.setdefault(key, {"where": where, "event_type": event_type, "condition": value,
                                              "count": 0, "used_in": []})
            row["count"] += 1
            if name not in row["used_in"]:
                row["used_in"].append(name)

        for choreography in self._repository.list():
            for trigger in choreography.graph.triggers:
                row = types.setdefault(trigger.event_type, {"type": trigger.event_type, "count": 0, "used_in": []})
                row["count"] += 1
                if choreography.name not in row["used_in"]:
                    row["used_in"].append(choreography.name)
                add_condition(trigger.condition, "trigger", choreography.name, trigger.event_type)
            for edge in choreography.graph.edges:
                add_condition(edge.condition, "arrow", choreography.name)
        sent = {d["_id"]: d.get("count") or 0 for d in self._types.find({}, {"count": 1})}
        for event_type, count in sent.items():
            types.setdefault(event_type, {"type": event_type, "count": 0, "used_in": []})["sent"] = count

        def rank(row: Dict[str, Any]) -> tuple:
            return (-row["count"], -(row.get("sent") or 0), row.get("type") or "")

        return {"event_types": sorted(types.values(), key=rank),
                "conditions": sorted(conditions.values(), key=lambda r: (-r["count"], json.dumps(r["condition"])))}
