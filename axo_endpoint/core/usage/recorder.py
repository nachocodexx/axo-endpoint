from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Union

from axo_shared.errors import AxoError
from axo_shared.events import models as event_models

from axo_endpoint.core.events.bus import Event as BusEvent
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]

# Error code -> billing outcome. Anything not listed is the platform's fault
# (worker/container crash, spawn failure, ...) and is never billed.
_FAILED_USER_CODES = {
    3002,  # FunctionExecError: the function itself raised
    3007,  # JobTimeoutError: the function's own max_duration_seconds elapsed
}
_CANCELLED_CODES = {3008}            # JobCancelledError
_TIMED_OUT_IN_QUEUE_CODES = {3009}   # QueueWaitTimeoutError (max_wait elapsed before it ran)


def classify_outcome(error: Optional[AxoError]) -> str:
    """The JobAttemptRecorded outcome for a finished attempt (None = success)."""
    if error is None:
        return "completed"
    code = getattr(error, "code", 0)
    if code in _FAILED_USER_CODES:
        return "failed_user"
    if code in _CANCELLED_CODES:
        return "cancelled"
    if code in _TIMED_OUT_IN_QUEUE_CODES:
        return "timed_out_in_queue"
    return "failed_platform"


@dataclass
class JobUsageContext:
    """What a job brings with it from upstream, kept until it finishes. Travels
    inside a JOB_FORWARD envelope ("usage") so the endpoint that actually runs
    the job records the attempt with the original trace and received time."""

    job_id: str
    function_id: str
    function_version: int
    received_at: float
    trace_id: str
    attempt: int = 1
    started_by_type: str = "user"
    started_by_id: str = "unknown"
    upstream_waits_ms: Dict[str, float] = field(default_factory=dict)
    # How long the job may wait for a free runner (-1 = forever), counted
    # from received_at -- travels with a forward so the deadline holds.
    max_wait_s: float = -1.0
    # The PERSON this job acts for -- whose bucket access axo_storage checks
    # for its dataio: the user themself, an API key's owner, or whoever
    # started the choreography run. "" = unknown (dataio then refuses).
    acting_user_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_envelope(
        cls, envelope: Dict[str, Any], job_id: str, now: float, max_wait_s: float = -1.0,
    ) -> "JobUsageContext":
        """From a JOB_SUBMIT envelope (optional trace_id / started_by_* /
        layer_waits_ms set by the Control Plane / VEM), or a JOB_FORWARD's
        nested "usage" dict."""
        forwarded = envelope.get("usage")
        if isinstance(forwarded, dict):
            return cls(**{**forwarded, "job_id": job_id})
        return cls(
            job_id=job_id,
            function_id=envelope.get("function_id", ""),
            function_version=int(envelope.get("function_version") or 0),
            received_at=float(envelope.get("received_at") or now),
            trace_id=envelope.get("trace_id") or job_id,
            attempt=int(envelope.get("attempt") or 1),
            started_by_type=envelope.get("started_by_type") or "user",
            started_by_id=envelope.get("started_by_id") or envelope.get("user_id") or "unknown",
            upstream_waits_ms=dict(envelope.get("layer_waits_ms") or {}),
            max_wait_s=max_wait_s,
            acting_user_id=_acting_user(envelope),
        )


def _acting_user(envelope: Dict[str, Any]) -> str:
    """Set by the VEM / Control Plane as "acting_user_id"; for a job started
    directly by a user, that user is the one acting."""
    explicit = envelope.get("acting_user_id")
    if explicit:
        return str(explicit)
    if (envelope.get("started_by_type") or "user") == "user":
        return str(envelope.get("started_by_id") or envelope.get("user_id") or "")
    return ""


