from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set

from axo_shared.messaging import InMemoryJournal, Journal, ReliableSender, SqliteJournal, ZmqDealerTransport
from axo_shared.metrics import names as m

from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher


def open_journal(outbox_path: str, virtual_environment_id: str) -> Journal:
    if outbox_path == ":memory:":
        return InMemoryJournal()
    os.makedirs(outbox_path, exist_ok=True)
    return SqliteJournal(os.path.join(outbox_path, f"vem-{virtual_environment_id}.db"))


class ControlPlaneLink:
    """Everything this VEM tells the Control Plane, through one reliable
    channel (a local journal, resent until the CP acks each message id):

    - a heartbeat every few seconds (lifecycle, health, endpoint counts,
      idle since);
    - every job attempt (billing -- must never be lost);
    - 1-minute summaries of the endpoints' statistics (merged exactly from
      their 10 s bins; the detail stays in the VE).

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
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ve_id = virtual_environment_id
        self._status_fn = status_fn
        self._metrics = metrics
        self._event_log = event_log
        self._sender = sender
        self._now_fn = now_fn
        self._last_summary_minute = 0.0
        self._sent_minutes: Set[float] = set()

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
            created_at=datetime.fromtimestamp(self._now_fn(), tz=timezone.utc),
        )
        self._publish(models.VE_HEARTBEAT, event.model_dump(mode="json"))
        return event

    # ── 1-minute summaries ───────────────────────────────────────────────────
    def send_minute_summaries(self) -> int:
        """Every complete minute not sent yet, one MetricSummaryReported each."""
        if self._sender is None:
            return 0
        minute_end = (self._now_fn() // 60) * 60
        by_minute: Dict[float, list] = {}
        for record in self._metrics.minute_summaries(before=minute_end):
            if record.bin_start not in self._sent_minutes:
                by_minute.setdefault(record.bin_start, []).append(record)
        for minute, records in sorted(by_minute.items()):
            event = models.MetricSummaryReported(
                virtual_environment_id=self._ve_id, component_kind=m.LAYER_VEM, component_id=self._ve_id,
                bin_start=minute, bin_width_s=60,
                summaries=[models.MetricSummary(metric=r.metric, dims=dict(r.dims), stats=r.stats.to_dict())
                           for r in records],
            )
            self._publish(models.METRIC_SUMMARY_REPORTED, event.model_dump(mode="json"))
            self._sent_minutes.add(minute)
        cutoff = minute_end - 7200
        self._sent_minutes = {mi for mi in self._sent_minutes if mi >= cutoff}
        return len(by_minute)

    # ── delivery ─────────────────────────────────────────────────────────────
    def pending(self) -> int:
        return self._sender.stats().get("pending", 0) if self._sender is not None else 0

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
                    last_summary = time.monotonic()
            except Exception:
                pass  # the next beat tries again; delivery itself is retried by the sender

    def close(self) -> None:
        if self._sender is not None:
            self._sender.stop()
