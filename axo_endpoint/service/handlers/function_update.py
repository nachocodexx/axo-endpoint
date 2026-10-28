from __future__ import annotations

import time
from typing import Callable, Union

from axo_endpoint.core.errors import MissingFieldError, StorageFailureError
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.storage.backend import StorageKey
from axo_shared.functions.params_schema import parse_params_schema
from axo_shared.protocol import Command, CommandHandler, CommandResult
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


class FunctionUpdateHandler(CommandHandler):
    """In-place mutation of an existing function version's params_schema/
    env_vars / settings (``runtime``: attempts, keep time, idle time, max
    concurrency; ``secret_env``: names never shown again) -- no new version
    created, no code touched. Takes effect
    lazily on whatever worker/container next naturally redeploys."""

    def __init__(
        self,
        registry: FunctionRegistry,
        now_fn: Callable[[], float] = time.time,
        logger: _Logger = None,
    ) -> None:
        self._registry = registry
        self._now_fn = now_fn
        self._logger: _Logger = logger or DumbLogger()

    def handle(self, command: Command) -> CommandResult:
        """Reads function_id, version, and the fields to merge from the
        request, and applies the update."""
        t0 = time.monotonic()
        function_id = command.envelope.get("function_id")
        version = command.envelope.get("version")

        if not function_id or version is None:
            err = MissingFieldError(
                "function_id and version are required",
                context={"fields": ["function_id", "version"]},
            )
            self._logger.info_event(
                Event.Function.UPDATED,
                component=Component.HANDLER_FUNCTION_UPDATE,
                status="error",
                duration_ms=round((time.monotonic() - t0) * 1000, 2),
                **err.to_dict(),
            )
            return CommandResult.from_error(err)

        replace_params = bool(command.envelope.get("params_schema_replace"))
        raw_schema = command.envelope.get("params_schema")
        params_schema = parse_params_schema(raw_schema) if raw_schema is not None else None
        if not replace_params:
            params_schema = params_schema or None
        replace_env = bool(command.envelope.get("env_vars_replace"))
        env_vars = command.envelope.get("env_vars") or None
        secret_env = command.envelope.get("secret_env")
        runtime = command.envelope.get("runtime") or None
        max_concurrency = command.envelope.get("max_concurrency")
        if max_concurrency is not None and (not isinstance(max_concurrency, int) or max_concurrency < 1):
            return CommandResult.from_error(MissingFieldError(
                "max_concurrency must be a whole number ≥ 1", context={"fields": ["max_concurrency"]},
            ))

        key = StorageKey(id=function_id, version=version, alias=function_id)
        result = self._registry.update(
            key, now=self._now_fn(), params_schema=params_schema, env_vars=env_vars,
            max_concurrency=max_concurrency, runtime=runtime, replace_env=replace_env,
            secret_env=list(secret_env) if secret_env is not None else None, replace_params=replace_params,
        )
        if result.is_err:
            raw = result.unwrap_err()
            err = StorageFailureError(str(raw), context={"function_id": function_id, "version": version})
            self._logger.info_event(
                Event.Function.UPDATED,
                component=Component.HANDLER_FUNCTION_UPDATE,
                status="error",
                function_id=function_id,
                function_version=version,
                duration_ms=round((time.monotonic() - t0) * 1000, 2),
                **err.to_dict(),
            )
            return CommandResult.from_error(err)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        self._logger.info_event(
            Event.Function.UPDATED,
            component=Component.HANDLER_FUNCTION_UPDATE,
            status="ok",
            function_id=function_id,
            function_version=version,
            duration_ms=duration_ms,
        )
        return CommandResult(ok=True, metadata={"function_id": function_id, "version": version})
