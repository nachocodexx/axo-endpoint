"""The VE's jobs as people see them: one record per job (the read model,
domain/execution/job.py), one per attempt, and the job's result kept in
the results bucket for as long as the job (or the VE) says.

- ``submitted``: what the VEM knows when it submits a job (who started
  it, its settings, its choreography run) -- the endpoint's events fill in
  the rest (application/projector/job_handler.py);
- ``on_attempt``: an endpoint's JobAttemptRecorded -- kept here whole
  (traceback included, shown to Developers and above), sent on to the
  Control Plane without the traceback; a job that will be tried again is
  marked waiting; a successful last attempt has its result fetched from
  the endpoint and saved;
- ``cancel`` / ``retry``: a person's request (Developer+, checked by the
  Control Plane), recorded in the Activity list;
- ``sweep``: deletes results past their keep time.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from axo_shared import wire
from axo_shared.protocol import Command
from axo_shared.runtime.results import NEVER, capped, keep_seconds
from axo_shared.runtime.spec import PLATFORM_MAX_ATTEMPTS

from axo_vem.application.jobs.results import JobResultStore, ResultNotSaved
from axo_vem.application.projector.job_handler import broadcast_job, error_summary
from axo_vem.domain.errors import ConflictError, DomainError, NotFoundError, UpstreamTimeoutError
from axo_vem.domain.events import models
from axo_vem.domain.execution.job import Job, JobStatus, status_for_outcome
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri, send_command

# A result fetch the endpoint couldn't answer yet is tried again this often, this many times.
FETCH_ATTEMPTS = 5
FETCH_BACKOFF_SECONDS = 1.0


def iso(at: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if at is None else at, tz=timezone.utc).isoformat()


def _epoch(raw: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def without_traceback(record: Dict[str, Any]) -> Dict[str, Any]:
    failure = record.get("failure")
    if not failure:
        return record
    return {**record, "failure": {**failure, "traceback": None}}


class JobsService:
    def __init__(
        self,
        jobs: Any,                                    # MongoJobRepository
        attempts: Any,                                # MongoJobAttemptRepository
        results: Optional[JobResultStore],
        endpoints: Any,                               # EndpointRepository (router_bind)
        settings_fn: Callable[[], Any],               # -> VeSettings
        event_log: Any = None,                        # append_to_stream(stream, type, data)
        broadcaster: Any = None,
        forward_attempt: Optional[Callable[[Dict[str, Any]], bool]] = None,   # -> the Control Plane
        virtual_environment_id: str = "",
        command_timeout_seconds: float = 3.0,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        workers: int = 4,
        logger: Any = None,
    ) -> None:
        self._jobs = jobs
        self._attempts = attempts
        self._results = results
        self._endpoints = endpoints
        self._settings_fn = settings_fn
        self._event_log = event_log
        self._broadcaster = broadcaster
        self._forward_attempt = forward_attempt
        self._ve_id = virtual_environment_id
        self._timeout = command_timeout_seconds
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        self._logger = logger
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="job-results")
        self._lock = threading.Lock()
        self._saving: set = set()

    # ── what the VEM submits ─────────────────────────────────────────────────
    def submitted(self, job_id: str, *, function_id: str, function_version: Optional[int], params: Dict[str, Any],
                  endpoint_id: Optional[str], acting_user_id: Optional[str], started_by_type: str,
                  started_by_id: Optional[str], started_by_name: Optional[str] = None,
                  max_attempts: int = 1, keep_result: Optional[str] = None, kind: str = "function",
                  function_name: Optional[str] = None, object_name: Optional[str] = None,
                  object_method: Optional[str] = None, choreography: Optional[Dict[str, Any]] = None) -> None:
        at = iso(self._now_fn())
        # a called run's jobs go in the row of the outermost run
        run_id = (choreography or {}).get("root_run_id") or (choreography or {}).get("run_id")
        self._jobs.submitted(job_id, {
            "function_id": function_id, "function_version": function_version, "params": params,
            "submitted_to": endpoint_id, "acting_user_id": acting_user_id, "started_by_type": started_by_type,
            "started_by_id": started_by_id, "started_by_name": started_by_name, "max_attempts": max_attempts,
            "keep_result": keep_result, "kind": kind, "function_name": function_name, "object_name": object_name,
            "object_method": object_method, "choreography": choreography, "created_at": at,
            "row_id": f"run:{run_id}" if run_id else job_id,
            "row_at": ((choreography or {}).get("run_started_at") or at) if run_id else at,
        })
        self._jobs.fill(job_id, {"endpoint_id": endpoint_id})
        broadcast_job(self._broadcaster, self._jobs, job_id)

    # ── an endpoint's attempt record ─────────────────────────────────────────
    def on_attempt(self, record: Dict[str, Any]) -> bool:
        """Ingestion acks the endpoint only when this returns True."""
        job_id = record["job_id"]
        attempt = int(record.get("attempt") or 1)
        self._attempts.save(record)
        outcome = record.get("outcome")
        failure = record.get("failure") or {}
        if record.get("retry_at"):
            self._jobs.advance(job_id, attempt, JobStatus.WAITING_RETRY, {
                "next_attempt_at": record["retry_at"], "outcome": outcome,
                "error": error_summary(failure, outcome), "endpoint_id": record.get("endpoint_id"),
            }, base={"function_id": record.get("function_id"), "function_version": record.get("function_version")})
        else:
            # The last attempt: its outcome, error and run time (the event may not be in yet).
            self._jobs.advance(job_id, attempt, status_for_outcome(outcome), {
                "outcome": outcome, "error": error_summary(failure, outcome),
                "finished_at": record.get("finished_at"), "next_attempt_at": None,
                "endpoint_id": record.get("endpoint_id"),
                **({"duration_ms": record["execution_ms"]} if record.get("execution_ms") is not None else {}),
            }, base={"function_id": record.get("function_id"), "function_version": record.get("function_version"),
                     "created_at": record.get("received_at")})
            if outcome == "completed":
                self._save_result_later(job_id, record.get("endpoint_id"), record.get("finished_at"))
        broadcast_job(self._broadcaster, self._jobs, job_id)
        if self._forward_attempt is None:
            return True
        return self._forward_attempt(without_traceback(record))

    # ── keeping the result ───────────────────────────────────────────────────
    def keep_choice(self, job: Job) -> str:
        settings = self._settings_fn()
        longest = getattr(settings, "result_keep_max", NEVER)
        return capped(job.keep_result or getattr(settings, "result_keep_default", "7d"), longest)

    def _save_result_later(self, job_id: str, endpoint_id: Optional[str], finished_at: Optional[str]) -> None:
        if self._results is None or not endpoint_id:
            return
        with self._lock:
            if job_id in self._saving:
                return
            self._saving.add(job_id)
        self._pool.submit(self._save_result, job_id, endpoint_id, finished_at)

    def _save_result(self, job_id: str, endpoint_id: str, finished_at: Optional[str]) -> None:
        try:
            value = self._fetch_result(job_id, endpoint_id)
            if value is None:
                self._jobs.update(job_id, {"result": {"stored": False, "reason": "the endpoint no longer has it"}})
                return
            job = self._jobs.get(job_id)
            choice = self.keep_choice(job) if job is not None else "7d"
            seconds = keep_seconds(choice)
            finished = _epoch(finished_at) or self._now_fn()
            try:
                saved = self._results.put(job_id, value["value"])
            except ResultNotSaved as exc:
                self._jobs.update(job_id, {"result": {"stored": False, "reason": f"not saved: {exc}"},
                                           "warnings": value.get("warnings") or []})
                return
            self._jobs.update(job_id, {
                "result": {"stored": True, **saved, "saved_at": iso(self._now_fn()), "deleted_at": None},
                "keep_result": choice,
                "keep_until": None if seconds is None else iso(finished + seconds),
                "warnings": value.get("warnings") or [],
            })
        except Exception as exc:  # noqa: BLE001 - a background save must never die silently
            if self._logger is not None:
                self._logger.warning(f"keeping the result of job {job_id} failed: {exc}")
        finally:
            with self._lock:
                self._saving.discard(job_id)
            broadcast_job(self._broadcaster, self._jobs, job_id)

    def _fetch_result(self, job_id: str, endpoint_id: str) -> Optional[Dict[str, Any]]:
        """``{"value", "warnings"}`` from the endpoint that ran it, or None
        when it doesn't have it (any more)."""
        for attempt in range(FETCH_ATTEMPTS):
            rpc = self._rpc(endpoint_id)
            if rpc is None:
                return None
            reply = send_command(rpc, Command(operation=wire.JOB_RESULT, content_type="application/json",
                                              envelope={"job_id": job_id}), self._timeout)
            if reply.is_ok:
                answer = reply.unwrap()
                meta = answer.metadata or {}
                if not answer.ok:
                    return None
                if meta.get("status") == "COMPLETED":
                    return {"value": (meta.get("output") or {}).get("value"), "warnings": meta.get("warnings")}
            self._sleep_fn(FETCH_BACKOFF_SECONDS * (attempt + 1))
        return None

    def result(self, job_id: str) -> Any:
        job = self._job(job_id)
        kept = job.result or {}
        if job.status == JobStatus.COMPLETED and not kept and job.endpoint_id:
            # Just finished, still being saved: the endpoint that ran it has it.
            fetched = self._fetch_result(job_id, job.endpoint_id)
            if fetched is not None:
                return fetched["value"]
        if job.status != JobStatus.COMPLETED or not kept.get("stored") or kept.get("deleted_at"):
            raise NotFoundError("the job's result is not kept", error_name="JOB_RESULT_NOT_KEPT")
        if self._results is None:
            raise NotFoundError("results are not kept in this VE", error_name="JOB_RESULT_NOT_KEPT")
        return self._results.get(job_id)

    def kept(self, job_id: str) -> bool:
        """Its result can still be read."""
        job = self._jobs.get(job_id)
        kept = (job.result or {}) if job is not None else {}
        return bool(job is not None and job.status == JobStatus.COMPLETED and kept.get("stored")
                    and not kept.get("deleted_at"))

    def result_ref(self, job_id: str, wait_seconds: float = 30.0) -> Optional[Dict[str, Any]]:
        """The file its result is kept in (bucket, name, version, format), for
        a choreography step's event -- waits a little for a save in progress.
        None when it isn't kept in a bucket."""
        deadline = self._now_fn() + wait_seconds
        while True:
            job = self._jobs.get(job_id)
            kept = (job.result or {}) if job is not None else {}
            if kept.get("stored") and kept.get("bucket"):
                return {"bucket": kept["bucket"], "name": kept["name"], "version": kept.get("version"),
                        "format": "json"}
            if (job is not None and kept and not kept.get("stored")) or self._now_fn() >= deadline:
                return None
            self._sleep_fn(0.2)

    # ── reading ──────────────────────────────────────────────────────────────
    def detail(self, job_id: str, with_traceback: bool = False) -> Dict[str, Any]:
        job = self._job(job_id)
        attempts = self._attempts.list(job_id)
        if not with_traceback:
            attempts = [without_traceback(a) for a in attempts]
        return {**job.to_dict(), "attempts": attempts}

    def _job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("job not found", error_name="VEM_NOT_FOUND")
        return job

    # ── a person's requests ──────────────────────────────────────────────────
    def cancel(self, job_id: str, user_id: str) -> Dict[str, Any]:
        job = self._job(job_id)
        if not job.active:
            raise ConflictError("the job already finished", error_name="JOB_NOT_ACTIVE")
        sent = False
        for endpoint_id in dict.fromkeys([job.endpoint_id, job.to_dict().get("submitted_to")]):
            rpc = self._rpc(endpoint_id) if endpoint_id else None
            if rpc is None:
                continue
            reply = send_command(rpc, Command(operation=wire.JOB_CANCEL, content_type="application/json",
                                              envelope={"job_id": job_id, "cancelled_by": user_id}), self._timeout)
            if reply.is_ok and reply.unwrap().ok:
                sent = True
                break
        if not sent:
            raise UpstreamTimeoutError("no endpoint could stop the job right now; try again")
        self._jobs.update(job_id, {"cancelled_by": user_id})
        self._record(models.JOB_CANCEL_REQUESTED, models.JobCancelRequested(
            job_id=job_id, function_id=job.function_id, requested_by=user_id, attempt=job.attempt,
            virtual_environment_id=self._ve_id or None))
        broadcast_job(self._broadcaster, self._jobs, job_id)
        return {"job_id": job_id, "cancelled": True}

    def retry(self, job_id: str, user_id: str, submit: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
        """A new attempt of the same job, with the same params -- even past
        its max_attempts. ``submit(envelope)`` sends JOB_SUBMIT to an endpoint
        and returns ``{endpoint_id, ...}``."""
        job = self._job(job_id)
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise ConflictError("only a failed or cancelled job can be tried again", error_name="JOB_NOT_RETRYABLE")
        if job.kind != "function" or job.choreography:
            raise ConflictError("a choreography step or an object call can't be tried again on its own",
                                error_name="JOB_NOT_RETRYABLE")
        attempt = max(job.attempt, self._attempts.last_number(job_id) or 0) + 1
        answer = submit({
            "job_id": job_id, "function_id": job.function_id, "function_version": job.function_version,
            "params": job.params or {}, "acting_user_id": job.acting_user_id or user_id,
            "started_by_type": job.started_by_type or "user", "started_by_id": job.started_by_id or user_id,
            "retried_by": user_id, "attempt": attempt,
        })
        self._jobs.advance(job_id, attempt, JobStatus.QUEUED, {
            "retried_by": user_id, "cancelled_by": None, "error": None, "outcome": None, "finished_at": None,
            "endpoint_id": answer.get("endpoint_id"),
        })
        self._record(models.JOB_RETRY_REQUESTED, models.JobRetryRequested(
            job_id=job_id, function_id=job.function_id, requested_by=user_id, attempt=attempt,
            virtual_environment_id=self._ve_id or None))
        broadcast_job(self._broadcaster, self._jobs, job_id)
        return {"job_id": job_id, "attempt": attempt, "endpoint_id": answer.get("endpoint_id")}

    # ── an endpoint lost with jobs on it ─────────────────────────────────────
    def rescue_lost(self, lost_endpoint_ids, submit: Callable[[Dict[str, Any]], Dict[str, Any]]) -> int:
        """Jobs whose endpoint went away (stopped / unreachable) mid-run: a
        platform failure, tried again on another endpoint for free (same id,
        next attempt) up to PLATFORM_MAX_ATTEMPTS, else failed (not billed).
        Choreography steps are left to their run (it sends them again) and
        object calls to the new leader. How many were handled."""
        lost = set(lost_endpoint_ids)
        if not lost:
            return 0
        handled = 0
        for job in self._jobs.active():
            if job.endpoint_id not in lost or job.kind != "function" or job.choreography:
                continue
            attempt = max(job.attempt, self._attempts.last_number(job.job_id) or 0)
            now = iso(self._now_fn())
            failure = {"error_class": "ENDPOINT_LOST", "error_code": 9629, "component": "axo_vem.jobs",
                       "message": f"endpoint {job.endpoint_id} went away while the job was {job.status.value.lower()}"}
            self._attempts.save({
                "job_id": job.job_id, "attempt": attempt, "function_id": job.function_id,
                "function_version": job.function_version, "endpoint_id": job.endpoint_id,
                "outcome": "failed_platform", "failure": failure, "finished_at": now,
                "received_at": job.created_at, "reason": "first" if attempt == 1 else "platform_failed",
            })
            if attempt < PLATFORM_MAX_ATTEMPTS:
                try:
                    answer = submit({
                        "job_id": job.job_id, "function_id": job.function_id,
                        "function_version": job.function_version, "params": job.params or {},
                        "acting_user_id": job.acting_user_id, "started_by_type": job.started_by_type or "user",
                        "started_by_id": job.started_by_id, "attempt": attempt + 1, "reason": "platform_failed",
                        "max_attempts": max(job.max_attempts, attempt + 1),
                        **({"keep_result": job.keep_result} if job.keep_result else {}),
                    })
                except DomainError:
                    continue                           # no endpoint right now: next time
                self._jobs.advance(job.job_id, attempt + 1, JobStatus.QUEUED, {
                    "endpoint_id": answer.get("endpoint_id"), "error": error_summary(failure, "failed_platform"),
                    "outcome": None, "finished_at": None})
            else:
                self._jobs.advance(job.job_id, attempt, JobStatus.FAILED, {
                    "outcome": "failed_platform", "error": error_summary(failure, "failed_platform"),
                    "finished_at": now, "next_attempt_at": None})
            broadcast_job(self._broadcaster, self._jobs, job.job_id)
            handled += 1
        return handled

    # ── deleting results past their keep time ────────────────────────────────
    def sweep(self) -> int:
        """Deletes results whose keep time ended, and shortens those kept
        longer than the VE's longest time allows now. How many deleted."""
        settings = self._settings_fn()
        longest = keep_seconds(getattr(settings, "result_keep_max", NEVER))
        if longest is not None:
            for job in self._jobs.kept_beyond(longest):
                finished = _epoch(job.finished_at)
                if finished is not None:
                    self._jobs.update(job.job_id, {"keep_until": iso(finished + longest),
                                                   "keep_result": getattr(settings, "result_keep_max")})
        deleted = 0
        for job in self._jobs.results_due(iso(self._now_fn())):
            try:
                if self._results is not None:
                    self._results.delete(job.job_id)
            except DomainError:
                continue
            self._jobs.update(job.job_id, {"result": {**(job.result or {}), "deleted_at": iso(self._now_fn())}})
            deleted += 1
        return deleted

    def shortened_by(self, new_longest: str) -> int:
        """How many kept results lowering the VE's longest time to
        ``new_longest`` would shorten (the settings page warns first)."""
        seconds = keep_seconds(new_longest)
        return 0 if seconds is None else len(self._jobs.kept_beyond(seconds, limit=100000))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _rpc(self, endpoint_id: Optional[str]) -> Optional[str]:
        if not endpoint_id:
            return None
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or not getattr(endpoint, "router_bind", None):
            return None
        return resolve_rpc_uri(endpoint.router_bind, endpoint_id)

    def _record(self, event_type: str, event: Any) -> None:
        if self._event_log is None:
            return
        try:
            self._event_log.append_to_stream(f"ve-jobs-{self._ve_id or 've'}", event_type, event.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - the history is best effort; the job record is the truth
            if self._logger is not None:
                self._logger.warning(f"could not record {event_type}")

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

