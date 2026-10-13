from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from option import Err, Result

from axo_endpoint.core.errors import (
    EndpointDrainingError,
    FunctionExecError,
    InvalidFieldError,
    InvocationError,
    MissingFieldError,
)
from axo_endpoint.core.events.bus import Event as BusEvent, EventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.functions.params_validation import validate_and_fill_params
from axo_shared.protocol import Command, CommandHandler, CommandResult
from axo_endpoint.core.results import FunctionResult, is_json_safe_value
from axo_endpoint.core.runtime import OUTPUT_BUCKETS_PARAM, FunctionRuntime, FunctionRuntimeError, InvocationHandle, split_output_buckets
from axo_endpoint.core.runtime.queue_deadlines import QueueDeadlines, WAIT_FOREVER, parse_max_wait
from axo_endpoint.core.storage.backend import StorageBackend, StorageKey
from axo_endpoint.core.usage import JobUsageContext, classify_outcome
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

# Marks a job's result as "not finished yet" while it's still running.
PENDING = "PENDING"

_Logger = Union[Log, DumbLogger]

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def build_completion_recorder(
    results: StorageBackend,
    event_bus: EventBus,
    now_fn: Callable[[], float] = time.time,
    logger: _Logger = None,
    replicate_fn: "Optional[Callable[[FunctionResult], None]]" = None,
) -> Callable[[InvocationHandle, "Result[Any, FunctionRuntimeError]", Optional[List[str]]], None]:
    """Creates a function that saves a job's final result and announces that
    it finished. ``replicate_fn``, if given, is called once after the local
    store/event -- this is the single point every job's completion passes
    through exactly once, regardless of which runtime executed it, so it's
    also the single point cluster-wide result replication hooks in from.
    Left unset (the default), behavior is identical to before this existed.

    Results are JSON only (numbers, text, lists, dicts -- see
    is_json_safe_value): they travel as events to the next function in a
    choreography. Anything else (bytes, a DataFrame, a custom object) fails
    the job as the user's error; unstructured data goes through a bucket
    with dataio.write instead. Function authors never write this envelope
    themselves."""

    _logger: _Logger = logger or DumbLogger()

    def on_complete(
        handle: InvocationHandle,
        outcome: "Result[Any, FunctionRuntimeError]",
        warnings: Optional[List[str]] = None,
    ) -> None:
        """Saves the job's result and emits a JOB_COMPLETED or JOB_FAILED event."""
        now = now_fn()
        duration_ms = round((now - handle.started_at) * 1000, 2) if handle.started_at is not None else None
        warnings = warnings or []
        if outcome.is_ok and not is_json_safe_value(outcome.unwrap()):
            outcome = Err(FunctionExecError(
                f"function returned {type(outcome.unwrap()).__name__}; results must be JSON "
                "(numbers, text, lists, dicts). Write data to a bucket with dataio.write(...) "
                "and return a small JSON summary or reference instead.",
                context={"job_id": handle.job_id, "returned_type": type(outcome.unwrap()).__name__},
            ))
        if outcome.is_ok:
            result = FunctionResult(
                job_id=handle.job_id, ok=True, output={"value": outcome.unwrap(), "type": "json"},
                duration_ms=duration_ms, warnings=warnings,
            )
            event_type = "JOB_COMPLETED"
            billing_outcome = classify_outcome(None)
            _logger.info_event(
                Event.Job.COMPLETED,
                component=Component.HANDLER_JOB_SUBMIT,
                status="ok",
                job_id=handle.job_id,
                function_name=handle.function_id,
            )
        else:
            err_msg = str(outcome.unwrap_err())
            billing_outcome = classify_outcome(outcome.unwrap_err())
            result = FunctionResult(
                job_id=handle.job_id, ok=False, error=err_msg,
                duration_ms=duration_ms, warnings=warnings,
            )
            event_type = "JOB_FAILED"
            _logger.info_event(
                Event.Job.FAILED,
                component=Component.HANDLER_JOB_SUBMIT,
                status="error",
                job_id=handle.job_id,
                function_name=handle.function_id,
                error_message=err_msg,
            )

        results.put(StorageKey(id=handle.job_id), result)
        event_bus.emit(
            BusEvent(
                event_type=event_type,
                payload={
                    "job_id": handle.job_id, "function_id": handle.function_id,
                    "function_version": handle.version, "duration_ms": duration_ms,
                    # For UsageRecorder: completed / failed_user / failed_platform /
                    # cancelled / timed_out_in_queue, and when the attempt ran.
                    "outcome": billing_outcome,
                    "started_at": handle.started_at,
                    "finished_at": now,
                },
                timestamp=now,
            )
        )

        if replicate_fn is not None:
            # Never block the pump/worker thread that called on_complete on
            # network I/O -- replication (incl. its own retries/backoff)
            # runs on its own daemon thread, same reasoning as the cold-start
            # dispatch and leader-reconciliation threads elsewhere.
            threading.Thread(target=replicate_fn, args=(result,), daemon=True).start()

    return on_complete


