from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application import notifications as N
from axo_control_plane.application.bucket_events import save_triggers
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.ports import HistoryLog
from axo_control_plane.application.storage_metering import (
    GIB,
    STORAGE_SIZES_REPORTED,
    STORAGE_USAGE_REPORTED,
    StorageMetering,
    hours_in_month,
)
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.billing import attempt_cost, choreography_lines

VE_HEARTBEAT = "VirtualEnvironmentHeartbeat"
JOB_ATTEMPT_RECORDED = "JobAttemptRecorded"
METRIC_SUMMARY_REPORTED = "MetricSummaryReported"
OBJECT_STATE_HOUR_REPORTED = "ObjectStateHourReported"

# Lifecycles the heartbeat may move between on its own (the provisioner owns
# every other transition).
_LIVE = (V.RUNNING, V.SCALING)


def _ts(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class VemIntake:
    """What each VEM sends the Control Plane, saved before it is acked (so a
    crash before the ack only means the VEM sends it again, and the copy is
    recognized and skipped):

    - heartbeat -> only the latest is kept per VE; a history record is
      written only when lifecycle or health changes;
    - job attempt -> the permanent billing record, one per (job, attempt),
      with its cost worked out once on arrival (price list in force at the
      attempt's finish time; never changed afterwards);
    - minute summary -> kept for charts and the admin dashboard;
    - object state hour -> the VE's active-object state size for one hour,
      priced once on arrival as GB-months (``object_state_hours``, VE bill).

    And from axo_storage (source ``storage:<id>``): its per-minute request
    totals and hourly bucket sizes, for the storage bills.
    """

    def __init__(self, db: Database, history: HistoryLog, notifications: Notifications, catalog: PlanCatalog,
                 storage: Optional[StorageMetering] = None, now_fn: Callable[[], float] = time.time) -> None:
        self._ves = db["virtual_environments"]
        self._teams = db["teams"]
        self._attempts = db["job_attempts"]
        self._attempts.create_index("choreography_lines.choreography_id", sparse=True)
        self._minutes = db["metric_minutes"]
        self._object_hours = db["object_state_hours"]
        self._history = history
        self._notifications = notifications
        self._catalog = catalog
        self._now_fn = now_fn
        self.handlers: Dict[str, Callable[[str, Dict[str, Any]], bool]] = {
            VE_HEARTBEAT: self.heartbeat,
            JOB_ATTEMPT_RECORDED: self.job_attempt,
            METRIC_SUMMARY_REPORTED: self.metric_summary,
            OBJECT_STATE_HOUR_REPORTED: self.object_state_hour,
        }
        self.storage_handlers: Dict[str, Callable[[str, Dict[str, Any]], bool]] = (
            {STORAGE_USAGE_REPORTED: storage.usage, STORAGE_SIZES_REPORTED: storage.sizes} if storage else {})

    def handle(self, event_type: str, source_id: str, data: Dict[str, Any]) -> bool:
        """True = saved (or already saved / not ours to keep): ack it.
        False = couldn't save now: don't ack, the VEM resends."""
        if source_id.startswith("storage:"):
            storage_handler = self.storage_handlers.get(event_type)
            return storage_handler(source_id, data) if storage_handler else True
        handler = self.handlers.get(event_type)
        if handler is None:
            return True
        ve_id = source_id.split(":", 1)[1] if source_id.startswith("vem:") else ""
        claimed = data.get("virtual_environment_id")
        if not ve_id or (claimed and claimed != ve_id):
            return True                     # not from a VEM, or about another VE: drop
        return handler(ve_id, data)

    # ── heartbeat ────────────────────────────────────────────────────────────
    def heartbeat(self, ve_id: str, data: Dict[str, Any]) -> bool:
        doc = self._ves.find_one({"_id": ve_id}, {"lifecycle": 1, "health": 1, "desired": 1, "name": 1,
                                                 "owner": 1, "lifecycle_reason": 1})
        if doc is None or doc.get("lifecycle") == V.DELETED:
            return True
        now = self._now_fn()
        changes: Dict[str, Any] = {"heartbeat": data, "heartbeat_at": now, "idle_since": _ts(data.get("idle_since")),
                                   "vem_down_since": None}
        if doc.get("lifecycle_reason") == V.VEM_DOWN_REASON:
            changes["lifecycle_reason"] = ""     # it answers again
        lifecycle = data.get("lifecycle")
        health = data.get("health")
        if doc.get("desired") == V.DESIRED_RUNNING and doc.get("lifecycle") in _LIVE:
            if lifecycle in _LIVE:
                changes["lifecycle"] = lifecycle
            changes["health"] = health
        self._ves.update_one({"_id": ve_id}, {"$set": changes})
        if "triggers" in data:
            save_triggers(self._ves, ve_id, data.get("triggers") or [], _ts(data.get("created_at")) or now)
        before = (doc.get("lifecycle"), doc.get("health"))
        after = (changes.get("lifecycle", before[0]), changes.get("health", before[1]))
        if after != before:
            self._history.append(f"ve-{ve_id}", "VirtualEnvironmentStateChanged",
                                 {"ve_id": ve_id, "lifecycle": after[0], "health": after[1],
                                  "from_lifecycle": before[0], "from_health": before[1], "at": now})
            if after[1] in ("Degraded", "Unavailable") and after[1] != before[1]:
                self._notify_owner(doc, f"VE {doc.get('name')} is {after[1].lower()}",
                                   {"ve_id": ve_id, "health": after[1]})
        return True

    # ── job attempts (billing) ───────────────────────────────────────────────
    def job_attempt(self, ve_id: str, data: Dict[str, Any]) -> bool:
        key = f"{data.get('job_id')}:{data.get('attempt')}"
        now = self._now_fn()
        finished_at = _ts(data.get("finished_at")) or _ts(data.get("received_at")) or now
        prices_from, prices = self._catalog.prices_in_force(finished_at)
        cost = attempt_cost(data, prices, prices_from)
        record = {"_id": key, "ve_id": ve_id, "logged": False, "received_by_cp_at": now, **data,
                  "finished_at_ts": finished_at, "cost": cost.to_dict()}
        lines = choreography_lines(data)
        if lines:
            record["choreography_lines"] = lines
        try:
            self._attempts.insert_one(record)
        except DuplicateKeyError:
            existing = self._attempts.find_one({"_id": key}, {"logged": 1}) or {}
            if existing.get("logged"):
                return True                  # a resend of something already saved
        # History copy (Kurrent); the flag makes a resend finish this step
        # if the first try stopped between the two writes.
        self._history.append(f"usage-{ve_id}", JOB_ATTEMPT_RECORDED, data)
        self._attempts.update_one({"_id": key}, {"$set": {"logged": True}})
        return True

    def backfill_choreography_lines(self, batch: int = 500) -> int:
        """Choreography step attempts saved before calls between
        choreographies were recorded get their lines (safe to run on every
        start, by every copy)."""
        done = 0
        query = {"started_by_type": "choreography", "choreography_lines": {"$exists": False}}
        while True:
            docs = list(self._attempts.find(query).limit(batch))
            if not docs:
                return done
            for doc in docs:
                self._attempts.update_one({"_id": doc["_id"]},
                                          {"$set": {"choreography_lines": choreography_lines(doc)}})
            done += len(docs)

    # ── 1-minute summaries ───────────────────────────────────────────────────
    def metric_summary(self, ve_id: str, data: Dict[str, Any]) -> bool:
        # One document per bin and sender process (``part``): readers and the
        # tier rollups merge every document of a bin.
        key = f"{ve_id}:{data.get('component_kind')}:{data.get('component_id')}:{data.get('bin_start')}"
        if data.get("part"):
            key += f":{data['part']}"
        try:
            self._minutes.insert_one({"_id": key, "ve_id": ve_id, "bin_start": data.get("bin_start"),
                                      "bin_width_s": data.get("bin_width_s"), "summaries": data.get("summaries", []),
                                      "component_kind": data.get("component_kind"),
                                      "component_id": data.get("component_id")})
        except DuplicateKeyError:
            pass
        return True

    # ── active-object state (billing) ────────────────────────────────────────
    def object_state_hour(self, ve_id: str, data: Dict[str, Any]) -> bool:
        hour = float(data.get("hour_start") or 0.0)
        if hour <= 0:
            return True
        size = max(0, int(data.get("stored_bytes") or 0))
        gb_months = size / GIB / hours_in_month(hour)
        prices = self._catalog.prices(hour)
        try:
            self._object_hours.insert_one({
                "_id": f"{ve_id}:{int(hour)}", "ve_id": ve_id, "ts": hour, "bytes": size, "gb_months": gb_months,
                "cost": {"total_ucents": round(gb_months * prices.object_state_per_gb_month_ucents)},
                "bill_id": None, "received_by_cp_at": self._now_fn()})
        except DuplicateKeyError:
            pass                              # a resend (the hour is counted once)
        return True

    # ── a deleted VE's unsent messages ───────────────────────────────────────
    def salvage(self, ve_id: str, tar_bytes: bytes) -> int:
        """Reads the VEM outbox (a tar of its SQLite journal files, copied
        before the VEM container is removed) and saves what it still held."""
        saved = 0
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as archive:
                for member in archive.getmembers():
                    if not member.isfile() or not member.name.endswith(".db"):
                        continue
                    target = f"{tmp}/{saved}-{member.name.rsplit('/', 1)[-1]}"
                    with open(target, "wb") as out:
                        out.write(archive.extractfile(member).read())   # type: ignore[union-attr]
                    saved += self._read_journal(ve_id, target)
        return saved

    def _read_journal(self, ve_id: str, path: str) -> int:
        count = 0
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT event_type, source_id, data FROM outbox").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        for event_type, source_id, data in rows:
            if event_type == VE_HEARTBEAT:
                continue
            if self.handle(event_type, source_id or f"vem:{ve_id}", json.loads(data)):
                count += 1
        return count

    def _notify_owner(self, doc: Dict[str, Any], text: str, data: Dict[str, Any]) -> None:
        owner = doc.get("owner") or {}
        user_id = owner.get("id") if owner.get("kind") == V.OWNER_USER else \
            (self._teams.find_one({"_id": owner.get("id")}) or {}).get("owner_id")
        self._notifications.notify([user_id], N.VE_STATE, text, data)
