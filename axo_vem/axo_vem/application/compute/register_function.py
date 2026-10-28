from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from axo_shared import wire
from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import Command

from axo_shared.functions.signature import read_signature
from axo_shared.functions.source import FunctionSourceError

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_code_store import FunctionCodeStore
from axo_vem.application.compute.function_secrets import FunctionSecrets, masked
from axo_vem.domain.compute.repository import FunctionRepository
from axo_vem.domain.errors import ConflictError, InvalidRequestError, UpstreamTimeoutError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import send_command


# How the uploaded code is sent: plain source, or a cloudpickle (the axo
# library's fallback when the source can't stand alone).
CODE_FORMATS = {"source": "source", "pickle": "cloudpickle", "cloudpickle": "cloudpickle"}
# "kwargs": a function with named parameters (ctx only if it asks); "object":
# an Axo class (ObjectService). The old f(params, ctx) style is refused.
CALL_STYLES = ("kwargs", "object")
_OLD_STYLE = ("functions written as f(params, ctx) are no longer supported: use named parameters, "
              "for example def resize(width: int, height: int = 100, ctx=None)")


class RegisterFunctionUseCase:
    """Registers a function in this VEM's VE: sends FUNCTION_REGISTER (with
    the code) to one running endpoint, which replicates it to the rest of the
    VE, then keeps the code in the VE's hidden code bucket for Restoring. The
    Control Plane already checked the caller may do this.

    With a ``code_hash`` (the axo client sends one), code already registered
    under that function answers with its existing version -- no new one."""

    def __init__(
        self,
        virtual_environment_id: str,
        endpoint_picker: EndpointPicker,
        command_timeout_seconds: float = 3.0,
        code_store: Optional[FunctionCodeStore] = None,
        function_repository: Optional[FunctionRepository] = None,
        secrets: Optional[FunctionSecrets] = None,
        runner_size: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        """``runner_size``: settles a container function's runner size (the
        plan's default when it asks for none) and refuses one too big."""
        self._runner_size = runner_size
        self._functions = function_repository
        self._secrets = secrets
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
        code_format: str = "source",
        code_hash: Optional[str] = None,
        call_style: Optional[str] = None,
        function_id: Optional[str] = None,
        display_source: Optional[bytes] = None,
        registered_by_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``function_id`` defaults to the caller's own (user, VE, name)
        identity; object classes pass their VE-wide one.

        The run form's fields (``params_schema``) are read from the code
        when not given: from the source, or for a pickle from its readable
        text (``display_source``, shown on the Code tab, never run)."""
        function_id = function_id or compute_function_id(current_user_id, self._virtual_environment_id, name)
        call_style = call_style or (runtime_spec or {}).get("call_style") or "kwargs"
        if call_style not in CALL_STYLES:
            raise InvalidRequestError(_OLD_STYLE if call_style == "params_ctx" else
                                      f"call_style must be one of {', '.join(CALL_STYLES)}",
                                      error_name="FUNCTION_STYLE_UNSUPPORTED" if call_style == "params_ctx" else None)
        readable = code if code_format == "source" else display_source
        description = None
        if readable and call_style == "kwargs":
            try:
                signature = read_signature(readable, name)
            except FunctionSourceError:
                signature = None            # display text that doesn't name it: nothing to read
            if signature is not None:
                if signature.old_style:
                    raise InvalidRequestError(_OLD_STYLE, error_name="FUNCTION_STYLE_UNSUPPORTED")
                description = signature.description
                if params_schema is None:
                    params_schema = signature.params_schema()
        if code_hash and self._functions is not None:
            existing = self._functions.find_by_code_hash(function_id, code_hash)
            if existing is not None:
                return {"endpoint_id": (existing.endpoint_id or [None])[0], "name": name,
                        "function_id": function_id, "version": existing.version, "existing": True}

        runtime_spec = {**(runtime_spec or {}), "call_style": call_style}
        if self._runner_size is not None:
            runtime_spec = self._runner_size(runtime_spec)
        endpoint = self._picker.pick()
        envelope: Dict[str, Any] = {"function_id": function_id, "name": name,
                                    "code_format": CODE_FORMATS[code_format]}
        envelope["runtime_spec"] = runtime_spec
        if params_schema:
            envelope["params_schema"] = params_schema
        if code_hash:
            envelope["code_hash"] = code_hash

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
            meta = command_result.metadata or {}
            raise ConflictError(command_result.error, error_name=meta.get("error_name") or None,
                                error_code=meta.get("error_code") or None)
        metadata = command_result.metadata or {}
        if self._secrets is not None and metadata.get("version") is not None and runtime_spec.get("secret_env"):
            self._secrets.keep(function_id, metadata["version"], runtime_spec)
        # Keep the code so Restoring can bring it back after a stop/start.
        if self._code_store is not None and metadata.get("version") is not None:
            self._code_store.save(function_id, metadata["version"], code)
            if display_source and code_format != "source":
                self._code_store.save_display(function_id, metadata["version"], display_source)
        # Saved straight away too: the endpoint's FunctionRegistered reaches
        # the read model later (projector), and a job submitted right after
        # registering must already find the function. Same fields, so the
        # projector's upsert of that event changes nothing.
        if self._functions is not None and metadata.get("version") is not None:
            self._functions.apply_event_data({
                "function_id": function_id, "version": metadata["version"], "name": name,
                "runtime_spec": masked(runtime_spec), "params_schema": params_schema,
                "code_format": envelope["code_format"], "code_hash": code_hash,
                "endpoint_id": endpoint.endpoint_id, "registered_by": current_user_id,
                "registered_by_name": registered_by_name, "description": description,
                "has_display_source": bool(display_source) and code_format != "source",
            })
        return {"endpoint_id": endpoint.endpoint_id, "name": name, **metadata, "existing": False}
