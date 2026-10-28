from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from axo_vem.domain.execution.job import Job, JobStatus
from axo_vem.domain.visibility import Shared


@dataclass(frozen=True)
class JobFilters:
    """What the jobs list can be narrowed to (every one optional)."""

    function_id: Optional[str] = None
    function_version: Optional[int] = None
    statuses: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)   # completed / failed_user / failed_platform / ...
    started_by_type: Optional[str] = None
    started_by_id: Optional[str] = None
    error_name: Optional[str] = None
    job_id: Optional[str] = None                        # the start of a job id
    since: Optional[str] = None                          # ISO times, received
    until: Optional[str] = None
    choreography_id: Optional[str] = None
    run_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    # None = every job; else only those this viewer may see (their own + shared)
    visible_to: Optional[Shared] = None


@dataclass(frozen=True)
class JobRow:
    """One row of the jobs list: a job on its own, or every matching job of
    one choreography run (``run_id`` set)."""

    row_id: str
    row_at: Optional[str]
    jobs: List[Job]
    run_id: Optional[str] = None


class JobRepository(ABC):
    """The jobs read model (Mongo ``jobs``): written by the VEM when it
    submits a job (``submitted``) and by the endpoints' job events
    (``advance``, application/projector/job_handler.py)."""

    @abstractmethod
    def get(self, job_id: str) -> Optional[Job]: ...

    @abstractmethod
    def list_by_function(self, function_id: str, function_version: Optional[int] = None) -> List[Job]: ...

    @abstractmethod
    def kept_results(self, visible_to: Optional[Shared] = None, before: Optional[str] = None,
                     before_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """Jobs whose result is kept, newest saved first, with their count / bytes."""

    @abstractmethod
    def save(self, job: Job) -> None: ...

    def submitted(self, job_id: str, fields: Dict[str, Any]) -> None:
        """What the VEM knows when it submits the job (who started it, its
        settings); never changes the job's state."""
        raise NotImplementedError

    def advance(self, job_id: str, attempt: int, status: JobStatus, fields: Optional[Dict[str, Any]] = None,
                base: Optional[Dict[str, Any]] = None) -> bool:
        """Moves the job to ``status`` for ``attempt`` (with ``fields``) --
        only forward (see domain/execution/job.py). ``base``: written if the
        job isn't known yet. True if it moved."""
        raise NotImplementedError

    def fill(self, job_id: str, fields: Dict[str, Any]) -> None:
        """Sets each field the job doesn't have yet (whatever its state)."""
        raise NotImplementedError

    def update(self, job_id: str, fields: Dict[str, Any]) -> None:
        raise NotImplementedError

    def page(self, filters: JobFilters, before: Optional[str] = None, before_id: Optional[str] = None,
             limit: int = 50) -> List[JobRow]:
        raise NotImplementedError
