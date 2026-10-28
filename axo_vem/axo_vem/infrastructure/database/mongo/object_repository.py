from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymongo import DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from axo_vem.domain.objects.repository import ActiveObjectRepository, ObjectClassRepository

CREATE_METHOD = "__create__"


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if k != "_id"}


class MongoObjectClassRepository(ObjectClassRepository):
    def __init__(self, collection: Collection) -> None:
        self._collection = collection
        self._collection.create_index([("class_name", 1), ("version", DESCENDING)])
        self._collection.create_index([("class_name", 1), ("code_hash", 1)])

    def save(self, doc: Dict[str, Any]) -> None:
        key = f"{doc['class_name']}:{doc['version']}"
        self._collection.update_one({"_id": key}, {"$set": dict(doc)}, upsert=True)

    def get(self, class_name: str, version: int) -> Optional[Dict[str, Any]]:
        return _clean(self._collection.find_one({"_id": f"{class_name}:{version}"}))

    def latest(self, class_name: str) -> Optional[Dict[str, Any]]:
        return _clean(self._collection.find_one({"class_name": class_name}, sort=[("version", DESCENDING)]))

    def find_by_code_hash(self, class_name: str, code_hash: str) -> Optional[Dict[str, Any]]:
        return _clean(self._collection.find_one({"class_name": class_name, "code_hash": code_hash},
                                                sort=[("version", DESCENDING)]))

    def list(self, class_name: Optional[str] = None) -> List[Dict[str, Any]]:
        query = {"class_name": class_name} if class_name else {}
        return [_clean(d) for d in self._collection.find(query).sort([("class_name", 1), ("version", 1)])]

    def remove(self, class_name: str, version: int) -> None:
        self._collection.delete_one({"_id": f"{class_name}:{version}"})


class MongoActiveObjectRepository(ActiveObjectRepository):
    def __init__(self, collection: Collection, totals: Optional[Collection] = None) -> None:
        self._collection = collection
        self._collection.create_index("object_id")
        # one document: the VE's whole state size and the last hour billed
        self._totals = totals if totals is not None else collection.database["object_state_totals"]

    def reserve(self, doc: Dict[str, Any]) -> bool:
        try:
            self._collection.insert_one({"_id": doc["name"], **doc})
            return True
        except DuplicateKeyError:
            return False

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return _clean(self._collection.find_one({"_id": name}))

    def list(self) -> List[Dict[str, Any]]:
        return [_clean(d) for d in self._collection.find().sort("_id", 1)]

    def using(self, class_name: str, version: Optional[int] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"class_name": class_name}
        if version is not None:
            query["class_version"] = int(version)
        return [doc for doc in (_clean(d) for d in self._collection.find(query)) if doc is not None]

    def remove(self, name: str, object_id: Optional[str] = None) -> None:
        query: Dict[str, Any] = {"_id": name}
        if object_id is not None:
            query["object_id"] = object_id
        self._collection.delete_one(query)

    def mark_ready(self, name: str, object_id: str) -> None:
        self._collection.update_one({"_id": name, "object_id": object_id}, {"$set": {"status": "ready"}})

    def apply_created(self, data: Dict[str, Any]) -> None:
        """Also rebuilds a reservation when the read model is replayed."""
        fields = {
            "object_id": data["object_id"], "name": data["name"], "class_name": data["class_name"],
            "class_version": data["class_version"], "cloned_from": data.get("cloned_from"),
            "status": "ready", "created_at": data.get("created_at"),
        }
        if data.get("created_by"):
            fields["created_by"] = data["created_by"]
        self._collection.update_one({"_id": data["name"]}, {"$set": fields}, upsert=True)

    def apply_called(self, data: Dict[str, Any]) -> None:
        if data.get("method") == CREATE_METHOD and data.get("outcome") != "completed":
            # __init__ failed: the name is free again.
            self._collection.delete_one({"object_id": data["object_id"], "status": "creating"})
            return
        update: Dict[str, Any] = {"$set": {"last_call_at": data.get("created_at"), "last_outcome": data.get("outcome"),
                                           "last_method": data.get("method")},
                                  "$inc": {"calls": 1}}
        if data.get("outcome") == "completed":
            update["$set"].update({"revision": data.get("revision"), "size_bytes": data.get("size_bytes", 0)})
        self._collection.update_one({"object_id": data["object_id"]}, update)

    def apply_upgraded(self, data: Dict[str, Any]) -> None:
        self._collection.update_one({"object_id": data["object_id"]},
                                    {"$set": {"class_version": data["to_version"], "revision": data.get("revision")}})

    def apply_deleted(self, data: Dict[str, Any]) -> None:
        self._collection.delete_one({"object_id": data["object_id"]})

    def set_keep_revisions(self, name: str, keep_revisions: Optional[int]) -> None:
        self._collection.update_one({"_id": name}, {"$set": {"keep_revisions": keep_revisions}})

    def apply_sizes(self, sizes: Dict[str, int], stored_bytes: Optional[int] = None) -> None:
        for object_id, size in sizes.items():
            self._collection.update_one({"object_id": object_id}, {"$set": {"size_bytes": int(size)}})
        if stored_bytes is not None:
            self._totals.update_one({"_id": "ve"}, {"$set": {"stored_bytes": int(stored_bytes)}}, upsert=True)

    def stored_bytes(self) -> Optional[int]:
        doc = self._totals.find_one({"_id": "ve"}) or {}
        return doc.get("stored_bytes")

    def last_billed_hour(self) -> Optional[float]:
        doc = self._totals.find_one({"_id": "ve"}) or {}
        return doc.get("billed_hour")

    def mark_billed_hour(self, hour_start: float) -> None:
        self._totals.update_one({"_id": "ve"}, {"$set": {"billed_hour": float(hour_start)}}, upsert=True)
