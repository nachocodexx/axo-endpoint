from __future__ import annotations

from typing import Any, Dict, List, Optional

from axo_shared import wire
from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import Command

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_code_store import FunctionCodeStore
from axo_vem.domain.errors import ConflictError, UpstreamTimeoutError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import send_command


class RegisterFunctionUseCase:
    """Registers a function in this VEM's VE: sends FUNCTION_REGISTER (with
    the code) to one running endpoint, which replicates it to the rest of the
    VE, then keeps the code in the VE's hidden code bucket for Restoring. The
    Control Plane already checked the caller may do this."""

    def __init__(
        self,
        virtual_environment_id: str,
        endpoint_picker: EndpointPicker,
        command_timeout_seconds: float = 3.0,
        code_store: Optional[FunctionCodeStore] = None,
    ) -> None:
        self._virtual_environment_id = virtual_environment_id
        self._code_store = code_store
        self._picker = endpoint_picker
        self._command_timeout_seconds = command_timeout_seconds

    def execute(
        self,
        *,
        current_user_id: str,
        name: str,
        code: bytes,
        runtime_spec: Optional[Dict[str, Any]] = None,
        params_schema: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        endpoint = self._picker.pick()
        function_id = compute_function_id(current_user_id, self._virtual_environment_id, name)

        envelope: Dict[str, Any] = {"function_id": function_id, "name": name, "code_format": "source"}
        if runtime_spec is not None:
            envelope["runtime_spec"] = runtime_spec
        if params_schema:
            envelope["params_schema"] = params_schema

        command = Command(
            operation=wire.FUNCTION_REGISTER,
            content_type="application/octet-stream",
            envelope=envelope,
            payload=code,
        )
        result = send_command(self._picker.rpc_uri(endpoint), command, self._command_timeout_seconds)
        if result.is_err:
            raise UpstreamTimeoutError(str(result.unwrap_err()))

        command_result = result.unwrap()
        if not command_result.ok:
            raise ConflictError(command_result.error)
        metadata = command_result.metadata or {}
        # Keep the code so Restoring can bring it back after a stop/start.
        if self._code_store is not None and metadata.get("version") is not None:
            self._code_store.save(function_id, metadata["version"], code)
        return {"endpoint_id": endpoint.endpoint_id, "name": name, **metadata}
