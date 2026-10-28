from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pymongo.collection import Collection
from pymongo.database import Database

from axo_control_plane.application.buckets import StorageAccess
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.domain import virtual_environment as V

# axo_storage's report that one file version became complete.
STORAGE_OBJECT_WRITTEN = "StorageObjectWritten"
BUCKET_EVENTS_PATH = "/bucket-events"


TRIGGER_BUCKETS_PATH = "/trigger-buckets"


def save_triggers(ves: Collection, ve_id: str, triggers: List[Dict[str, str]], at: float) -> bool:
    """Keeps a VE's trigger list (``triggers``, ``triggers_at``) unless a
    newer one is already kept. ``at`` = when the VEM made the list (its
    heartbeat's created_at, or its /trigger-buckets answer): a heartbeat
    built before a trigger was added but arriving after the front door read
    the new list can't put the old one back."""
    result = ves.update_one(
        {"_id": ve_id, "$or": [{"triggers_at": {"$exists": False}}, {"triggers_at": {"$lte": at}}]},
        {"$set": {"triggers": list(triggers or []), "triggers_at": at}})
    return result.matched_count == 1


def bucket_event_id(ve_id: str, bucket_key: str, name: str, version: Any) -> str:
    """One delivery per VE and file version, however often it's reported."""
    key = f"{ve_id}:{bucket_key}:{name}:{version}"
    return "bev-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


class BucketEvents:
    """New files -> the VEs whose choreography triggers watch that bucket.

    Each VEM lists its triggers' buckets (as people wrote them: a name or an
    alias) and who added each one in its heartbeat; the front door also reads
    that list right after any call that changes triggers (save_triggers), so
    a file written just after a save is never missed. For a file written in a
    bucket, every VE that is running or sleeping and has a trigger on a name
    of that bucket gets one ``POST /bucket-events`` -- only for trigger
    creators who may read the bucket and are Developer+ in that VE (the runs
    act for them), listed in ``allowed_users``. Delivery goes through the
    SleepManager's saved requests: in order, retried until the VEM answers,
    and a sleeping VE is woken. Stopped VEs get nothing.
    """

    def __init__(self, db: Database, access: StorageAccess, sleep: SleepManager) -> None:
        self._ves = db["virtual_environments"]
        self._access = access
        self._sleep = sleep

    def written(self, source_id: str, data: Dict[str, Any]) -> bool:
        key, name = data.get("bucket_key"), data.get("name")
        if not key or not name:
            return True                                  # malformed: nothing to deliver
        query = {"desired": {"$in": [V.DESIRED_RUNNING, V.DESIRED_SLEEPING]}, "triggers.0": {"$exists": True}}
        for doc in self._ves.find(query, {"triggers": 1}):
            for bucket, allowed in self._matching(doc["_id"], key, doc["triggers"]).items():
                self._sleep.enqueue(
                    doc["_id"], bucket_event_id(doc["_id"], key, name, data.get("version")), BUCKET_EVENTS_PATH,
                    {"bucket": bucket, "name": name, "version": data.get("version"), "format": data.get("format"),
                     "source": data.get("source"), "allowed_users": allowed},
                    acting_user=allowed[0])
        return True

    def _matching(self, ve_id: str, bucket_key: str, triggers: List[Dict[str, str]]) -> Dict[str, List[str]]:
        """Trigger bucket name -> the creators allowed, for names of this bucket."""
        allowed: Dict[str, List[str]] = {}
        for trigger in triggers:
            bucket, user = trigger.get("bucket"), trigger.get("created_by")
            if not bucket or not user:
                continue
            answer = self._check(user, bucket, ve_id)
            if answer is not None and answer.get("bucket_key") == bucket_key and answer.get("allowed"):
                allowed.setdefault(bucket, []).append(user)
        return allowed

    def _check(self, user_id: str, bucket: str, ve_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._access.check(user_id, bucket, "read", ve_id)
        except Exception:     # an unreadable trigger never stops the others
            return None
