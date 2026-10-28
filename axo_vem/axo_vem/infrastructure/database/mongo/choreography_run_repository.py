from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from axo_vem.domain.choreography.run import (
    ACTIVE_RUN_STATUSES, COMPLETED, COMPLETED_WITH_ERRORS, FAILED, ChoreographyRun,
)
from axo_vem.domain.choreography.run_repository import ChoreographyRunRepository

# How many recent runs the usage figures look at.
_USAGE_SAMPLE = 20000


def _percentile(sorted_values: List[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[index]


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else value.isoformat()


class MongoChoreographyRunRepository(ChoreographyRunRepository):
    """``choreography_runs``: one document per run (``_id`` = run id)."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection
        self._collection.create_index([("choreography_id", ASCENDING), ("queued_at", DESCENDING)])
        self._collection.create_index("status")
        self._collection.create_index("parent.run_id", sparse=True)

    def get(self, run_id: str) -> Optional[ChoreographyRun]:
        doc = self._collection.find_one({"_id": run_id})
        return ChoreographyRun.from_dict(doc) if doc is not None else None

    def save(self, run: ChoreographyRun) -> None:
        self._collection.update_one({"_id": run.run_id}, {"$set": run.to_dict()}, upsert=True)

    def insert(self, run: ChoreographyRun) -> bool:
        try:
            self._collection.insert_one({"_id": run.run_id, **run.to_dict()})
            return True
        except DuplicateKeyError:
            return False

    def list(self, choreography_id: Optional[str] = None, status: Optional[str] = None, since=None,
             limit: int = 100, acting_user_id: Optional[str] = None, started_by_type: Optional[str] = None,
             parent_run_id: Optional[str] = None) -> List[ChoreographyRun]:
        query: Dict[str, Any] = {}
        if started_by_type is not None:
            query["started_by_type"] = started_by_type
        if parent_run_id is not None:
            query["parent.run_id"] = parent_run_id
        if choreography_id is not None:
            query["choreography_id"] = choreography_id
        if acting_user_id is not None:
            query["acting_user_id"] = acting_user_id
        if status is not None:
            query["status"] = status
        if since is not None:
            query["queued_at"] = {"$gte": _iso(since)}
        docs = self._collection.find(query).sort("queued_at", DESCENDING).limit(max(1, min(limit, 1000)))
        return [ChoreographyRun.from_dict(d) for d in docs]

    def active(self) -> List[ChoreographyRun]:
        docs = self._collection.find({"status": {"$in": sorted(ACTIVE_RUN_STATUSES)}}).sort("queued_at", ASCENDING)
        return [ChoreographyRun.from_dict(d) for d in docs]

    def count(self, choreography_id: str, status: str) -> int:
        return self._collection.count_documents({"choreography_id": choreography_id, "status": status})

    def overview(self, since) -> Dict[str, Dict[str, Any]]:
        since_iso = _iso(since)
        errors = [FAILED, COMPLETED_WITH_ERRORS]
        pipeline: List[Dict[str, Any]] = [
            {"$sort": {"queued_at": DESCENDING}},
            {"$group": {
                "_id": "$choreography_id",
                "run_id": {"$first": "$run_id"}, "status": {"$first": "$status"},
                "version": {"$first": "$version"}, "queued_at": {"$first": "$queued_at"},
                "finished_at": {"$first": "$finished_at"},
                "runs_24h": {"$sum": {"$cond": [{"$gte": ["$queued_at", since_iso]}, 1, 0]}},
                "errors_24h": {"$sum": {"$cond": [{"$and": [{"$gte": ["$queued_at", since_iso]},
                                                            {"$in": ["$status", errors]}]}, 1, 0]}},
                "active_runs": {"$sum": {"$cond": [{"$in": ["$status", sorted(ACTIVE_RUN_STATUSES)]}, 1, 0]}},
            }},
        ]
        out: Dict[str, Dict[str, Any]] = {}
        for row in self._collection.aggregate(pipeline):
            out[row["_id"]] = {
                "last_run": {k: row.get(k) for k in ("run_id", "status", "version", "queued_at", "finished_at")},
                "runs_24h": row["runs_24h"], "errors_24h": row["errors_24h"], "active_runs": row["active_runs"],
            }
        return out

    def usage(self, choreography_id: str, since=None) -> Dict[str, Any]:
        """Runs by status / version / how they started / who they acted for,
        success rate and durations (finished runs only)."""
        query: Dict[str, Any] = {"choreography_id": choreography_id}
        if since is not None:
            query["queued_at"] = {"$gte": _iso(since)}
        fields = {"status": 1, "version": 1, "started_by_type": 1, "acting_user_id": 1, "duration_ms": 1}
        docs = list(self._collection.find(query, fields).sort("queued_at", DESCENDING).limit(_USAGE_SAMPLE))
        durations = sorted(float(d["duration_ms"]) for d in docs if d.get("duration_ms") is not None)
        by_status = Counter(d.get("status") for d in docs)
        finished = sum(n for s, n in by_status.items() if s not in ACTIVE_RUN_STATUSES)
        return {
            "runs": len(docs),
            "by_status": dict(by_status),
            "by_version": {str(k): v for k, v in Counter(d.get("version") for d in docs).items()},
            "by_trigger": dict(Counter(d.get("started_by_type") for d in docs)),
            "by_user": dict(Counter(d.get("acting_user_id") for d in docs).most_common(20)),
            "success_rate": (by_status.get(COMPLETED, 0) / finished) if finished else None,
            "duration_ms": {
                "avg": (sum(durations) / len(durations)) if durations else None,
                "p50": _percentile(durations, 0.5), "p95": _percentile(durations, 0.95),
                "max": durations[-1] if durations else None,
            },
            "sampled": len(docs) >= _USAGE_SAMPLE,
        }

    def delete_for(self, choreography_id: str) -> int:
        return self._collection.delete_many({"choreography_id": choreography_id}).deleted_count

    def delete_finished_before(self, cutoff) -> int:
        """Deletes runs that finished before ``cutoff`` for good (their
        steps' answers are kept -- or not -- with their jobs)."""
        return self._collection.delete_many({"finished_at": {"$ne": None, "$lt": _iso(cutoff)}}).deleted_count
