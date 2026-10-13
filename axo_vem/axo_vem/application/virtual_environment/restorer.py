from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_code_store import FunctionCodeStore
from axo_vem.domain.compute.repository import FunctionRepository
from axo_vem.domain.errors import DomainError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import send_command


@dataclass
class RestoreProgress:
    """What the UI shows while the VE is Restoring: "restored 12 / 40 functions"."""

    total: int = 0
    restored: int = 0
    failed: List[Dict[str, Any]] = field(default_factory=list)
    running: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"total": self.total, "restored": self.restored, "failed": list(self.failed), "running": self.running}


class VeRestorer:
    """After a VE starts again its endpoints are empty (catalogs live in
    memory), so every live function is re-registered from the code bucket at
    its exact version. One running endpoint is enough -- endpoints replicate
    functions among themselves. Safe to repeat: a version that's already back
    counts as restored."""

    def __init__(
        self,
        function_repository: FunctionRepository,
        code_store: FunctionCodeStore,
        endpoint_picker: EndpointPicker,
        command_timeout_seconds: float = 10.0,
    ) -> None:
        self._functions = function_repository
        self._code = code_store
        self._picker = endpoint_picker
        self._timeout = command_timeout_seconds
        self._progress = RestoreProgress()
        self._lock = threading.Lock()

    def progress(self) -> RestoreProgress:
        with self._lock:
            return RestoreProgress(**self._progress.to_dict())

    def restore(self) -> RestoreProgress:
        functions = self._functions.list_active()
        with self._lock:
            self._progress = RestoreProgress(total=len(functions), running=True)
        for function in functions:
            error = self._restore_one(function)
            with self._lock:
                if error is None:
                    self._progress.restored += 1
                else:
                    self._progress.failed.append(
                        {"function_id": function.function_id, "version": function.version, "error": error},
                    )
        with self._lock:
            self._progress.running = False
        return self.progress()

    def _restore_one(self, function) -> Optional[str]:
        try:
            code = self._code.load(function.function_id, function.version)
            endpoint = self._picker.pick()
        except DomainError as exc:
            return str(exc)
        envelope: Dict[str, Any] = {
            "function_id": function.function_id,
            "name": function.name or function.function_id,
            "code_format": "source",
            "restore": True,
            "version": function.version,
        }
        if function.runtime_spec is not None:
            envelope["runtime_spec"] = function.runtime_spec
        if function.params_schema:
            envelope["params_schema"] = function.params_schema
        command = Command(
            operation=wire.FUNCTION_REGISTER, content_type="application/octet-stream",
            envelope=envelope, payload=code,
        )
        result = send_command(self._picker.rpc_uri(endpoint), command, self._timeout)
        if result.is_err:
            return str(result.unwrap_err())
        reply = result.unwrap()
        if reply.ok or (reply.metadata or {}).get("error_name") == "FUNCTION_VERSION_EXISTS":
            return None
        return reply.error
