"""Runs active object operations on the leader, one at a time per object.

An operation (create / call a method / upgrade) is a job of the object's
class: the coordinator loads the saved state, puts it in the job's params
(small values in full, the names of big ones), submits the job like any
other, and when it finishes saves what the call changed -- only if it
succeeded and the object is still at the revision it read. A failed call
saves nothing. The next queued operation on the object starts afterwards.

The per-object queue lives in the leader's memory; if the leader changes
mid-call, the conditional save on the new state store still stops both
from writing (the late one fails with ACTIVE_OBJECT_STATE_CONFLICT).
"""

from __future__ import annotations

import base64
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

from option import Err, Ok, Result

from axo_shared.active_objects.models import AttributeEntry, StateManifest
from axo_shared.active_objects.state import ObjectState, StatePolicy, StateStore
from axo_shared.errors import (
    ActiveObjectExistsError,
    ActiveObjectNotFoundError,
    ActiveObjectStateConflictError,
    ActiveObjectStateUnreadableError,
    AxoError,
)
from axo_shared.protocol import Command, CommandResult
from axo_shared import wire

from axo_endpoint.core.errors import FunctionExecError, InvalidFieldError, MissingFieldError
from axo_endpoint.core.events.bus import Event as BusEvent, EventBus
from axo_endpoint.core.results import FunctionResult, is_json_safe_value
from axo_endpoint.core.runtime import InvocationHandle
from axo_endpoint.core.runtime.entry import OBJECT_OUTCOME, OBJECT_PARAM
from axo_endpoint.core.storage.backend import StorageBackend, StorageKey
from axo_endpoint.core.usage import classify_outcome
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]

ACTIVE_OBJECT_CREATED_EVENT = "ACTIVE_OBJECT_CREATED_EVENT"
ACTIVE_OBJECT_CALLED_EVENT = "ACTIVE_OBJECT_CALLED_EVENT"
ACTIVE_OBJECT_UPGRADED_EVENT = "ACTIVE_OBJECT_UPGRADED_EVENT"
ACTIVE_OBJECT_DELETED_EVENT = "ACTIVE_OBJECT_DELETED_EVENT"
ACTIVE_OBJECT_SIZES_EVENT = "ACTIVE_OBJECT_STATE_SIZES_EVENT"

OPS = ("create", "call", "upgrade")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PENDING = "PENDING"

# Submits one job (JobSubmitHandler's submit_job with this endpoint's runtime).
SubmitFn = Callable[[Command, str], CommandResult]
OnComplete = Callable[[InvocationHandle, "Result[Any, AxoError]", Optional[List[str]]], None]


@dataclass
class ObjectOperation:
    op: str
    object_id: str
    name: str
    function_id: str              # the class's registration on this endpoint
    class_name: str
    class_version: int
    job_id: str
    envelope: Dict[str, Any]
    method: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    args: List[Any] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    from_version: Optional[int] = None
    keep_revisions: Optional[int] = None

    @property
    def label(self) -> str:
        """The method name billing and history show."""
        if self.op == "call":
            return self.method or "run"
        return f"__{self.op}__"


@dataclass
class _Running:
    op: ObjectOperation
    state: ObjectState


def parse_operation(envelope: Dict[str, Any], job_id: str) -> Result[ObjectOperation, AxoError]:
    missing = [f for f in ("op", "object_id", "name", "function_id", "class_version") if envelope.get(f) in (None, "")]
    if missing:
        return Err(MissingFieldError(f"missing {', '.join(missing)}", context={"fields": missing}))
    if envelope["op"] not in OPS:
        return Err(InvalidFieldError(f"op must be one of {', '.join(OPS)}", context={"field": "op"}))
    if envelope["op"] == "upgrade" and envelope.get("from_version") is None:
        return Err(MissingFieldError("an upgrade needs from_version", context={"fields": ["from_version"]}))
    for name, kind in (("params", dict), ("args", list), ("kwargs", dict)):
        value = envelope.get(name)
        if value is not None and (not isinstance(value, kind) or not is_json_safe_value(value)):
            return Err(InvalidFieldError(f"{name} must be JSON ({kind.__name__})", context={"field": name}))
    return Ok(ObjectOperation(
        op=envelope["op"], object_id=str(envelope["object_id"]), name=str(envelope["name"]),
        function_id=str(envelope["function_id"]), class_name=str(envelope.get("class_name") or envelope["function_id"]),
        class_version=int(envelope["class_version"]), job_id=job_id, envelope=envelope,
        method=envelope.get("method"), params=dict(envelope.get("params") or {}),
        args=list(envelope.get("args") or []), kwargs=dict(envelope.get("kwargs") or {}),
        from_version=envelope.get("from_version"), keep_revisions=envelope.get("keep_revisions"),
    ))


