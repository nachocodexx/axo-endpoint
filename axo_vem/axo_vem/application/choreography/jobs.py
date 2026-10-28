"""How the choreography engine starts and follows a step's job: a function
job (``JOB_SUBMIT``) or an object method call (``ACTIVE_OBJECT_CALL``),
then ``JOB_RESULT`` on the endpoint that answered, ``JOB_CANCEL`` to stop it."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.domain.compute.repository import EndpointRepository
from axo_vem.domain.errors import DomainError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri, send_command

PENDING = "PENDING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"          # the endpoint doesn't know the job (any more): submit it again

_ERROR_NAME = re.compile(r"^([A-Z][A-Z0-9_]{2,}):\s*")


@dataclass
class JobStatus:
    status: str
    output: Any = None
    error: Optional[str] = None
    error_name: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    duration_ms: Optional[float] = None


def split_error(error: Optional[str]) -> tuple:
    """``"NAME: message"`` -> (NAME, message)."""
    if not error:
        return None, error
    match = _ERROR_NAME.match(error)
    return (match.group(1), error[match.end():]) if match else (None, error)


class StepJobs(ABC):
    @abstractmethod
    def submit_function(self, *, function_id: str, version: int, params: Dict[str, Any], event: Dict[str, Any],
                        output_buckets: List[str], job_id: str, acting_user_id: Optional[str],
                        choreography_id: str, max_wait: Optional[float],
                        step_ref: Optional[Dict[str, Any]] = None, attempts: Optional[Dict[str, Any]] = None,
                        keep_result: Optional[str] = None,
                        choreography: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Returns ``{job_id, endpoint_id}``; raises a DomainError if it can't
        start. ``step_ref``: run / step / version, kept on its billing record;
        ``attempts``: the step's max_attempts / retry settings (None = the
        function's); ``choreography``: the run, kept on the job record."""

    @abstractmethod
    def call_object(self, *, name: str, method: Optional[str], params: Dict[str, Any], event: Dict[str, Any],
                    output_buckets: List[str], job_id: str, acting_user_id: Optional[str],
                    choreography_id: str, max_wait: Optional[float],
                    step_ref: Optional[Dict[str, Any]] = None, keep_result: Optional[str] = None,
                    choreography: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...

    @abstractmethod
    def status(self, endpoint_id: str, job_id: str) -> JobStatus: ...

    @abstractmethod
    def cancel(self, endpoint_id: str, job_id: str) -> None: ...


class EndpointStepJobs(StepJobs):
    def __init__(self, submit_function_job, object_service, endpoints: EndpointRepository,
                 timeout_seconds: float = 3.0, job_records: Any = None) -> None:
        self._submit = submit_function_job
        self._records = job_records          # JobsService: a job the endpoint no longer has
        self._objects = object_service
        self._endpoints = endpoints
        self._timeout = timeout_seconds

    def submit_function(self, *, function_id, version, params, event, output_buckets, job_id, acting_user_id,
                        choreography_id, max_wait, step_ref=None, attempts=None, keep_result=None,
                        choreography=None):
        return self._submit.execute(
            function_id=function_id, version=version, params=params, acting_user_id=acting_user_id,
            started_by_type="choreography", started_by_id=choreography_id, output_buckets=output_buckets,
            job_id=job_id, max_wait=max_wait, event=event, step_ref=step_ref, attempts=attempts,
            keep_result=keep_result, started_by_name=(choreography or {}).get("choreography_name"),
            choreography=choreography,
        )

    def call_object(self, *, name, method, params, event, output_buckets, job_id, acting_user_id,
                    choreography_id, max_wait, step_ref=None, keep_result=None, choreography=None):
        return self._objects.call(
            user_id=acting_user_id, name=name, method=method, params=params, job_id=job_id, max_wait=max_wait,
            started_by_type="choreography", started_by_id=choreography_id, event=event,
            output_buckets=output_buckets, step_ref=step_ref, keep_result=keep_result,
            started_by_name=(choreography or {}).get("choreography_name"), choreography=choreography,
        )

    def _rpc(self, endpoint_id: str) -> Optional[str]:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or not endpoint.router_bind:
            return None
        return resolve_rpc_uri(endpoint.router_bind, endpoint_id)

    def status(self, endpoint_id: str, job_id: str) -> JobStatus:
        status = self._from_endpoint(endpoint_id, job_id)
        if status.status == UNKNOWN:
            # It forgot (it keeps a finished job an hour) or is gone: the
            # job's own record knows whether it finished.
            return self._from_record(job_id) or status
        return status

    def _from_record(self, job_id: str) -> Optional[JobStatus]:
        if self._records is None:
            return None
        try:
            job = self._records.detail(job_id)
        except DomainError:
            return None
        if job.get("status") == "COMPLETED":
            try:
                output = self._records.result(job_id)
            except DomainError:
                return None                    # not kept: run it again
            return JobStatus(COMPLETED, output=output, warnings=list(job.get("warnings") or []),
                             duration_ms=job.get("duration_ms"))
        if job.get("status") in ("FAILED", "CANCELLED"):
            error = job.get("error") or {}
            return JobStatus(FAILED, error=error.get("message") or "the job failed",
                             error_name=error.get("name") or ("JOB_CANCELLED" if job["status"] == "CANCELLED" else None),
                             duration_ms=job.get("duration_ms"))
        return None

    def _from_endpoint(self, endpoint_id: str, job_id: str) -> JobStatus:
        rpc = self._rpc(endpoint_id)
        if rpc is None:
            return JobStatus(UNKNOWN, error=f"endpoint {endpoint_id} is gone")
        result = send_command(rpc, Command(operation=wire.JOB_RESULT, content_type="application/json",
                                           envelope={"job_id": job_id}), self._timeout)
        if result.is_err:
            return JobStatus(PENDING)                  # unreachable for now: ask again later
        reply = result.unwrap()
        meta = reply.metadata or {}
        if not reply.ok:
            if (reply.error_name or meta.get("error_name")) == "JOB_NOT_FOUND":
                return JobStatus(UNKNOWN, error=reply.error)
            return JobStatus(PENDING)
        status = meta.get("status")
        if status == PENDING:
            return JobStatus(PENDING)
        output = (meta.get("output") or {}).get("value")
        warnings = list(meta.get("warnings") or [])
        if status == COMPLETED:
            return JobStatus(COMPLETED, output=output, warnings=warnings, duration_ms=meta.get("duration_ms"))
        name, message = split_error(meta.get("error"))
        return JobStatus(FAILED, error=message or "the job failed", error_name=name, warnings=warnings,
                         duration_ms=meta.get("duration_ms"))

    def cancel(self, endpoint_id: str, job_id: str) -> None:
        rpc = self._rpc(endpoint_id)
        if rpc is not None:
            send_command(rpc, Command(operation=wire.JOB_CANCEL, content_type="application/json",
                                      envelope={"job_id": job_id}), self._timeout)