def submit_job(
    command: Command,
    job_id: str,
    runtime: FunctionRuntime,
    results: StorageBackend,
    event_bus: EventBus,
    function_registry: FunctionRegistry,
    now_fn: Callable[[], float] = time.time,
    logger: _Logger = None,
    queue_deadlines: Optional[QueueDeadlines] = None,
    default_max_wait_s: float = WAIT_FOREVER,
) -> CommandResult:
    """Validates, records, and invokes one job under the given job_id.

    ``max_wait`` (envelope, seconds; else ``default_max_wait_s``) bounds how
    long the job may wait for a free runner -- see QueueDeadlines. A
    forwarded job keeps the origin's value, carried in its "usage" context.
    Shared by JobSubmitHandler (job_id freshly minted) and JobForwardHandler
    (job_id supplied by whoever forwarded the job here) so both go through
    identical validation/PENDING-write/invoke logic -- the only difference
    between a normal submission and a forwarded one is where the job_id
    comes from.

    function_id is the catalog lookup key; function_name (if present) is
    kept only for logging -- names aren't unique across users/workspaces
    once function_id is derived from (user_id, virtual_environment_id,
    name), so name alone can no longer resolve a function."""
    _logger: _Logger = logger or DumbLogger()
    t0              = time.monotonic()
    function_id     = command.envelope.get("function_id")
    name            = command.envelope.get("function_name")
    version         = command.envelope.get("function_version")

    if not function_id or version is None:
        err = MissingFieldError(
            "function_id and function_version are required",
            context={"fields": ["function_id", "function_version"]},
        )
        _logger.info_event(
            Event.Job.SUBMITTED,
            component=Component.HANDLER_JOB_SUBMIT,
            status="error",
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
            **err.to_dict(),
        )
        return CommandResult.from_error(err)

    # Output buckets (FN -> bucket edges) ride in the params under a reserved
    # key; set aside here so validation never sees it -- a JOB_FORWARD
    # brings params that already carry it.
    params, output_buckets = split_output_buckets(command.envelope.get("params", {}))
    output_buckets = list(command.envelope.get("output_buckets") or output_buckets)
    function_ref = StorageKey(id=function_id, version=version, alias=function_id)

    lookup_result = function_registry.get(function_ref)
    record = lookup_result.unwrap() if lookup_result.is_ok else None
    if record is not None and record.params_schema:
        validated = validate_and_fill_params(record.params_schema, params)
        if validated.is_err:
            err = validated.unwrap_err()
            _logger.info_event(
                Event.Job.SUBMITTED,
                component=Component.HANDLER_JOB_SUBMIT,
                status="error",
                function_name=name,
                function_version=version,
                duration_ms=round((time.monotonic() - t0) * 1000, 2),
                **err.to_dict(),
            )
            return CommandResult.from_error(err)
        params = validated.unwrap()
    if output_buckets:
        params = {**params, OUTPUT_BUCKETS_PARAM: [str(b) for b in output_buckets]}

    max_wait_s = parse_max_wait(command.envelope.get("max_wait"), default_max_wait_s)
    if max_wait_s is None:
        err = InvalidFieldError(
            "max_wait must be -1 (wait forever), 0 (don't wait) or a positive number of seconds",
            context={"field": "max_wait", "value": command.envelope.get("max_wait")},
        )
        _logger.info_event(
            Event.Job.SUBMITTED,
            component=Component.HANDLER_JOB_SUBMIT,
            status="error",
            function_name=name,
            function_version=version,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
            **err.to_dict(),
        )
        return CommandResult.from_error(err)

    submitted_at = now_fn()
    usage = JobUsageContext.from_envelope(
        {**command.envelope, "function_id": function_ref.id, "function_version": version}, job_id, submitted_at,
        max_wait_s=max_wait_s,
    )
    results.put(StorageKey(id=job_id), FunctionResult(job_id=job_id, ok=False, error=PENDING))
    event_bus.emit(
        BusEvent(
            event_type="JOB_SUBMITTED",
            payload={
                "job_id": job_id, "function_id": function_ref.id,
                "function_version": version, "params": params,
                "usage": usage.to_dict(),
            },
            timestamp=submitted_at,
        )
    )

    if queue_deadlines is not None:
        queue_deadlines.set(job_id, usage.max_wait_s, usage.received_at)
    invoke_result = runtime.invoke(function_ref, job_id, params)
    if invoke_result.is_err:
        if queue_deadlines is not None:
            queue_deadlines.discard(job_id)
        raw = invoke_result.unwrap_err()
        err = InvocationError(str(raw), context={"job_id": job_id, "function_name": name, "version": version})
        results.put(StorageKey(id=job_id), FunctionResult(job_id=job_id, ok=False, error=err.message))
        _logger.error_event(
            Event.Job.SUBMITTED,
            component        = Component.HANDLER_JOB_SUBMIT,
            status           = "error",
            job_id           = job_id,
            function_name    = name,
            function_version = version,
            duration_ms      = round((time.monotonic() - t0) * 1000, 2),
            **err.to_dict(),
        )
        return CommandResult.from_error(err, metadata={"job_id": job_id})

    duration_ms = round((time.monotonic() - t0) * 1000, 2)
    _logger.info_event(
        Event.Job.SUBMITTED,
        component=Component.HANDLER_JOB_SUBMIT,
        status="ok",
        job_id=job_id,
        function_name=name,
        function_version=version,
        duration_ms=duration_ms,
    )
    return CommandResult(ok=True, metadata={"job_id": job_id, "status": "QUEUED"})


