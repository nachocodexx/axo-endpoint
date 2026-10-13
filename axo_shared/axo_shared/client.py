from __future__ import annotations


import time
from typing import Any, Dict, List, Optional

import cloudpickle
import zmq

from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import Command, CommandResult
from axo_shared.wire import (
    ACTIVITY_LIST,
    FUNCTION_DELETE,
    FUNCTION_REGISTER,
    FUNCTION_UPDATE,
    JOB_CANCEL,
    JOB_RESULT,
    JOB_SUBMIT,
    PING,
    decode_command_result,
    encode_command,
)

# Data (buckets, objects, chunks) is axo_storage's job now -- see
# axo_shared.axo_storage.AxoStorageClient. Re-exported for older importers.
from axo_shared.axo_storage.client import DEFAULT_CHUNK_BYTES  # noqa: E402,F401


class AxoClientError(Exception):
    pass


class AxoClientTimeout(AxoClientError):
    pass


class AxoEndpointClient:
    """ZMQ DEALER client for a single axo_endpoint node."""

    def __init__(self, address: str, timeout_ms: int = 5000) -> None:
        self._address = address
        self._timeout_ms = timeout_ms
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.DEALER)
        self._sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self._sock.connect(address)

    def close(self) -> None:
        self._sock.close()
        self._ctx.term()

    def __enter__(self) -> "AxoEndpointClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _send(self, command: Command) -> CommandResult:
        self._sock.send_multipart(encode_command(command))
        try:
            frames = self._sock.recv_multipart()
        except zmq.Again:
            raise AxoClientTimeout("no reply received within timeout")
        result = decode_command_result(frames)
        if result.is_err:
            raise AxoClientError(str(result.unwrap_err()))
        return result.unwrap()

    def ping(self) -> CommandResult:
        return self._send(Command(
            operation=PING,
            content_type="application/json",
            envelope={},
            payload=b"",
        ))

    def register_function(
        self,
        user_id: str,
        virtual_environment_id: str,
        name: str,
        fn: Any = None,
        code: Optional[bytes] = None,
        runtime_spec: Optional[Dict[str, Any]] = None,
        params_schema: Optional[List[Dict[str, Any]]] = None,
    ) -> CommandResult:
        """version is no longer caller-supplied -- the node assigns
        next_version = max(existing versions for this function_id) + 1
        internally, returned in the result's metadata["version"].
        function_id is derived from (user_id, virtual_environment_id, name)
        via compute_function_id, the same pure derivation
        axo_vem's register_function endpoint uses -- re-registering
        under the same triple continues that function's version lineage."""
        if fn is not None:
            code = cloudpickle.dumps(fn)
        if code is None:
            raise ValueError("fn or code must be provided")
        function_id = compute_function_id(user_id, virtual_environment_id, name)
        envelope: Dict[str, Any] = {"function_id": function_id, "name": name}
        if runtime_spec:
            envelope["runtime_spec"] = runtime_spec
        if params_schema:
            envelope["params_schema"] = params_schema
        return self._send(Command(
            operation=FUNCTION_REGISTER,
            content_type="application/octet-stream",
            envelope=envelope,
            payload=code,
        ))

    def update_function(
        self,
        user_id: str,
        virtual_environment_id: str,
        name: str,
        version: int,
        params_schema: Optional[List[Dict[str, Any]]] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """In-place metadata mutation -- new params_schema entries are merged
        additively, env_vars are merged into the existing dict. Takes effect
        lazily: a currently-running worker/container keeps its old config
        until it next naturally redeploys."""
        function_id = compute_function_id(user_id, virtual_environment_id, name)
        envelope: Dict[str, Any] = {"function_id": function_id, "version": version}
        if params_schema:
            envelope["params_schema"] = params_schema
        if env_vars:
            envelope["env_vars"] = env_vars
        return self._send(Command(
            operation=FUNCTION_UPDATE,
            content_type="application/json",
            envelope=envelope,
            payload=b"",
        ))

    def delete_function(self, user_id: str, virtual_environment_id: str, name: str, version: int) -> CommandResult:
        function_id = compute_function_id(user_id, virtual_environment_id, name)
        return self._send(Command(
            operation=FUNCTION_DELETE,
            content_type="application/json",
            envelope={"function_id": function_id, "version": version},
            payload=b"",
        ))

    def submit_job(
        self,
        user_id: str,
        virtual_environment_id: str,
        function_name: str,
        version: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        function_id = compute_function_id(user_id, virtual_environment_id, function_name)
        return self._send(Command(
            operation=JOB_SUBMIT,
            content_type="application/json",
            envelope={
                "function_id": function_id,
                "function_name": function_name,
                "function_version": version,
                "params": params or {},
                # The job acts for this user: axo_storage checks their bucket
                # access for the job's dataio.
                "acting_user_id": user_id,
                "started_by_type": "user",
                "started_by_id": user_id,
            },
            payload=b"",
        ))

    def get_job_result(self, job_id: str) -> CommandResult:
        return self._send(Command(
            operation=JOB_RESULT,
            content_type="application/json",
            envelope={"job_id": job_id},
            payload=b"",
        ))

    def cancel_job(self, job_id: str) -> CommandResult:
        """Asks whichever endpoint owns this job to stop it: kill the
        in-flight worker process or dismiss the in-flight container, and
        drop it if it's still only queued. Never leader-proxied -- only the
        endpoint actually running the job can act on it."""
        return self._send(Command(
            operation=JOB_CANCEL,
            content_type="application/json",
            envelope={"job_id": job_id},
            payload=b"",
        ))

    def list_activity(self, function_id: Optional[str] = None, limit: int = 100) -> CommandResult:
        """Reads back recorded job/container activity from whichever node
        this client is connected to -- purely that node's own local view."""
        envelope: Dict[str, Any] = {"limit": limit}
        if function_id is not None:
            envelope["function_id"] = function_id
        return self._send(Command(operation=ACTIVITY_LIST, content_type="application/json", envelope=envelope))

    def run(
        self,
        user_id: str,
        virtual_environment_id: str,
        function_name: str,
        version: int,
        params: Optional[Dict[str, Any]] = None,
        poll_interval: float = 0.5,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Submit a job and block until it completes or times out."""
        submit = self.submit_job(user_id, virtual_environment_id, function_name, version, params)
        if not submit.ok:
            return submit
        job_id = submit.metadata["job_id"]
        deadline = time.monotonic() + timeout
        while True:
            poll = self.get_job_result(job_id)
            if poll.ok and poll.metadata.get("status") != "PENDING":
                return poll
            if time.monotonic() > deadline:
                raise AxoClientTimeout(
                    f"job {job_id} did not complete within {timeout}s"
                )
            time.sleep(poll_interval)
