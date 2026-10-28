from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.domain.compute.repository import FunctionRepository
from axo_shared.runtime.results import KeepResultError, check_keep_result
from axo_shared.runtime.spec import RuntimeSpecError, check_attempts

from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError, UpstreamTimeoutError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import send_command


class SubmitFunctionJobUseCase:
    """Submits a job to one of this VE's running endpoints -- callers only
    know a function_id/version, never an endpoint. The endpoint cluster
    itself places the job (possibly forwarding it to another endpoint)."""

    def __init__(
        self,
        function_repository: FunctionRepository,
        endpoint_picker: EndpointPicker,
        command_timeout_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        jobs: Any = None,                       # JobsService: the job's record
    ) -> None:
        self._jobs = jobs
        self._function_repository = function_repository
        self._picker = endpoint_picker
        self._command_timeout_seconds = command_timeout_seconds
        self._clock = clock

    def execute(
        self,
        *,
        function_id: str,
        version: int,
        params: Dict[str, Any],
        acting_user_id: Optional[str] = None,
        started_by_type: str = "user",
        started_by_id: Optional[str] = None,
        output_buckets: Optional[List[str]] = None,
        job_id: Optional[str] = None,
        max_wait: Optional[float] = None,
        upstream_waits_ms: Optional[Dict[str, float]] = None,
        event: Any = None,
        step_ref: Optional[Dict[str, Any]] = None,
        attempts: Optional[Dict[str, Any]] = None,
        keep_result: Optional[str] = None,
        started_by_name: Optional[str] = None,
        choreography: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """``acting_user_id`` is the person the job acts for -- axo_storage
        checks that person's bucket access for the job's dataio.
        ``started_by_*`` say what started it (a user, a choreography, ...).
        ``upstream_waits_ms`` = the waits before the VEM (the Control
        Plane's); the VEM adds its own (picking / waking an endpoint), and
        the endpoint records them all on the job's attempt. ``event``: what a
        choreography step receives (``ctx.event``); ``step_ref`` = its run,
        step and version, kept on the billing record (step costs).
        ``attempts`` = the job's own max_attempts / retry_delay_seconds /
        retry_backoff (else the function's); ``keep_result`` = how long its
        result is kept (else the VE's default, never above the VE's longest);
        ``choreography`` = its run (the jobs list groups a run's jobs)."""
        attempt_settings = _attempt_settings(attempts)
        if keep_result is not None:
            try:
                check_keep_result(keep_result)
            except KeepResultError as exc:
                raise InvalidRequestError(str(exc)) from exc
        began = self._clock()
        function = self._function_repository.get(function_id, version)
        if function is None or function.deleted_at is not None:
            raise NotFoundError("function not found")

        endpoint = self._picker.pick()
        waits = {**(upstream_waits_ms or {}), "vem": round(max(0.0, self._clock() - began) * 1000, 3)}
        command = Command(
            operation=wire.JOB_SUBMIT,
            content_type="application/json",
            envelope={
                "function_id": function_id,
                "function_name": None,
                "function_version": version,
                "params": params,
                "acting_user_id": acting_user_id,
                "started_by_type": started_by_type,
                "started_by_id": started_by_id or acting_user_id,
                # FN -> bucket edges: the function sees them as ctx.output_buckets.
                "output_buckets": list(output_buckets or []),
                "layer_waits_ms": waits,
                **({"job_id": job_id} if job_id else {}),
                **({"max_wait": max_wait} if max_wait is not None else {}),
                **({"event": event} if event is not None else {}),
                **(step_ref or {}),
                **attempt_settings,
            },
        )
        result = send_command(self._picker.rpc_uri(endpoint), command, self._command_timeout_seconds)
        if result.is_err:
            raise UpstreamTimeoutError(str(result.unwrap_err()))

        command_result = result.unwrap()
        if not command_result.ok:
            meta = command_result.metadata or {}      # the wire carries the endpoint's error here
            raise ConflictError(command_result.error,
                                error_name=command_result.error_name or meta.get("error_name") or None,
                                error_code=command_result.error_code or meta.get("error_code") or None)
        answer = {"endpoint_id": endpoint.endpoint_id, **(command_result.metadata or {})}
        if self._jobs is not None and answer.get("job_id") and answer.get("status") != "DUPLICATE":
            spec = function.runtime_spec or {}
            self._jobs.submitted(
                answer["job_id"], function_id=function_id, function_version=version, params=params,
                endpoint_id=endpoint.endpoint_id, acting_user_id=acting_user_id, started_by_type=started_by_type,
                started_by_id=started_by_id or acting_user_id, started_by_name=started_by_name,
                max_attempts=int(attempt_settings.get("max_attempts", spec.get("max_attempts", 1))),
                keep_result=keep_result or spec.get("keep_result"), function_name=getattr(function, "name", None),
                choreography=choreography,
            )
        return answer


    def resubmit(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Sends a ready envelope (a person's retry of a job) to one of the
        VE's endpoints; ``{endpoint_id, ...}``."""
        function = self._function_repository.get(envelope["function_id"], envelope.get("function_version"))
        if function is None or function.deleted_at is not None:
            raise NotFoundError("the job's function (version) no longer exists")
        endpoint = self._picker.pick()
        command = Command(operation=wire.JOB_SUBMIT, content_type="application/json",
                          envelope={"function_name": None, **envelope})
        result = send_command(self._picker.rpc_uri(endpoint), command, self._command_timeout_seconds)
        if result.is_err:
            raise UpstreamTimeoutError(str(result.unwrap_err()))
        reply = result.unwrap()
        if not reply.ok:
            meta = reply.metadata or {}
            raise ConflictError(reply.error, error_name=reply.error_name or meta.get("error_name") or None,
                                error_code=reply.error_code or meta.get("error_code") or None)
        return {"endpoint_id": endpoint.endpoint_id, **(reply.metadata or {})}


def _attempt_settings(attempts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The job's own attempt settings, checked (unset ones are left out)."""
    chosen = {k: v for k, v in (attempts or {}).items()
              if k in ("max_attempts", "retry_delay_seconds", "retry_backoff") and v is not None}
    if chosen:
        try:
            check_attempts(int(chosen.get("max_attempts", 1)), float(chosen.get("retry_delay_seconds", 1.0)),
                           str(chosen.get("retry_backoff", "fixed")))
        except (RuntimeSpecError, TypeError, ValueError) as exc:
            raise InvalidRequestError(str(exc)) from exc
    return chosen
