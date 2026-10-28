"""A job = one request to run a function (or an object's method): one id
forever. Each try is an attempt (``job_attempts``), numbered from 1; the
job shows the latest one's state.

The record is written from two sides, in any order: the VEM when it
submits the job (who started it, its settings) and the endpoints' events
(queued / started / waiting to retry / finished). A state change only ever
moves forward -- a later attempt, or a later phase of the same attempt
(``PHASES``) -- so a late or repeated event can't put a job back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from axo_shared.runtime.outcomes import (
    CANCELLED, COMPLETED as OUTCOME_COMPLETED, FAILED_PLATFORM, FAILED_USER, TIMED_OUT_IN_QUEUE,
)


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"      # an attempt failed; the next one starts at next_attempt_at
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.WAITING_RETRY})
FINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
# How far along an attempt is: a change to a lower phase of the same attempt is ignored.
PHASES = {JobStatus.QUEUED: 0, JobStatus.RUNNING: 1, JobStatus.WAITING_RETRY: 2,
          JobStatus.COMPLETED: 3, JobStatus.FAILED: 3, JobStatus.CANCELLED: 3}

# The plain fault labels shown for a failed attempt.
FAULTS = {
    FAILED_USER: "code",                 # your code failed (billed)
    FAILED_PLATFORM: "platform",         # the platform failed (not billed)
    TIMED_OUT_IN_QUEUE: "queue",         # waited too long in the queue (not billed)
    CANCELLED: "cancelled",              # cancelled (billed for the time it ran)
}


def status_for_outcome(outcome: str) -> JobStatus:
    if outcome == OUTCOME_COMPLETED:
        return JobStatus.COMPLETED
    if outcome == CANCELLED:
        return JobStatus.CANCELLED
    return JobStatus.FAILED


@dataclass
class Job:
    """One job's current state (Mongo ``jobs``, keyed by job_id). Times are
    ISO strings (UTC). ``error`` = the last failed attempt's error (name,
    code, message, fault, part, the code's Python type) -- its traceback is
    kept with the attempt only. ``result`` = where the answer is kept
    (``axo/ve-<id>-results``), or why it isn't."""

    job_id: str
    function_id: str
    status: JobStatus
    function_version: Optional[int] = None
    duration_ms: Optional[float] = None
    params: Optional[Dict[str, Any]] = None
    endpoint_id: Optional[str] = None
    created_at: Optional[str] = None
    attempt: int = 1
    max_attempts: int = 1
    next_attempt_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    outcome: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    kind: str = "function"                       # function | object
    function_name: Optional[str] = None
    object_name: Optional[str] = None
    object_method: Optional[str] = None
    acting_user_id: Optional[str] = None
    started_by_type: Optional[str] = None        # user | api_key | choreography | trigger
    started_by_id: Optional[str] = None
    started_by_name: Optional[str] = None
    choreography: Optional[Dict[str, Any]] = None  # {choreography_id, run_id, step, version, run_started_at}
    keep_result: Optional[str] = None
    keep_until: Optional[str] = None             # None with a kept result = never deleted
    result: Optional[Dict[str, Any]] = None
    cancelled_by: Optional[str] = None
    retried_by: Optional[str] = None
    warnings: list = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "Job":
        names = cls.__dataclass_fields__
        data = {k: v for k, v in doc.items() if k in names}
        data["status"] = JobStatus(doc.get("status") or JobStatus.QUEUED.value)
        data.setdefault("function_id", "")
        return cls(**data)
