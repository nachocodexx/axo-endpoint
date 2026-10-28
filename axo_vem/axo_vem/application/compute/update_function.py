from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.domain.compute.repository import EndpointRepository, FunctionRepository
from axo_vem.domain.errors import ConflictError, NotFoundError, UpstreamTimeoutError
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri, send_command


class UpdateFunctionUseCase:
    """Replaces the body of the former
    PATCH /endpoints/{endpoint_id}/functions/{name}/{version} route handler.
    An in-place params_schema/env_vars mutation, no new version, no code
    change -- takes effect lazily, an already-running worker/container
    keeps its old config until it next naturally redeploys. Same
    endpoint-resolution fix as DeleteFunctionUseCase: routes off the
    function's own recorded endpoint_id list, never a caller-picked
    endpoint or a re-derived function_id hash -- tries each recorded
    endpoint in order (FUNCTION_UPDATE is leader-gated cluster-wide, so any
    one live holder is enough), only failing once every candidate is
    exhausted."""

    def __init__(
        self,
        function_repository: FunctionRepository,
        endpoint_repository: EndpointRepository,
        command_timeout_seconds: float = 3.0,
        secrets: Any = None,                       # FunctionSecrets
        runner_size: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._runner_size = runner_size
        self._secrets = secrets
        self._function_repository = function_repository
        self._endpoint_repository = endpoint_repository
        self._command_timeout_seconds = command_timeout_seconds

    def execute(
        self,
        *,
        function_id: str,
        version: int,
        current_user_id: str,
        params_schema: Optional[List[Dict[str, Any]]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        max_concurrency: Optional[int] = None,
        runtime: Optional[Dict[str, Any]] = None,
        env_vars_replace: bool = False,
        secret_env: Optional[List[str]] = None,
        params_schema_replace: bool = False,
    ) -> Dict[str, Any]:
        """The function page's Settings save everything at once (the
        ``*_replace`` flags): the whole params_schema / env_vars, which env
        vars are secret (a secret sent without a value keeps its saved one)
        and ``runtime`` (attempts, keep time, idle time, max concurrency,
        runner size)."""
        function = self._function_repository.get(function_id, version)
        if function is None:
            raise NotFoundError("function version not found")
        if not function.endpoint_id:
            raise ConflictError("function has no recorded endpoint to route this request to")

        if runtime and self._runner_size is not None and ({"cpu_limit", "memory_limit_bytes"} & set(runtime)):
            # Checked (raises if too big); an empty size = the plan's default again.
            settled = self._runner_size({**(function.runtime_spec or {}), **runtime})
            runtime = {**runtime, **{k: settled.get(k) for k in ("cpu_limit", "memory_limit_bytes") if k in runtime}}
        envelope: Dict[str, Any] = {"function_id": function_id, "version": version}
        if params_schema or (params_schema_replace and params_schema is not None):
            envelope["params_schema"] = params_schema
            if params_schema_replace:
                envelope["params_schema_replace"] = True
        if env_vars or env_vars_replace:
            envelope["env_vars"] = env_vars or {}
            if env_vars_replace:
                envelope["env_vars_replace"] = True
        if secret_env is not None:
            envelope["secret_env"] = list(secret_env)
        if runtime:
            envelope["runtime"] = runtime
        if max_concurrency is not None:
            envelope["max_concurrency"] = max_concurrency
        command = Command(operation=wire.FUNCTION_UPDATE, content_type="application/json", envelope=envelope)

        last_error: Optional[str] = None
        for candidate_endpoint_id in function.endpoint_id:
            endpoint = self._endpoint_repository.get(candidate_endpoint_id)
            if endpoint is None or not endpoint.router_bind:
                last_error = f"endpoint {candidate_endpoint_id} is unreachable"
                continue
            rpc_uri = resolve_rpc_uri(endpoint.router_bind, endpoint.endpoint_id)
            result = send_command(rpc_uri, command, self._command_timeout_seconds)
            if result.is_err:
                last_error = str(result.unwrap_err())
                continue

            command_result = result.unwrap()
            if not command_result.ok:
                raise ConflictError(command_result.error)
            if self._secrets is not None and (secret_env is not None or env_vars):
                current = function.runtime_spec or {}
                self._secrets.keep(function_id, version, {
                    "secret_env": secret_env if secret_env is not None else current.get("secret_env") or [],
                    "env_vars": env_vars or {}}, replace=secret_env is not None)
            return {"function_id": function_id, "version": version}

        raise UpstreamTimeoutError(last_error or "no recorded endpoint was reachable")
