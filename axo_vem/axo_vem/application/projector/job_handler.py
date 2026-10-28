from __future__ import annotations

from typing import Any, Dict, Optional

from axo_vem.domain.events import models
from axo_vem.domain.execution.job import FAULTS, JobStatus
from axo_vem.domain.execution.repository import JobRepository
from axo_shared.runtime.outcomes import CANCELLED, outcome_of

JOBS_TOPIC = "jobs"           # every job change; "jobs:<job_id>" for one job

JOB_EVENT_TYPES = frozenset({
    models.JOB_QUEUED, models.JOB_STARTED, models.JOB_COMPLETED, models.JOB_FAILED,
})


def broadcast_job(broadcaster: Any, repository: JobRepository, job_id: str) -> None:
    """Sends the job as it is now to the jobs feeds (no traceback: jobs
    rows never carry one)."""
    if broadcaster is None:
        return
    job = repository.get(job_id)
    if job is None:
        return
    message = {"type": "job", **job.to_dict()}
    broadcaster.broadcast(JOBS_TOPIC, message)
    broadcaster.broadcast(f"{JOBS_TOPIC}:{job_id}", message)


def apply(repository: JobRepository, event_type: str, data: Dict[str, Any], broadcaster: Any = None) -> None:
    """An endpoint's job event moves the job forward (never back: an event
    for an earlier attempt, or replayed, changes nothing). Any of them can
    arrive first; ``base`` fills a job not known yet."""
    job_id = data["job_id"]
    attempt = int(data.get("attempt") or 1)
    at = data.get("created_at")
    base = {"function_id": data["function_id"], "function_version": data.get("version"), "created_at": at,
            "endpoint_id": data.get("endpoint_id")}

    if event_type == models.JOB_QUEUED:
        repository.advance(job_id, attempt, JobStatus.QUEUED, {}, base=base)
        # Whatever came first: the job keeps what it was asked with.
        repository.fill(job_id, {"params": data.get("params"), "function_version": data.get("version"),
                                 "created_at": at, "row_id": job_id, "row_at": at})
    elif event_type == models.JOB_STARTED:
        repository.advance(job_id, attempt, JobStatus.RUNNING, {
            "started_at": at, "endpoint_id": data.get("endpoint_id"), "next_attempt_at": None,
            **({"function_version": data["version"]} if data.get("version") is not None else {}),
        }, base=base)
    elif event_type == models.JOB_COMPLETED:
        repository.advance(job_id, attempt, JobStatus.COMPLETED, {
            "finished_at": at, "duration_ms": data.get("duration_ms"), "outcome": "completed",
            "error": None, "next_attempt_at": None, "endpoint_id": data.get("endpoint_id"),
        }, base=base)
    elif event_type == models.JOB_FAILED:
        failure = data.get("failure") or {}
        outcome = outcome_of(failure.get("error_code"))
        repository.advance(job_id, attempt, JobStatus.CANCELLED if outcome == CANCELLED else JobStatus.FAILED, {
            "finished_at": at, "duration_ms": data.get("duration_ms"), "outcome": outcome,
            "error": error_summary(failure, outcome), "next_attempt_at": None,
        }, base=base)
    broadcast_job(broadcaster, repository, job_id)


def error_summary(failure: Dict[str, Any], outcome: str) -> Optional[Dict[str, Any]]:
    """What the job (and its list row) shows of an error -- the traceback
    stays with the attempt."""
    if not failure:
        return None
    return {
        "name": failure.get("error_class"), "code": failure.get("error_code"), "message": failure.get("message"),
        "part": failure.get("component"), "error_type": failure.get("error_type"), "fault": FAULTS.get(outcome),
    }
