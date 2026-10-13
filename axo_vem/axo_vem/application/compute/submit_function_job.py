from __future__ import annotations

from typing import Any, Dict, List, Optional

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.domain.compute.repository import FunctionRepository
from axo_vem.domain.errors import ConflictError, NotFoundError, UpstreamTimeoutError
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
    ) -> None:
        self._function_repository = function_repository
        self._picker = endpoint_picker
        self._command_timeout_seconds = command_timeout_seconds

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
    ) -> Dict[str, Any]:
        """``acting_user_id`` is the person the job acts for -- axo_storage
        checks that person's bucket access for the job's dataio.
        ``started_by_*`` say what started it (a user, a choreography, ...)."""
        function = self._function_repository.get(function_id, version)
        if function is None or function.deleted_at is not None:
            raise NotFoundError("function not found")

        endpoint = self._picker.pick()
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
                **({"job_id": job_id} if job_id else {}),
                **({"max_wait": max_wait} if max_wait is not None else {}),
            },
        )
        result = send_command(self._picker.rpc_uri(endpoint), command, self._command_timeout_seconds)
        if result.is_err:
            raise UpstreamTimeoutError(str(result.unwrap_err()))

        command_result = result.unwrap()
        if not command_result.ok:
            raise ConflictError(command_result.error)
        return {"endpoint_id": endpoint.endpoint_id, **(command_result.metadata or {})}
