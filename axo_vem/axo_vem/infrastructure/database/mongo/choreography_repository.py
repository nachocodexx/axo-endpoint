from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymongo import DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from axo_vem.domain.choreography.choreography import Choreography
from axo_vem.domain.choreography.repository import ChoreographyRepository


def _plain(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "_id"}


class MongoChoreographyRepository(ChoreographyRepository):
    """``choreographies`` (newest version, ``_id`` = id), ``choreography_versions``
    (``<id>:<version>``) and ``choreography_names`` (``_id`` = name)."""

    def __init__(self, collection: Collection, versions: Optional[Collection] = None,
                 names: Optional[Collection] = None) -> None:
        self._collection = collection
        db = collection.database
        self._versions = versions if versions is not None else db["choreography_versions"]
        self._names = names if names is not None else db["choreography_names"]
        self._versions.create_index([("choreography_id", 1), ("version", DESCENDING)])
        self._collection.create_index("triggers.bucket")
        self._collection.create_index("triggers.event_type")

    def _live(self, query: Dict[str, Any]) -> Optional[Choreography]:
        doc = self._collection.find_one({**query, "deleted_at": None})
        return Choreography.from_dict(doc) if doc is not None else None

    def get(self, choreography_id: str) -> Optional[Choreography]:
        return self._live({"_id": choreography_id})

    def get_by_name(self, name: str) -> Optional[Choreography]:
        return self._live({"name": name})

    def list(self) -> List[Choreography]:
        return [Choreography.from_dict(d) for d in self._collection.find({"deleted_at": None}).sort("name", 1)]

    def save(self, choreography: Choreography) -> None:
        data = choreography.to_dict()
        existing = self._collection.find_one({"_id": choreography.choreography_id}, {"version": 1, "triggers": 1})
        if existing is not None and int(existing.get("version") or 0) > choreography.version:
            pass                                   # an older version arriving late: only its version row
        else:
            if existing is not None and not data["triggers"]:
                # a version from before triggers were saved with versions keeps the old ones
                data["triggers"] = [t for t in existing.get("triggers") or [] if "event_type" not in t]
            self._collection.update_one({"_id": choreography.choreography_id},
                                        {"$set": {**data, "deleted_at": None}}, upsert=True)
        version_doc = {k: v for k, v in data.items() if k != "triggers"}
        self._versions.update_one({"_id": f"{choreography.choreography_id}:{choreography.version}"},
                                  {"$set": version_doc}, upsert=True)

    def get_version(self, choreography_id: str, version: int) -> Optional[Choreography]:
        doc = self._versions.find_one({"_id": f"{choreography_id}:{version}"})
        return Choreography.from_dict(doc) if doc is not None else None

    def versions(self, choreography_id: str) -> List[Dict[str, Any]]:
        docs = self._versions.find({"choreography_id": choreography_id},
                                   {"version": 1, "name": 1, "updated_at": 1, "updated_by": 1, "_id": 0})
        return list(docs.sort("version", DESCENDING))

    def set_triggers(self, choreography_id: str, triggers: List[Dict[str, Any]]) -> None:
        self._collection.update_one({"_id": choreography_id}, {"$set": {"triggers": list(triggers)}})

    def reserve_name(self, name: str, choreography_id: str) -> bool:
        try:
            self._names.insert_one({"_id": name, "choreography_id": choreography_id})
            return True
        except DuplicateKeyError:
            holder = self._names.find_one({"_id": name})
            return bool(holder and holder.get("choreography_id") == choreography_id)

    def release_name(self, name: str, choreography_id: str) -> None:
        self._names.delete_one({"_id": name, "choreography_id": choreography_id})

    def soft_delete(self, choreography_id: str, deleted_at) -> None:
        self._collection.update_one({"_id": choreography_id}, {"$set": {"deleted_at": deleted_at}})

    def triggers_for_bucket(self, bucket: str) -> List[Choreography]:
        docs = self._collection.find({"deleted_at": None, "triggers.bucket": bucket})
        return [Choreography.from_dict(d) for d in docs]

    def triggers_for_event(self, event_type: str) -> List[Choreography]:
        docs = self._collection.find({"deleted_at": None, "triggers.event_type": event_type})
        return [Choreography.from_dict(d) for d in docs]

    def trigger_buckets(self) -> List[Dict[str, str]]:
        seen = set()
        for doc in self._collection.find({"deleted_at": None, "triggers": {"$ne": []}}, {"triggers": 1}):
            for trigger in doc.get("triggers") or []:
                pair = (trigger.get("bucket"), trigger.get("created_by"))
                if all(pair):
                    seen.add(pair)
        return [{"bucket": b, "created_by": u} for b, u in sorted(seen)]

    # used by purge
    def remove(self, choreography_id: str) -> None:
        self._collection.delete_one({"_id": choreography_id})
        self._versions.delete_many({"choreography_id": choreography_id})
        self._names.delete_many({"choreography_id": choreography_id})

    def raw(self, choreography_id: str) -> Optional[Dict[str, Any]]:
        doc = self._collection.find_one({"_id": choreography_id})
        return _plain(doc) if doc is not None else None
