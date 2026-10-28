from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from axo_vem.domain.execution.job import ACTIVE_STATUSES, FINAL_STATUSES, PHASES, Job, JobStatus
from axo_vem.domain.execution.repository import JobFilters, JobRepository, JobRow
from axo_vem.domain.visibility import Shared


class MongoJobRepository(JobRepository):
    """The ``jobs`` collection, keyed by job_id. ``phase`` = how far the
    current attempt is (PHASES); ``row_id`` / ``row_at`` = the jobs-list row
    a job belongs to (its own, or its choreography run's)."""

    def __init__(self, collection: Collection, attempts: Any = None) -> None:
        self._collection = collection
        self._attempts = attempts           # MongoJobAttemptRepository: removed along with their jobs
        self._collection.create_index([("function_id", ASCENDING), ("created_at", DESCENDING)])
        self._collection.create_index("created_at")
        self._collection.create_index([("row_at", DESCENDING), ("row_id", DESCENDING)])
        self._collection.create_index("keep_until")

    def get(self, job_id: str) -> Optional[Job]:
        doc = self._collection.find_one({"_id": job_id})
        return Job.from_dict(doc) if doc is not None else None

    def get_many(self, job_ids: List[str]) -> List[Job]:
        return [Job.from_dict(doc) for doc in self._collection.find({"_id": {"$in": list(job_ids)}})]

    def list_by_function(self, function_id: str, function_version: Optional[int] = None) -> List[Job]:
        query: Dict[str, Any] = {"function_id": function_id}
        if function_version is not None:
            query["function_version"] = function_version
        return [Job.from_dict(doc) for doc in self._collection.find(query).sort("created_at", -1)]

    def save(self, job: Job) -> None:
        data = job.to_dict()
        data["phase"] = PHASES[job.status]
        self._collection.update_one({"_id": job.job_id}, {"$set": data}, upsert=True)

    def submitted(self, job_id: str, fields: Dict[str, Any]) -> None:
        self._collection.update_one(
            {"_id": job_id},
            {"$set": {k: v for k, v in fields.items() if k not in ("status", "attempt", "phase")},
             "$setOnInsert": {"job_id": job_id, "status": JobStatus.QUEUED.value, "attempt": 1, "phase": 0}},
            upsert=True,
        )

    def advance(self, job_id: str, attempt: int, status: JobStatus, fields: Optional[Dict[str, Any]] = None,
                base: Optional[Dict[str, Any]] = None) -> bool:
        phase = PHASES[status]
        newer = {"$or": [{"attempt": {"$lt": attempt}}, {"attempt": attempt, "phase": {"$lt": phase}},
                         {"attempt": {"$exists": False}}]}
        if base:
            self._collection.update_one({"_id": job_id},
                                        {"$setOnInsert": {"job_id": job_id, **base}}, upsert=True)
        change = {"$set": {**(fields or {}), "status": status.value, "attempt": attempt, "phase": phase}}
        try:
            moved = self._collection.update_one({"_id": job_id, **newer}, change, upsert=True)
        except DuplicateKeyError:
            return False                      # known, and already further along
        return bool(moved.modified_count or moved.upserted_id is not None)

    def fill(self, job_id: str, fields: Dict[str, Any]) -> None:
        """Sets each field the job doesn't have yet (whatever its state)."""
        for name, value in fields.items():
            if value is None:
                continue
            self._collection.update_one({"_id": job_id, "$or": [{name: {"$exists": False}}, {name: None}]},
                                        {"$set": {name: value}})

    def update(self, job_id: str, fields: Dict[str, Any]) -> None:
        self._collection.update_one({"_id": job_id}, {"$set": fields})

    def page(self, filters: JobFilters, before: Optional[str] = None, before_id: Optional[str] = None,
             limit: int = 50) -> List[JobRow]:
        pipeline: List[Dict[str, Any]] = [
            {"$match": _query(filters)},
            {"$addFields": {"row_id": {"$ifNull": ["$row_id", "$_id"]},
                            "row_at": {"$ifNull": ["$row_at", "$created_at"]},
                            "row_run": {"$ifNull": ["$choreography.root_run_id", "$choreography.run_id"]}}},
            {"$group": {"_id": "$row_id", "row_at": {"$max": "$row_at"}, "jobs": {"$push": "$_id"},
                        "run_id": {"$max": "$row_run"}}},
        ]
        if before is not None:
            after_cursor: List[Dict[str, Any]] = [{"row_at": {"$lt": before}}]
            if before_id is not None:
                after_cursor.append({"row_at": before, "_id": {"$lt": before_id}})
            pipeline.append({"$match": {"$or": after_cursor}})
        pipeline += [{"$sort": {"row_at": -1, "_id": -1}}, {"$limit": max(1, limit)}]
        groups = list(self._collection.aggregate(pipeline))
        jobs = {job.job_id: job for job in self.get_many([j for g in groups for j in g["jobs"]])}
        rows = []
        for group in groups:
            members = sorted((jobs[j] for j in group["jobs"] if j in jobs),
                             key=lambda job: (job.created_at or "", job.job_id), reverse=True)
            run_id = group.get("run_id") if str(group["_id"]).startswith("run:") else None
            rows.append(JobRow(row_id=group["_id"], row_at=group.get("row_at"), jobs=members, run_id=run_id))
        return rows

    def count_for_run(self, run_id: str) -> int:
        return self._collection.count_documents({"choreography.run_id": run_id})

    # ── keeping results ──────────────────────────────────────────────────────
    def kept_results(self, visible_to: Optional[Shared] = None, before: Optional[str] = None,
                     before_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """The results bucket as a person may browse it: jobs whose result is
        kept, newest saved first, only those they may see; with how many
        files / bytes that is."""
        query: Dict[str, Any] = {"result.stored": True, "result.deleted_at": None}
        visible = visible_jobs(visible_to)
        if visible is not None:
            query = {"$and": [query, visible]}
        totals = list(self._collection.aggregate([
            {"$match": query},
            {"$group": {"_id": None, "files": {"$sum": 1}, "bytes": {"$sum": {"$ifNull": ["$result.size", 0]}}}}]))
        page = dict(query)
        if before is not None:
            after: List[Dict[str, Any]] = [{"result.saved_at": {"$lt": before}}]
            if before_id is not None:
                after.append({"result.saved_at": before, "_id": {"$lt": before_id}})
            page = {"$and": [query, {"$or": after}]}
        docs = list(self._collection.find(page).sort([("result.saved_at", DESCENDING), ("_id", DESCENDING)])
                    .limit(max(1, limit) + 1))
        more = len(docs) > limit
        docs = docs[:limit]
        rows = [{"job_id": d["_id"], **{k: (d.get("result") or {}).get(k) for k in ("name", "size", "version",
                                                                                   "saved_at")},
                 **{k: d.get(k) for k in ("kind", "function_id", "function_name", "function_version", "object_name",
                                          "object_method", "choreography", "started_by_name", "acting_user_id",
                                          "keep_until", "finished_at")}} for d in docs]
        last = docs[-1] if docs else None
        return {"results": rows, "files": totals[0]["files"] if totals else 0,
                "bytes": totals[0]["bytes"] if totals else 0,
                "next": ({"before": (last.get("result") or {}).get("saved_at"), "before_id": last["_id"]}
                         if more and last else None)}

    def results_due(self, now_iso: str, limit: int = 500) -> List[Job]:
        """Jobs whose kept result has passed its keep time."""
        docs = self._collection.find({"keep_until": {"$ne": None, "$lt": now_iso}, "result.stored": True,
                                      "result.deleted_at": None}).limit(limit)
        return [Job.from_dict(doc) for doc in docs]

    def kept_beyond(self, longest_s: float, limit: int = 1000) -> List[Job]:
        """Finished jobs whose result is kept longer than ``longest_s`` after
        they finished (or for ever) -- what lowering the VE's longest time
        shortens."""
        docs = self._collection.find({"result.stored": True, "result.deleted_at": None,
                                      "finished_at": {"$ne": None}}).limit(limit * 10)
        out = []
        for doc in docs:
            finished = _epoch(doc.get("finished_at"))
            until = _epoch(doc.get("keep_until")) if doc.get("keep_until") else None
            if finished is not None and (until is None or until - finished > longest_s + 1):
                out.append(Job.from_dict(doc))
                if len(out) >= limit:
                    break
        return out

    def delete_finished_before(self, cutoff: str) -> int:
        """Deletes, for good, finished jobs received before ``cutoff`` (ISO)
        whose result is no longer kept (or never was)."""
        query = {
            "status": {"$in": [s.value for s in FINAL_STATUSES]},
            "created_at": {"$lt": cutoff},
            "$or": [{"result.stored": {"$ne": True}}, {"result.deleted_at": {"$ne": None}}],
        }
        ids = [doc["_id"] for doc in self._collection.find(query, {"_id": 1})]
        if not ids:
            return 0
        if self._attempts is not None:
            self._attempts.delete_for(ids)
        return self._collection.delete_many({"_id": {"$in": ids}}).deleted_count

    def active(self, limit: int = 1000) -> List[Job]:
        docs = self._collection.find({"status": {"$in": [s.value for s in ACTIVE_STATUSES]}}).limit(limit)
        return [Job.from_dict(doc) for doc in docs]


def _query(filters: JobFilters) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if filters.function_id:
        query["function_id"] = filters.function_id
    if filters.function_version is not None:
        query["function_version"] = filters.function_version
    if filters.statuses:
        query["status"] = {"$in": list(filters.statuses)}
    if filters.outcomes:
        query["outcome"] = {"$in": list(filters.outcomes)}
    if filters.started_by_type:
        query["started_by_type"] = filters.started_by_type
    if filters.started_by_id:
        query["started_by_id"] = filters.started_by_id
    if filters.error_name:
        query["error.name"] = filters.error_name
    if filters.job_id:
        query["_id"] = {"$regex": "^" + re.escape(filters.job_id)}
    if filters.endpoint_id:
        query["endpoint_id"] = filters.endpoint_id
    if filters.choreography_id:
        query["choreography.choreography_id"] = filters.choreography_id
    if filters.run_id:      # a run's jobs, with those of the runs its steps called
        query["$or"] = [{"choreography.run_id": filters.run_id}, {"choreography.root_run_id": filters.run_id}]
    received: Dict[str, Any] = {}
    if filters.since:
        received["$gte"] = filters.since
    if filters.until:
        received["$lt"] = filters.until
    if received:
        query["created_at"] = received
    visible = visible_jobs(filters.visible_to)
    if visible is not None:
        query = {"$and": [query, visible]} if query else visible
    return query


def visible_jobs(shared: Optional[Shared]) -> Optional[Dict[str, Any]]:
    """The jobs a viewer may see (None = all): their own, a run of a
    choreography shared with them, or a job on its own of a shared object /
    function (domain/visibility.py)."""
    if shared is None:
        return None
    alone = {"$or": [{"choreography": None}, {"choreography.choreography_id": None}]}
    return {"$or": [
        {"acting_user_id": shared.user_id},
        {"choreography.choreography_id": {"$in": sorted(shared.choreography_ids)}},
        # a run called by a step of a shared choreography's run
        {"choreography.chain": {"$in": sorted(shared.choreography_ids)}},
        {"$and": [alone, {"kind": "object"}, {"object_name": {"$in": sorted(shared.object_names)}}]},
        {"$and": [alone, {"kind": {"$ne": "object"}}, {"function_id": {"$in": sorted(shared.function_ids)}}]},
    ]}


def _epoch(raw: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
