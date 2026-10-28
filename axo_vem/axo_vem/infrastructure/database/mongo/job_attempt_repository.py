from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymongo import ASCENDING
from pymongo.collection import Collection


class MongoJobAttemptRepository:
    """Every attempt of every job (Mongo ``job_attempts``, ``<job_id>:<n>``):
    the endpoint's JobAttemptRecorded as it came, traceback included (the
    Control Plane gets it without)."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection
        self._collection.create_index([("job_id", ASCENDING), ("attempt", ASCENDING)])

    def save(self, record: Dict[str, Any]) -> None:
        doc_id = f"{record['job_id']}:{int(record.get('attempt') or 1)}"
        self._collection.update_one({"_id": doc_id}, {"$set": {k: v for k, v in record.items() if k != "_id"}},
                                    upsert=True)

    def list(self, job_id: str) -> List[Dict[str, Any]]:
        docs = self._collection.find({"job_id": job_id}).sort("attempt", ASCENDING)
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in docs]

    def last_number(self, job_id: str) -> Optional[int]:
        doc = self._collection.find_one({"job_id": job_id}, sort=[("attempt", -1)])
        return int(doc["attempt"]) if doc else None

    def delete_for(self, job_ids: List[str]) -> int:
        return self._collection.delete_many({"job_id": {"$in": list(job_ids)}}).deleted_count
