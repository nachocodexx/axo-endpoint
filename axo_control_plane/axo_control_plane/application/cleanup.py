from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application.buckets import Buckets
from axo_control_plane.application.ports import ContainerInfo, HistoryLog, Leases, StackDriver, StorageGateway, VemGateway
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import UnavailableError

CLEANUP_LOGS_BUCKET = "axo/cleanup-logs"
_VE_ROLES = ("vem", "kurrent", "mongo")


class Cleanup:
    """Finds containers the platform made that nobody owns any more, by their
    labels, and removes them -- after saving their last log lines.

    Leftover when:
    - VEM / Kurrent / Mongo: its VE is unknown or deleted;
    - endpoint: its VE is deleted, or that VE's VEM says it was removed (or
      doesn't know it);
    - runner: the endpoint it belongs to has no container any more.

    Safety: unlabelled containers are never touched; a VE whose VEM doesn't
    answer is skipped this round (never guess); a container is removed only
    if it looked like a leftover in two checks in a row, at least
    ``confirm_after_s`` apart (something being created right now is spared).

    The same pass removes deleted VEs' volumes and deleted buckets' objects
    once their grace period is over."""

    def __init__(self, db: Database, driver: StackDriver, vem: VemGateway, storage: StorageGateway,
                 buckets: Buckets, leases: Leases, history: HistoryLog, instance_id: str,
                 confirm_after_s: float = 45.0, log_lines: int = 500,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._ves = db["virtual_environments"]
        self._suspects = db["cleanup_suspects"]
        self._log = db["cleanup_log"]
        self._driver = driver
        self._vem = vem
        self._storage = storage
        self._buckets = buckets
        self._leases = leases
        self._history = history
        self._instance_id = instance_id
        self._confirm_after_s = confirm_after_s
        self._log_lines = log_lines
        self._now_fn = now_fn

    def run_forever(self, stop_event: threading.Event, interval_s: float = 60.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.tick()
            except Exception:
                pass

    def tick(self) -> List[str]:
        if not self._leases.acquire("cleanup", self._instance_id, 120.0):
            return []
        removed = self.remove_leftovers()
        self.remove_expired_volumes()
        self._buckets.purge_due()
        return removed

    # ── containers ───────────────────────────────────────────────────────────
    def remove_leftovers(self) -> List[str]:
        now = self._now_fn()
        containers = self._driver.list_labelled()
        endpoint_names = {c.labels.get("axo.endpoint") for c in containers if c.labels.get("axo.role") == "endpoint"}
        ve_docs: Dict[str, Optional[Dict[str, Any]]] = {}
        endpoints_by_ve: Dict[str, Optional[Dict[str, str]]] = {}
        suspects_now: Dict[str, str] = {}

        for info in containers:
            ve_id = info.labels.get("axo.ve", "")
            if ve_id not in ve_docs:
                ve_docs[ve_id] = self._ves.find_one({"_id": ve_id}) if ve_id else None
            reason = self._why_leftover(info, ve_docs[ve_id], endpoint_names, endpoints_by_ve)
            if reason:
                suspects_now[info.name] = reason

        removed: List[str] = []
        for name, reason in suspects_now.items():
            info = next(c for c in containers if c.name == name)
            seen = self._suspects.find_one({"_id": name})
            if seen is None:
                self._suspects.insert_one({"_id": name, "first_seen": now, "reason": reason,
                                           "labels": info.labels})
                continue
            if now - seen["first_seen"] < self._confirm_after_s:
                continue
            self._remove(info, reason)
            self._suspects.delete_one({"_id": name})
            removed.append(name)
        # No longer suspicious (e.g. the VEM recorded it meanwhile).
        self._suspects.delete_many({"_id": {"$nin": list(suspects_now)}})
        return removed

    def _why_leftover(self, info: ContainerInfo, ve: Optional[Dict[str, Any]], endpoint_names,
                      endpoints_by_ve: Dict[str, Optional[Dict[str, str]]]) -> Optional[str]:
        role = info.labels.get("axo.role")
        ve_id = info.labels.get("axo.ve", "")
        ve_gone = ve is None or ve.get("lifecycle") == V.DELETED
        if role == "network" or role is None:
            return None
        if role in _VE_ROLES:
            return f"its VE {ve_id or '?'} is gone" if ve_gone else None
        if role == "endpoint":
            if ve_gone:
                return f"its VE {ve_id or '?'} is gone"
            if ve.get("desired") != V.DESIRED_RUNNING or ve.get("lifecycle") not in (V.RUNNING, V.SCALING):
                return None                     # VEM may be stopped: don't judge now
            if ve_id not in endpoints_by_ve:
                try:
                    endpoints_by_ve[ve_id] = self._vem.endpoint_ids(ve_id)
                except UnavailableError:
                    endpoints_by_ve[ve_id] = None
            known = endpoints_by_ve[ve_id]
            if known is None:
                return None                     # VEM not answering: skip this VE
            state = known.get(info.labels.get("axo.endpoint", ""))
            if state is None:
                return "the VE manager doesn't know this endpoint"
            return "the VE manager removed this endpoint" if state == "removed" else None
        if role == "runner":
            if ve_gone:
                return f"its VE {ve_id or '?'} is gone"
            owner = info.labels.get("axo.endpoint")
            return None if owner in endpoint_names else f"its endpoint {owner} is gone"
        return None

    def _remove(self, info: ContainerInfo, reason: str) -> None:
        now = self._now_fn()
        object_name = None
        try:
            text = self._driver.logs(info.name, self._log_lines)
            object_name = f"{time.strftime('%Y/%m/%d', time.gmtime(now))}/{info.name}-{int(now)}.log"
            self._storage.put_object(CLEANUP_LOGS_BUCKET, object_name, text.encode("utf-8", "replace"))
        except Exception:
            object_name = None                  # removing still matters more than the log
        self._driver.remove(info.name)
        entry = {"container": info.name, "ve_id": info.labels.get("axo.ve"), "role": info.labels.get("axo.role"),
                 "endpoint": info.labels.get("axo.endpoint"), "reason": reason, "removed_at": now,
                 "log_object": f"{CLEANUP_LOGS_BUCKET}/{object_name}" if object_name else None}
        self._log.insert_one(dict(entry))
        self._history.append("platform-cleanup", "LeftoverContainerRemoved", entry)

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [{k: v for k, v in d.items() if k != "_id"}
                for d in self._log.find().sort("removed_at", -1).limit(limit)]

    # ── volumes after the grace period ──────────────────────────────────────
    def remove_expired_volumes(self) -> List[str]:
        done = []
        for doc in self._ves.find({"lifecycle": V.DELETED, "volumes_removed": {"$ne": True},
                                   "purge_after": {"$lte": self._now_fn()}}):
            ve_id = doc["_id"]
            for suffix in ("kurrent-data", "mongo-data", "vem-outbox"):
                self._driver.remove_volume(f"ve-{ve_id}-{suffix}")
            self._ves.update_one({"_id": ve_id}, {"$set": {"volumes_removed": True}})
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentVolumesRemoved", {"ve_id": ve_id,
                                                                                    "at": self._now_fn()})
            done.append(ve_id)
        return done