class JobSubmitHandler(CommandHandler):
    """Accepts a job to run and returns a job id right away, before it finishes."""

    def __init__(
        self,
        runtime: FunctionRuntime,
        results: StorageBackend,
        event_bus: EventBus,
        function_registry: FunctionRegistry,
        job_id_fn: Callable[[], str] = lambda: uuid.uuid4().hex,
        now_fn: Callable[[], float] = time.time,
        logger: _Logger = None,
        queue_deadlines: Optional[QueueDeadlines] = None,
        default_max_wait_s: float = WAIT_FOREVER,
        is_draining: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._runtime = runtime
        self._is_draining = is_draining
        self._queue_deadlines = queue_deadlines
        self._default_max_wait_s = default_max_wait_s
        self._results = results
        self._event_bus = event_bus
        self._function_registry = function_registry
        self._job_id_fn = job_id_fn
        self._now_fn = now_fn
        self._logger: _Logger = logger or DumbLogger()

    def handle(self, command: Command) -> CommandResult:
        """Mints a fresh job id and submits it -- unless this endpoint is draining.

        A caller may bring its own ``job_id`` (the Control Plane does, for a
        job it accepted while the VE was asleep, so the id the person already
        got stays the same). Sending the same id twice runs the job once."""
        if self._is_draining is not None and self._is_draining():
            return CommandResult.from_error(EndpointDrainingError("endpoint is draining; send the job elsewhere"))
        requested = command.envelope.get("job_id")
        if requested is not None:
            if not isinstance(requested, str) or not _JOB_ID_RE.match(requested):
                return CommandResult.from_error(InvalidFieldError(
                    "job_id must be 8-64 letters, digits, '-' or '_'", context={"job_id": str(requested)[:80]}))
            existing = self._results.exists(StorageKey(id=requested))
            if existing.is_ok and existing.unwrap():
                return CommandResult(ok=True, metadata={"job_id": requested, "status": "DUPLICATE"})
        return submit_job(
            command,
            requested or self._job_id_fn(),
            self._runtime,
            self._results,
            self._event_bus,
            self._function_registry,
            self._now_fn,
            self._logger,
            self._queue_deadlines,
            self._default_max_wait_s,
        )
