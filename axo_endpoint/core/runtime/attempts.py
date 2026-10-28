"""A job's attempts on this endpoint: the one place that decides whether a
failed attempt is tried again -- for both runtimes, under the same job id.

- the platform failed (a worker or container crashed, a runner couldn't
  start): tried again on its own, free, up to PLATFORM_MAX_ATTEMPTS
  platform-failed attempts;
- the function's code failed (it raised, or ran past its time): tried again
  only while the job's ``max_attempts`` allows, after ``retry_delay_seconds``
  (doubled each time when ``retry_backoff`` is "exponential");
- cancelled, or waited too long in the queue: never tried again.

Sits in front of the completion recorder (``wrap``): a failed attempt that
will be tried again never reaches it -- it's recorded on its own
(``on_attempt_failed``, the billing record) and the job is invoked again
when its delay is over. Only the job's final outcome goes on. A job waiting
for its next attempt can be cancelled (``cancel``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from option import Err, Result

from axo_shared.errors import AxoError
from axo_shared.runtime.spec import PLATFORM_MAX_ATTEMPTS, retry_delay

from axo_endpoint.core.errors import JobCancelledError
from axo_endpoint.core.runtime.base import FunctionRuntimeError, InvocationHandle
from axo_endpoint.core.runtime.queue_deadlines import QueueDeadlines
from axo_endpoint.core.storage.backend import StorageKey
from axo_endpoint.core.usage.recorder import classify_outcome
from axo_endpoint.log import DumbLogger
from axo_endpoint.log.catalog import Component, Event

# Why an attempt started (shown with each attempt).
FIRST = "first"
PLATFORM_RETRY = "platform_failed"
CODE_RETRY = "code_failed"
MANUAL_RETRY = "manual"
# Platform retries wait a little, growing: 1 s, 2 s, ...
PLATFORM_RETRY_DELAY_SECONDS = 1.0

OnComplete = Callable[[InvocationHandle, "Result[Any, FunctionRuntimeError]", Optional[List[str]]], None]
InvokeFn = Callable[[StorageKey, str, Dict[str, Any]], "Result[Any, FunctionRuntimeError]"]
# (job_id, error, started_at, retry_at, next_reason) -- a failed attempt that will be tried again.
OnAttemptFailed = Callable[[str, AxoError, Optional[float], float, str], None]


@dataclass(frozen=True)
class AttemptPolicy:
    max_attempts: int = 1
    retry_delay_seconds: float = 1.0
    retry_backoff: str = "fixed"


@dataclass
class _Job:
    function_ref: StorageKey
    params: Dict[str, Any]
    policy: AttemptPolicy
    attempt: int
    code_failures: int = 0
    platform_failures: int = 0
    timer: Optional[threading.Timer] = None
    waiting_since: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class JobAttempts:
    def __init__(
        self,
        invoke_fn: InvokeFn,
        on_attempt_failed: Optional[OnAttemptFailed] = None,
        on_retry_started: Optional[Callable[[str, int], None]] = None,
        now_fn: Callable[[], float] = time.time,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
        queue_deadlines: Optional[QueueDeadlines] = None,
        logger: Any = None,
    ) -> None:
        self._logger = logger or DumbLogger()
        self._invoke = invoke_fn
        self._on_attempt_failed = on_attempt_failed
        self._on_retry_started = on_retry_started
        self._now_fn = now_fn
        self._timer_factory = timer_factory
        self._jobs: Dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._queue_deadlines = queue_deadlines
        self._inner: Optional[OnComplete] = None
        self._wrapped: Optional[OnComplete] = None

    # ── what submit_job calls ────────────────────────────────────────────────
    def begin(self, job_id: str, function_ref: StorageKey, params: Dict[str, Any],
              policy: AttemptPolicy, attempt: int = 1, max_wait_s: float = -1.0) -> None:
        with self._lock:
            self._jobs[job_id] = _Job(function_ref=function_ref, params=dict(params), policy=policy,
                                      attempt=max(1, int(attempt)), extra={"max_wait_s": max_wait_s})

    def forget(self, job_id: str) -> None:
        """The job never started here (invoke refused it, or it was forwarded)."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None and job.timer is not None:
            job.timer.cancel()

    def take_for_forward(self, job_id: str) -> Dict[str, Any]:
        """The job's attempt settings for the endpoint it's forwarded to
        (which then runs -- and retries -- it); forgotten here."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return {}
        if job.timer is not None:
            job.timer.cancel()
        return {"max_attempts": job.policy.max_attempts, "retry_delay_seconds": job.policy.retry_delay_seconds,
                "retry_backoff": job.policy.retry_backoff}

    def restore(self, job_id: str, function_ref: StorageKey, params: Dict[str, Any], settings: Dict[str, Any],
                attempt: int, max_wait_s: float = -1.0) -> None:
        """Undo take_for_forward() when the forward itself failed."""
        if settings:
            self.begin(job_id, function_ref, params, AttemptPolicy(**settings), attempt, max_wait_s)

    def attempt_of(self, job_id: str) -> Optional[int]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.attempt if job is not None else None

    def waiting(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and job.timer is not None

    # ── the completion path ──────────────────────────────────────────────────
    def wrap(self, inner: OnComplete) -> OnComplete:
        self._inner = inner

        def on_complete(handle: InvocationHandle, outcome: "Result[Any, FunctionRuntimeError]",
                        warnings: Optional[List[str]] = None) -> None:
            if outcome.is_err and self._try_again(handle, outcome.unwrap_err()):
                return
            try:
                inner(handle, outcome, warnings)       # attempt_of() still answers in there
            finally:
                with self._lock:
                    self._jobs.pop(handle.job_id, None)

        self._wrapped = on_complete
        return on_complete

    def _try_again(self, handle: InvocationHandle, error: AxoError) -> bool:
        kind = classify_outcome(error)
        now = self._now_fn()
        with self._lock:
            job = self._jobs.get(handle.job_id)
            if job is None or job.timer is not None:
                return False
            if kind == "failed_platform" and job.platform_failures + 1 < PLATFORM_MAX_ATTEMPTS:
                job.platform_failures += 1
                delay = PLATFORM_RETRY_DELAY_SECONDS * job.platform_failures
                reason = PLATFORM_RETRY
            elif kind == "failed_user" and job.code_failures + 1 < job.policy.max_attempts:
                job.code_failures += 1
                delay = retry_delay(job.code_failures, job.policy.retry_delay_seconds, job.policy.retry_backoff)
                reason = CODE_RETRY
            else:
                if kind in ("failed_platform", "failed_user") and job.attempt > 1:
                    self._logger.info_event(Event.Job.RETRY_EXHAUSTED, component=Component.JOB_ATTEMPTS,
                                            job_id=handle.job_id, attempt=job.attempt, outcome=kind)
                return False
            job.waiting_since = now
            job.timer = self._timer_factory(delay, lambda: self._retry(handle.job_id))
            job.timer.daemon = True
            job.extra["next_reason"] = reason
        self._logger.info_event(Event.Job.RETRY_SCHEDULED, component=Component.JOB_ATTEMPTS, job_id=handle.job_id,
                                attempt=job.attempt, next_reason=reason, delay_s=delay, error_message=str(error))
        if self._on_attempt_failed is not None:
            self._on_attempt_failed(handle.job_id, error, handle.started_at, now + delay, reason)
        job.timer.start()
        return True

    def _retry(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.timer is None:
                return                       # cancelled meanwhile
            job.timer = None
            job.waiting_since = None
            job.attempt += 1
            attempt, function_ref, params = job.attempt, job.function_ref, job.params
            max_wait_s = job.extra.get("max_wait_s", -1.0)
        if self._queue_deadlines is not None:
            # A new attempt waits for a runner like a new job: max_wait counts from now.
            self._queue_deadlines.set(job_id, max_wait_s, self._now_fn())
        if self._on_retry_started is not None:
            self._on_retry_started(job_id, attempt)
        invoked = self._invoke(function_ref, job_id, params)
        if invoked.is_err:
            # It couldn't even be placed: that's this attempt's outcome (and
            # may itself be tried again, as a platform failure).
            assert self._wrapped is not None, "wrap() first"
            self._wrapped(InvocationHandle(job_id=job_id, function_id=function_ref.id,
                                           version=function_ref.version), Err(invoked.unwrap_err()), [])

    # ── cancelling a job that waits for its next attempt ─────────────────────
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.timer is None:
                return False
            job.timer.cancel()
            self._jobs.pop(job_id, None)
            function_ref = job.function_ref
        if self._inner is not None:
            self._inner(InvocationHandle(job_id=job_id, function_id=function_ref.id, version=function_ref.version),
                        Err(JobCancelledError("job was cancelled while waiting for its next attempt",
                                              context={"job_id": job_id})), [])
        return True
