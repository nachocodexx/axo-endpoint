from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo.collection import Collection

from axo_vem.domain.events.activity_recorder import ActivityRecorder

_ENVELOPE_TOP_LEVEL_FIELDS = frozenset({"user_id", "virtual_environment_id", "endpoint_id", "runtime_type"})


def _doc_to_dict(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc["event_id"] = doc.pop("_id")
    created_at = doc.get("created_at")
    if isinstance(created_at, datetime) and created_at.tzinfo is None:
        # PyMongo's default client isn't tz_aware, so datetimes read back out of
        # Mongo lose their tzinfo even though the wall-clock digits are UTC (see
        # record()/_parse_created_at below, which always stores UTC-aware values).
        # Without this, FastAPI's default jsonable_encoder emits a Z-less ISO
        # string, which browsers parse as local time instead of UTC.
        doc["created_at"] = created_at.replace(tzinfo=timezone.utc)
    return doc


def _parse_created_at(raw: str) -> datetime:
    """created_at arrives as an ISO8601 string (pydantic's model_dump(mode="json")
    on the publishing side, decoded back out of Kurrent's JSON bytes) -- stored
    as a real datetime so unified_activity's created_at index/sort behaves like
    one. .replace("Z", ...) is needed because fromisoformat only accepts a
    trailing "Z" from Python 3.11 onward and this project targets 3.9/3.10."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class MongoActivityRepository(ActivityRecorder):
    """Reads/writes the unified_activity collection -- the cross-cutting
    audit trail every recognized taxonomy event is recorded into
    unconditionally (see application/projector/dispatcher.py), independent
    of which aggregate (if any) it also targets. Returns plain dicts on
    read, not a fixed dataclass -- the shape is genuinely polymorphic per
    event_type (base envelope fields top-level, everything event-specific
    under `meta`), so forcing it into a fixed shape was the bug this
    replaces, not a missing field.

    record() absorbs projector/upserts.py's former record_unified_activity/
    _parse_created_at; get_timeline() is unchanged from repository/activity.py.
    """

    def __init__(self, collection: Collection) -> None:
        self._collection = collection
        self._collection.create_index("user_id")
        self._collection.create_index("virtual_environment_id")
        self._collection.create_index("endpoint_id")
        self._collection.create_index("meta.function_id")
        self._collection.create_index("meta.active_object_id")
        self._collection.create_index("meta.function_version")
        self._collection.create_index("meta.duration_ms")

    def record(self, event_type: str, data: Dict[str, Any]) -> None:
        meta = {
            k: v for k, v in data.items()
            if k not in _ENVELOPE_TOP_LEVEL_FIELDS and k not in ("event_id", "created_at")
        }
        doc = {
            "event_type": event_type,
            "created_at": _parse_created_at(data["created_at"]),
            "user_id": data.get("user_id"),
            "virtual_environment_id": data.get("virtual_environment_id"),
            "endpoint_id": data.get("endpoint_id"),
            "runtime_type": data.get("runtime_type"),
            "meta": meta,
        }
        self._collection.update_one({"_id": data["event_id"]}, {"$set": doc}, upsert=True)

    def get_timeline(
        self,
        *,
        user_id: Optional[str] = None,
        virtual_environment_id: Optional[str] = None,
        endpoint_id: Optional[str] = None,
        function_id: Optional[str] = None,
        active_object_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Newest-first, optionally filtered along any combination of the
        five indexed dimensions. function_id/active_object_id filter on
        their nested meta.* location -- see record()'s top-level/meta split.
        ``since`` (the VE's retention, or a narrower caller window) excludes
        anything older -- rows are never deleted for retention, only hidden."""
        query: Dict[str, Any] = {}
        if user_id is not None:
            query["user_id"] = user_id
        if virtual_environment_id is not None:
            query["virtual_environment_id"] = virtual_environment_id
        if endpoint_id is not None:
            query["endpoint_id"] = endpoint_id
        if function_id is not None:
            query["meta.function_id"] = function_id
        if active_object_id is not None:
            query["meta.active_object_id"] = active_object_id
        if since is not None:
            query["created_at"] = {"$gte": since}

        cursor = self._collection.find(query).sort("created_at", -1).limit(limit)
        return [_doc_to_dict(doc) for doc in cursor]

    def purge(
        self,
        *,
        endpoint_id: Optional[str] = None,
        virtual_environment_id: Optional[str] = None,
        function_id: Optional[str] = None,
        function_version: Optional[int] = None,
        choreography_id: Optional[str] = None,
    ) -> int:
        """Permanently deletes unified_activity rows matching the given
        filter(s) -- used by each entity's hard-delete ("purge") use case
        to remove its activity history alongside its read-model doc. Reuses
        get_timeline()'s query-key convention (function_version is new here
        since a function purge is scoped to one version, not the whole
        function_id)."""
        query: Dict[str, Any] = {}
        if endpoint_id is not None:
            query["endpoint_id"] = endpoint_id
        if virtual_environment_id is not None:
            query["virtual_environment_id"] = virtual_environment_id
        if function_id is not None:
            query["meta.function_id"] = function_id
        if function_version is not None:
            query["meta.function_version"] = function_version
        if choreography_id is not None:
            query["meta.choreography_id"] = choreography_id
        assert query, "purge() requires at least one filter"
        return self._collection.delete_many(query).deleted_count
