from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from axo_shared.messaging import InMemoryJournal, Journal, ReliableSender, SqliteJournal, ZmqDealerTransport
from axo_shared.metrics import names as m

from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.domain.events import models
from axo_vem.domain.objects.repository import ActiveObjectRepository
from axo_vem.domain.events.publisher import EventPublisher


def open_journal(outbox_path: str, virtual_environment_id: str) -> Journal:
    if outbox_path == ":memory:":
        return InMemoryJournal()
    os.makedirs(outbox_path, exist_ok=True)
    return SqliteJournal(os.path.join(outbox_path, f"vem-{virtual_environment_id}.db"))


MAX_CATCH_UP_HOURS = 31 * 24


class ControlPlaneLink:
    """Everything this VEM tells the Control Plane, through one reliable
    channel (a local journal, resent until the CP acks each message id):

    - a heartbeat every few seconds (lifecycle, health, endpoint counts,
      idle since);
    - every job attempt (billing -- must never be lost);
    - 1-minute summaries of the endpoints' statistics (merged exactly from
      their 10 s bins; the detail stays in the VE);
    - once per finished hour, the VE's active-object state size (billed as
      GB-months); hours missed while the VEM was down or asleep are sent on
      the next start, at the size last known (at most ``MAX_CATCH_UP_HOURS``).

    With no CP address configured, heartbeats and summaries are off, and job
    attempts go to the VEM's own event log instead (stream usage-<ve>), so
    billing records still survive."""

    def __init__(
        self,
        virtual_environment_id: str,
        status_fn: Callable[[], Dict[str, Any]],
        metrics: MetricsStore,
        event_log: EventPublisher,
        sender: Optional[ReliableSender] = None,
        objects: Optional[ActiveObjectRepository] = None,
        triggers_fn: Callable[[], List[Dict[str, str]]] = list,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ve_id = virtual_environment_id
        self._status_fn = status_fn
        self._metrics = metrics
        self._event_log = event_log
        self._sender = sender
        self._objects = objects
        self._triggers_fn = triggers_fn
        self._now_fn = now_fn
        self._last_summary_minute = 0.0
        self._sent_minutes: Set[float] = set()
        self._part = uuid.uuid4().hex[:12]        # this process's minute summaries

    @classmethod
    def connect(cls, cp_uri: str, outbox_path: str, virtual_environment_id: str, **kwargs) -> "ControlPlaneLink":
        sender = ReliableSender(
            open_journal(outbox_path, virtual_environment_id),
            ZmqDealerTransport(cp_uri),
            source_id=f"vem:{virtual_environment_id}",
        )
        sender.start()
        return cls(virtual_environment_id, sender=sender, **kwargs)

    @property
    def connected(self) -> bool:
        return self._sender is not None

    def _publish(self, event_type: str, data: Dict[str, Any]) -> bool:
        if self._sender is None:
            return False
        return self._sender.publish(event_type, data).is_ok

    # ── job attempts (from the endpoints, via ingestion) ────────────────────
    def job_attempt(self, data: Dict[str, Any]) -> bool:
        """Ingestion acks the endpoint only when this returns True -- the
        record is then durable here (journal or event log)."""
        if self._sender is not None:
            return self._publish(models.JOB_ATTEMPT_RECORDED, data)
        self._event_log.append_to_stream(f"usage-{self._ve_id}", models.JOB_ATTEMPT_RECORDED, data)
        return True

    # ── heartbeat ────────────────────────────────────────────────────────────
    def heartbeat(self) -> Optional[models.VirtualEnvironmentHeartbeat]:
        if self._sender is None:
            return None
        status = self._status_fn()
        counts = status.get("endpoints", {})
        idle = status.get("idle_since")
        event = models.VirtualEnvironmentHeartbeat(
            virtual_environment_id=self._ve_id,
            lifecycle=status["lifecycle"],
            health=status.get("health"),
            target_endpoints=status["target_endpoints"],
            min_endpoints=status["min_endpoints"],
            running=counts.get("running", 0),
            starting=counts.get("creating", 0) + counts.get("starting", 0),
            leaving=counts.get("draining", 0) + counts.get("removing", 0),
            failed=counts.get("failed", 0),
            sleeping=counts.get("sleeping", 0),
            idle_since=datetime.fromisoformat(idle) if idle else None,
            outbox_pending=self.pending(),
            triggers=self._triggers_fn(),
            created_at=datetime.fromtimestamp(self._now_fn(), tz=timezone.utc),
        )
        self._publish(models.VE_HEARTBEAT, event.model_dump(mode="json"))
        return event

    # ── 1-minute summaries ───────────────────────────────────────────────────
    def send_minute_summaries(self, everything: bool = False) -> int:
        """Every complete minute not sent yet, one MetricSummaryReported each.
        ``everything``: the minute still going too (the VE is stopping, so
        nothing more comes for it from this process)."""
        if self._sender is None:
            return 0
        minute_end = (self._now_fn() // 60) * 60 + (60 if everything else 0)
        by_minute: Dict[float, list] = {}
        for record in self._metrics.minute_summaries(before=minute_end):
            if record.bin_start not in self._sent_minutes:
                by_minute.setdefault(record.bin_start, []).append(record)
        for minute, records in sorted(by_minute.items()):
            event = models.MetricSummaryReported(
                virtual_environment_id=self._ve_id, component_kind=m.LAYER_VEM, component_id=self._ve_id,
                bin_start=minute, bin_width_s=60, part=self._part,
                summaries=[models.MetricSummary(metric=r.metric, dims=dict(r.dims), stats=r.stats.to_dict())
                           for r in records],
            )
            self._publish(models.METRIC_SUMMARY_REPORTED, event.model_dump(mode="json"))
            self._sent_minutes.add(minute)
        cutoff = minute_end - 7200
        self._sent_minutes = {mi for mi in self._sent_minutes if mi >= cutoff}
        return len(by_minute)

    # ── object state (billing) ───────────────────────────────────────────────
    def send_object_state_hours(self) -> int:
        """One ObjectStateHourReported per finished hour not sent yet."""
        if self._sender is None or self._objects is None:
            return 0
        size = self._objects.stored_bytes()
        if size is None:
            return 0                                  # no object has ever been saved here
        current = (self._now_fn() // 3600) * 3600
        last = self._objects.last_billed_hour()
        hour = current - 3600 if last is None else last + 3600
        hour = max(hour, current - MAX_CATCH_UP_HOURS * 3600)
        sent = 0
        while hour < current:
            event = models.ObjectStateHourReported(virtual_environment_id=self._ve_id, hour_start=hour,
                                                   stored_bytes=int(size))
            if not self._publish(models.OBJECT_STATE_HOUR_REPORTED, event.model_dump(mode="json")):
                break                                 # the journal refused it: try again next time
            self._objects.mark_billed_hour(hour)
            hour += 3600
            sent += 1
        return sent

    # ── delivery ─────────────────────────────────────────────────────────────
    def pending(self) -> int:
        return self._sender.stats().get("pending", 0) if self._sender is not None else 0

    def settle(self, timeout_s: float = 30.0, reports_wait_s: float = 3.0) -> bool:
        """Before the CP shuts this VEM down (stop, sleep, delete): wait a
        moment for the stopped endpoints' last reports, send every minute
        still in memory (else it dies with the process), then flush."""
        if self._sender is None:
            return True
        time.sleep(reports_wait_s)
        self.send_minute_summaries(everything=True)
        return self.flush(timeout_s)

    def flush(self, timeout_s: float = 30.0, poll_s: float = 0.2) -> bool:
        """Wait until the CP acked everything queued (before a stop/delete)."""
        deadline = time.monotonic() + timeout_s
        while self.pending() and time.monotonic() < deadline:
            time.sleep(poll_s)
        return self.pending() == 0

    def run_forever(self, stop_event: threading.Event, heartbeat_s: float = 5.0) -> None:
        last_summary = 0.0
        while not stop_event.wait(heartbeat_s):
            try:
                self.heartbeat()
                if time.monotonic() - last_summary >= 60:
                    self.send_minute_summaries()
                    self.send_object_state_hours()
                    last_summary = time.monotonic()
            except Exception:
                pass  # the next beat tries again; delivery itself is retried by the sender

    def close(self) -> None:
        if self._sender is not None:
            self._sender.stop()