def _ts(epoch: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch is not None else None


class UsageRecorder:
    """Turns the internal job bus events into one JobAttemptRecorded per
    attempt -- the billing record -- and hands it to ``publish`` (the
    reliable publisher, so it is never lost).

    Only the endpoint that *ran* the attempt records it: JOB_COMPLETED /
    JOB_FAILED fire exactly once, on the executing endpoint (a forwarded job's
    origin never sees them). ``on_record`` lets metrics observe the same record.
    """

    def __init__(
        self,
        endpoint_id: str,
        virtual_environment_id: Optional[str],
        owner_type: str,
        owner_id: str,
        endpoint_cores: float,
        endpoint_ram_mb: int,
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_record: Optional[Callable[[event_models.JobAttemptRecorded], None]] = None,
        now_fn: Callable[[], float] = time.time,
        stale_after_s: float = 86_400.0,
        logger: _Logger = None,
    ) -> None:
        self._endpoint_id = endpoint_id
        self._virtual_environment_id = virtual_environment_id
        self._owner_type = owner_type
        self._owner_id = owner_id
        self._endpoint_cores = endpoint_cores
        self._endpoint_ram_mb = endpoint_ram_mb
        self._publish = publish
        self._on_record = on_record
        self._now_fn = now_fn
        self._stale_after_s = stale_after_s
        self._logger: _Logger = logger or DumbLogger()
        self._contexts: Dict[str, JobUsageContext] = {}
        self._lock = threading.Lock()

    # ── bus subscribers ──────────────────────────────────────────────────────
    def on_job_submitted(self, event: BusEvent) -> None:
        usage = event.payload.get("usage")
        if not isinstance(usage, dict):
            return
        context = JobUsageContext(**usage)
        with self._lock:
            self._contexts[context.job_id] = context
            if len(self._contexts) % 1000 == 0:
                self._prune_locked()

    def on_job_finished(self, event: BusEvent) -> None:
        payload = event.payload
        with self._lock:
            context = self._contexts.pop(payload["job_id"], None)
        if context is None:
            return  # submitted before this recorder existed, or forwarded away
        self._record(
            context,
            outcome=payload.get("outcome", "completed" if event.event_type == "JOB_COMPLETED" else "failed_platform"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at", event.timestamp),
        )

    # ── runtime hooks ────────────────────────────────────────────────────────
    def on_attempt_retried(self, job_id: str, error: AxoError, started_at: Optional[float]) -> None:
        """A failed attempt the runtime is about to retry: record it now (it
        consumed time and may be billable), then count the next attempt."""
        with self._lock:
            context = self._contexts.get(job_id)
            if context is None:
                return
            snapshot = JobUsageContext(**context.to_dict())
            context.attempt += 1
        self._record(snapshot, classify_outcome(error), started_at, self._now_fn())

    def take_for_forward(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Hand a job's context over to the endpoint it's forwarded to (which
        will record it); this endpoint forgets it."""
        with self._lock:
            context = self._contexts.pop(job_id, None)
        return context.to_dict() if context is not None else None

    def restore(self, context: Dict[str, Any]) -> None:
        """Undo take_for_forward() when the forward itself failed."""
        restored = JobUsageContext(**context)
        with self._lock:
            self._contexts[restored.job_id] = restored

    def acting_user(self, job_id: str) -> Optional[str]:
        """Who a running job acts for (for its dataio access checks)."""
        with self._lock:
            context = self._contexts.get(job_id)
        return context.acting_user_id or None if context is not None else None

    def pending_count(self) -> int:
        with self._lock:
            return len(self._contexts)

    # ── internals ────────────────────────────────────────────────────────────
    def _prune_locked(self) -> None:
        """Drop contexts that never finished here (e.g. invoke() failed before
        the job ever ran) so the map can't grow forever."""
        cutoff = self._now_fn() - self._stale_after_s
        for job_id in [j for j, c in self._contexts.items() if c.received_at < cutoff]:
            del self._contexts[job_id]

    def _record(
        self, context: JobUsageContext, outcome: str, started_at: Optional[float], finished_at: Optional[float],
    ) -> None:
        waits = dict(context.upstream_waits_ms)
        execution_ms = 0.0
        if started_at is not None:
            waits["endpoint"] = round(max(0.0, started_at - context.received_at) * 1000, 3)
            if finished_at is not None:
                execution_ms = round(max(0.0, finished_at - started_at) * 1000, 3)
        record = event_models.JobAttemptRecorded(
            endpoint_id=self._endpoint_id,
            virtual_environment_id=self._virtual_environment_id,
            job_id=context.job_id,
            attempt=context.attempt,
            trace_id=context.trace_id,
            outcome=outcome,
            owner_type=self._owner_type,
            owner_id=self._owner_id,
            started_by_type=context.started_by_type,
            started_by_id=context.started_by_id,
            function_id=context.function_id,
            function_version=context.function_version,
            endpoint_cores=self._endpoint_cores,
            endpoint_ram_mb=self._endpoint_ram_mb,
            received_at=_ts(context.received_at),
            started_at=_ts(started_at),
            finished_at=_ts(finished_at),
            layer_waits_ms=waits,
            execution_ms=execution_ms,
        )
        self._logger.debug_event(
            Event.Usage.RECORDED,
            component=Component.USAGE,
            job_id=record.job_id,
            attempt=record.attempt,
            outcome=record.outcome,
            billable=record.billable,
            execution_ms=record.execution_ms,
        )
        if self._publish is not None:
            self._publish(event_models.JOB_ATTEMPT_RECORDED, record.model_dump(mode="json"))
        if self._on_record is not None:
            self._on_record(record)