class ObjectCoordinator:
    def __init__(
        self,
        store: StateStore,
        submit_fn: SubmitFn,
        results: StorageBackend,
        event_bus: EventBus,
        self_id: str,
        now_fn: Callable[[], float] = time.time,
        job_id_fn: Callable[[], str] = lambda: uuid.uuid4().hex,
        logger: _Logger = None,
    ) -> None:
        self.store = store
        self._submit = submit_fn
        self._results = results
        self._bus = event_bus
        self._self_id = self_id
        self._now = now_fn
        self._job_id_fn = job_id_fn
        self._logger: _Logger = logger or DumbLogger()
        self._queues: Dict[str, Deque[ObjectOperation]] = {}
        self._running: Dict[str, _Running] = {}
        self._lock = threading.Lock()

    # ── operations that run code (jobs) ──────────────────────────────────────
    def handle_call(self, command: Command) -> CommandResult:
        envelope = command.envelope
        requested = envelope.get("job_id")
        if requested is not None:
            if not isinstance(requested, str) or not _JOB_ID_RE.match(requested):
                return CommandResult.from_error(InvalidFieldError(
                    "job_id must be 8-64 letters, digits, '-' or '_'", context={"job_id": str(requested)[:80]}))
            existing = self._results.exists(StorageKey(id=requested))
            if existing.is_ok and existing.unwrap():
                return CommandResult(ok=True, metadata={"job_id": requested, "status": "DUPLICATE",
                                                        "endpoint_id": self._self_id})
        parsed = parse_operation(envelope, requested or self._job_id_fn())
        if parsed.is_err:
            return CommandResult.from_error(parsed.unwrap_err())
        op = parsed.unwrap()
        self._results.put(StorageKey(id=op.job_id), FunctionResult(job_id=op.job_id, ok=False, error=PENDING))
        with self._lock:
            queue = self._queues.setdefault(op.object_id, deque())
            queue.append(op)
            position = len(queue) - 1
        self._logger.info_event(Event.Objects.CALL_QUEUED, component=Component.OBJECTS, object_id=op.object_id,
                                job_id=op.job_id, op=op.op, method=op.method, ahead=position)
        if position == 0:
            self._advance(op.object_id, start_first=True)
        return CommandResult(ok=True, metadata={"job_id": op.job_id, "status": "QUEUED",
                                                "endpoint_id": self._self_id, "ahead": position})

    def wrap(self, inner: OnComplete) -> OnComplete:
        """The completion hook every runtime calls: object jobs are saved here
        first, everything else passes straight through."""

        def on_complete(handle: InvocationHandle, outcome: "Result[Any, AxoError]",
                        warnings: Optional[List[str]] = None) -> None:
            with self._lock:
                running = self._running.pop(handle.job_id, None)
            if running is None:
                inner(handle, outcome, warnings)
                return
            try:
                outcome, manifest = self._finish(running, outcome)
                inner(handle, outcome, warnings)
                self._announce(running, outcome, manifest, handle)
            finally:
                self._advance(running.op.object_id)

        return on_complete

    def read_state(self, job_id: str, attribute: str) -> Result[bytes, AxoError]:
        """A running call asking for a saved value it wasn't sent."""
        with self._lock:
            running = self._running.get(job_id)
        if running is None:
            return Err(ActiveObjectStateUnreadableError("This job isn't an active object call", {"job_id": job_id}))
        return running.state.raw(attribute)

    def busy(self, object_id: str) -> bool:
        with self._lock:
            return bool(self._queues.get(object_id))

    def _advance(self, object_id: str, start_first: bool = False) -> None:
        """Finish the head of the object's queue (unless it's just starting)
        and start the next; operations that can't even start fail at once."""
        pop = not start_first
        while True:
            with self._lock:
                queue = self._queues.get(object_id)
                if queue and pop:
                    queue.popleft()
                if not queue:
                    self._queues.pop(object_id, None)
                    return
                op = queue[0]
            if self._start(op):
                return
            pop = True

    def _start(self, op: ObjectOperation) -> bool:
        loaded = self.store.load(op.object_id)
        if loaded.is_err:
            self._fail_unstarted(op, loaded.unwrap_err())
            return False
        state = loaded.unwrap()
        if op.op == "create" and state.revision > 0:
            self._fail_unstarted(op, ActiveObjectExistsError(
                f"Object {op.name} already has state", {"object_id": op.object_id}))
            return False
        manifest = state.manifest
        spec: Dict[str, Any] = {
            "op": op.op, "object_id": op.object_id, "name": op.name, "method": op.method,
            "args": op.args, "kwargs": op.kwargs, "from_version": op.from_version,
            "hot_max_bytes": self._policy(op.envelope).hot_max_bytes,
            "values": state.hot_values(),
            "lazy": {n: e.to_dict() for n, e in manifest.attributes.items() if e.tier == "blob"},
            "hashes": {n: e.hash for n, e in manifest.attributes.items()},
        }
        params = {**(op.params if op.op == "call" else {}), OBJECT_PARAM: spec}
        usage_fields = {k: v for k, v in op.envelope.items()
                        if k in ("trace_id", "started_by_type", "started_by_id", "user_id", "acting_user_id",
                                 "layer_waits_ms", "received_at", "max_wait", "event", "output_buckets",
                                 "choreography_run_id", "choreography_step", "choreography_version",
                                 "choreography_calls")}
        command = Command(operation=wire.JOB_SUBMIT, content_type="application/json", envelope={
            **usage_fields, "function_id": op.function_id, "function_name": op.class_name,
            "function_version": op.class_version, "params": params,
            "object_id": op.object_id, "object_method": op.label,
        })
        with self._lock:
            self._running[op.job_id] = _Running(op=op, state=state)
        submitted = self._submit(command, op.job_id)
        if not submitted.ok:
            with self._lock:
                self._running.pop(op.job_id, None)
            err = AxoError(submitted.error or "the call could not start")
            err.code, err.name = submitted.error_code or 0, submitted.error_name or "INVOCATION_FAILED"
            self._fail_unstarted(op, err)
            return False
        return True

    def _fail_unstarted(self, op: ObjectOperation, err: AxoError) -> None:
        """It never ran: nothing to bill, the job ends failed."""
        self._results.put(StorageKey(id=op.job_id), FunctionResult(
            job_id=op.job_id, ok=False, error=f"{err.name}: {err.message}"))
        self._emit(ACTIVE_OBJECT_CALLED_EVENT, {
            **self._call_payload(op), "outcome": "failed_platform", "revision": 0,
            "failure": _failure(err)})
        self._logger.warning_event(Event.Objects.CALL_FAILED, component=Component.OBJECTS, object_id=op.object_id,
                                   job_id=op.job_id, **err.to_dict())

    def _finish(self, running: _Running, outcome: "Result[Any, AxoError]"
                ) -> Tuple["Result[Any, AxoError]", Optional[StateManifest]]:
        op = running.op
        if outcome.is_err:
            return outcome, None                   # the call failed: nothing is saved
        value = outcome.unwrap()
        body = value.get(OBJECT_OUTCOME) if isinstance(value, dict) else None
        if not isinstance(body, dict):
            return Err(FunctionExecError("The object call returned no state; is the class an Axo class?",
                                         context={"object_id": op.object_id})), None
        try:
            changes = {name: (AttributeEntry.from_dict(c["entry"]), base64.b64decode(c["data"]))
                       for name, c in (body.get("changed") or {}).items()}
        except (KeyError, TypeError, ValueError) as exc:
            return Err(ActiveObjectStateUnreadableError(f"The call's changes arrived damaged: {exc}")), None
        committed = self.store.commit_encoded(op.object_id, running.state.revision, changes,
                                              body.get("deleted") or [], policy=self._policy(op.envelope),
                                              keep_revisions=op.keep_revisions)
        if committed.is_err:
            return Err(committed.unwrap_err()), None
        return Ok(body.get("result")), committed.unwrap()

    def _announce(self, running: _Running, outcome: "Result[Any, AxoError]",
                  manifest: Optional[StateManifest], handle: InvocationHandle) -> None:
        op = running.op
        before = running.state.manifest
        after = manifest or before
        changed = sorted(n for n, e in after.attributes.items() if before.attributes.get(n) != e)
        deleted = sorted(set(before.attributes) - set(after.attributes))
        duration_ms = round((self._now() - handle.started_at) * 1000, 2) if handle.started_at else None
        payload = {**self._call_payload(op), "revision": after.revision, "changed": changed, "deleted": deleted,
                   "size_bytes": after.size_bytes, "duration_ms": duration_ms,
                   "outcome": classify_outcome(None if outcome.is_ok else outcome.unwrap_err())}
        if outcome.is_err:
            payload["failure"] = _failure(outcome.unwrap_err())
        self._emit(ACTIVE_OBJECT_CALLED_EVENT, payload)
        if outcome.is_ok and op.op == "create":
            self._emit(ACTIVE_OBJECT_CREATED_EVENT, {
                "object_id": op.object_id, "name": op.name, "class_name": op.class_name,
                "class_version": op.class_version, "created_by": op.envelope.get("started_by_id")})
        if outcome.is_ok and op.op == "upgrade":
            self._emit(ACTIVE_OBJECT_UPGRADED_EVENT, {
                "object_id": op.object_id, "from_version": int(op.from_version or 0),
                "to_version": op.class_version, "revision": after.revision})
        self._logger.info_event(
            Event.Objects.CALL_FINISHED if outcome.is_ok else Event.Objects.CALL_FAILED,
            component=Component.OBJECTS, object_id=op.object_id, job_id=op.job_id, op=op.op,
            method=op.method, revision=after.revision, changed=changed)

    def _call_payload(self, op: ObjectOperation) -> Dict[str, Any]:
        return {"object_id": op.object_id, "job_id": op.job_id, "trace_id": op.envelope.get("trace_id") or op.job_id,
                "method": op.label, "class_version": op.class_version}

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        self._bus.emit(BusEvent(event_type=event_type, payload=payload, timestamp=self._now()))

    def _policy(self, envelope: Dict[str, Any]) -> StatePolicy:
        """The plan's limits as the VEM sent them now (``state_limits``), over
        this endpoint's launch settings."""
        limits = envelope.get("state_limits")
        return self.store.policy.with_limits(limits if isinstance(limits, dict) else None)

    # ── operations without code ──────────────────────────────────────────────
    def handle_clone(self, command: Command) -> CommandResult:
        env = command.envelope
        source, target = env.get("source_id"), env.get("object_id")
        if not source or not target or not env.get("name"):
            return CommandResult.from_error(MissingFieldError(
                "source_id, object_id and name are required", context={"fields": ["source_id", "object_id", "name"]}))
        cloned = self.store.clone(str(source), str(target), env.get("keep_revisions"), self._policy(env))
        if cloned.is_err:
            return CommandResult.from_error(cloned.unwrap_err())
        manifest = cloned.unwrap()
        self._emit(ACTIVE_OBJECT_CREATED_EVENT, {
            "object_id": target, "name": env["name"], "class_name": env.get("class_name") or "",
            "class_version": int(env.get("class_version") or 0), "created_by": env.get("started_by_id"),
            "cloned_from": source})
        return CommandResult(ok=True, metadata={"object_id": target, "revision": manifest.revision,
                                                "size_bytes": manifest.size_bytes})

    def handle_delete(self, command: Command) -> CommandResult:
        object_id = command.envelope.get("object_id")
        if not object_id:
            return CommandResult.from_error(MissingFieldError("object_id is required", context={"fields": ["object_id"]}))
        dropped = self.store.drop(str(object_id))
        if dropped.is_err:
            return CommandResult.from_error(dropped.unwrap_err())
        self._emit(ACTIVE_OBJECT_DELETED_EVENT, {"object_id": object_id})
        return CommandResult(ok=True, metadata={"object_id": object_id})

    def handle_state(self, command: Command) -> CommandResult:
        """What the object holds: small values in full, big ones by size,
        plus the kept revisions."""
        object_id = command.envelope.get("object_id")
        if not object_id:
            return CommandResult.from_error(MissingFieldError("object_id is required", context={"fields": ["object_id"]}))
        current = self.store.manifest(str(object_id))
        if current.is_err:
            return CommandResult.from_error(current.unwrap_err())
        if current.unwrap() is None:
            return CommandResult.from_error(ActiveObjectNotFoundError(
                f"Object {object_id} has no saved state", {"object_id": object_id}))
        revision = command.envelope.get("revision")
        loaded = self.store.load(str(object_id), revision=int(revision) if revision is not None else None)
        if loaded.is_err:
            return CommandResult.from_error(loaded.unwrap_err())
        state = loaded.unwrap()
        revisions = self.store.revisions(str(object_id))
        if revisions.is_err:
            return CommandResult.from_error(revisions.unwrap_err())
        hot = state.hot_values()
        attributes = {name: {**e.to_dict(), **({"value": hot[name]} if name in hot else {})}
                      for name, e in state.manifest.attributes.items()}
        return CommandResult(ok=True, metadata={
            "object_id": object_id, "revision": state.revision, "size_bytes": state.manifest.size_bytes,
            "updated_at": state.manifest.updated_at, "attributes": attributes, "busy": self.busy(str(object_id)),
            "revisions": [{"revision": m.revision, "updated_at": m.updated_at, "size_bytes": m.size_bytes}
                          for m in revisions.unwrap()]})

    def handle_restore(self, command: Command) -> CommandResult:
        env = command.envelope
        object_id, revision = env.get("object_id"), env.get("revision")
        if not object_id or revision is None:
            return CommandResult.from_error(MissingFieldError(
                "object_id and revision are required", context={"fields": ["object_id", "revision"]}))
        if self.busy(str(object_id)):
            return CommandResult.from_error(ActiveObjectStateConflictError(
                "The object is running a call; restore it when it's done", {"object_id": object_id}))
        current = self.store.manifest(str(object_id))
        if current.is_err:
            return CommandResult.from_error(current.unwrap_err())
        base = current.unwrap().revision if current.unwrap() else 0
        restored = self.store.restore(str(object_id), int(revision), base, env.get("keep_revisions"),
                                      self._policy(env))
        if restored.is_err:
            return CommandResult.from_error(restored.unwrap_err())
        manifest = restored.unwrap()
        self._emit(ACTIVE_OBJECT_CALLED_EVENT, {
            "object_id": object_id, "job_id": env.get("job_id") or f"restore-{manifest.revision}",
            "trace_id": env.get("trace_id") or "", "method": "__restore__", "class_version": int(env.get("class_version") or 0),
            "outcome": "completed", "revision": manifest.revision, "changed": sorted(manifest.attributes),
            "deleted": [], "size_bytes": manifest.size_bytes})
        return CommandResult(ok=True, metadata={"object_id": object_id, "revision": manifest.revision,
                                                "restored_from": int(revision)})

    # ── housekeeping (leader only) ───────────────────────────────────────────
    def report_sizes(self) -> Result[None, AxoError]:
        stored = self.store.stored_bytes()
        if stored.is_err:
            return stored
        sizes = self.store.sizes()
        if sizes.is_err:
            return sizes
        self._emit(ACTIVE_OBJECT_SIZES_EVENT, {"stored_bytes": stored.unwrap(), "objects": sizes.unwrap()})
        return Ok(None)


def _failure(err: AxoError) -> Dict[str, Any]:
    return {"error_class": getattr(err, "name", type(err).__name__), "error_code": getattr(err, "code", 0),
            "component": "active_objects", "message": getattr(err, "message", str(err)), "is_transient": False}


class StateAwareDataIO:
    """The runtimes' dataio resolver, plus ``state_get`` for running object calls."""

    def __init__(self, inner: Any, coordinator: Optional[ObjectCoordinator]) -> None:
        self._inner = inner
        self._coordinator = coordinator

    def resolve(self, job_id: str, op: str, ref: Any, data: Optional[bytes]):
        from axo_endpoint.core.dataio import DataIOUnavailableError
        from axo_endpoint.core.runtime.entry import STATE_GET

        if op == STATE_GET:
            if self._coordinator is None:
                return Err(DataIOUnavailableError("this endpoint runs no active objects", context={"op": op}))
            return self._coordinator.read_state(job_id, ref.name)
        if self._inner is None:
            return Err(DataIOUnavailableError("this endpoint has no axo_storage configured", context={"op": op}))
        return self._inner.resolve(job_id, op, ref, data)
