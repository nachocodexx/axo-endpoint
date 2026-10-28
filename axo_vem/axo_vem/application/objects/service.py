"""Active objects of this VE: Axo classes and their objects.

A class version is registered on the endpoints like a function (``call_style``
"object", one VE-wide function per class name), and recorded here with its
callable methods. An object has a name unique in the VE; the VEM reserves
the name, then asks the endpoints (the leader runs every object operation,
one at a time per object) to create it, call it, upgrade it, ... A call is a
job: the answer carries its ``job_id`` and the endpoint to poll.
"""

from __future__ import annotations

import ast
import hashlib
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from axo_shared import wire
from axo_shared.active_objects.source import check_class_source, read_class
from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import Command

from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.register_function import CODE_FORMATS, RegisterFunctionUseCase
from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError, UpstreamTimeoutError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.events.stream_naming import stream_name_for
from axo_vem.domain.objects.repository import ActiveObjectRepository, ObjectClassRepository
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import send_command

# Classes belong to the VE, not to whoever registered them first: their
# function id is derived from this owner, the VE and the class name.
CLASS_OWNER = "axo-object-classes"
DEFAULT_METHOD = "run"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")


class ObjectService:
    def __init__(
        self,
        virtual_environment_id: str,
        endpoint_picker: EndpointPicker,
        register_function: RegisterFunctionUseCase,
        classes: ObjectClassRepository,
        objects: ActiveObjectRepository,
        event_publisher: Optional[EventPublisher] = None,
        command_timeout_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        id_fn: Callable[[], str] = lambda: f"obj-{uuid.uuid4().hex}",
        limits_fn: Callable[[], PlanLimits] = PlanLimits,
        delete_function: Any = None,             # DeleteFunctionUseCase: a class version's code on the endpoints
    ) -> None:
        self._ve = virtual_environment_id
        self.jobs: Any = None                  # JobsService (set by the server): each call's job record
        self._picker = endpoint_picker
        self._register = register_function
        self._classes = classes
        self._objects = objects
        self._publisher = event_publisher
        self._timeout = command_timeout_seconds
        self._clock = clock
        self._id_fn = id_fn
        self._limits_fn = limits_fn
        self._delete_function = delete_function

    # ── classes ──────────────────────────────────────────────────────────────
    def class_function_id(self, class_name: str) -> str:
        return compute_function_id(CLASS_OWNER, self._ve, class_name)

    def register_class(
        self, *, user_id: str, class_name: str, code: bytes, code_format: str = "source",
        code_hash: Optional[str] = None, methods: Optional[List[Dict[str, Any]]] = None,
        has_upgrade: bool = False, runtime_spec: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """A new class version -- unless this exact code is already one."""
        if not CLASS_NAME_RE.match(class_name or ""):
            raise InvalidRequestError(f"{class_name!r} is not a class name", error_name="ACTIVE_OBJECT_CLASS_INVALID")
        if code_format not in CODE_FORMATS:
            raise InvalidRequestError(f"code_format must be one of {', '.join(CODE_FORMATS)}",
                                      error_name="ACTIVE_OBJECT_CLASS_INVALID")
        if code_format == "source":
            _check_class_source(code, class_name)
        code_hash = code_hash or _code_hash(code_format, code)
        known = self._classes.find_by_code_hash(class_name, code_hash)
        if known is not None:
            return {**_class_answer(known), "existing": True}
        # From the source (the browser sends none of these): the callable
        # methods with their forms, __init__'s parameters, the upgrade step.
        read = read_class(code, class_name) if code_format == "source" else None
        if read is not None:
            forms = {m["name"]: m for m in read["methods"]}
            methods = ([{**m, "params_schema": forms.get(m.get("name"), {}).get("params_schema")} for m in methods]
                       if methods else read["methods"])
            has_upgrade = has_upgrade or read["has_upgrade"]

        function_id = self.class_function_id(class_name)
        registered = self._register.execute(
            current_user_id=user_id, name=class_name, code=code, runtime_spec=runtime_spec,
            code_format=code_format, code_hash=code_hash, call_style="object", function_id=function_id,
        )
        version = int(registered["version"])
        event = models.ActiveObjectClassRegistered(
            user_id=user_id, virtual_environment_id=self._ve, class_name=class_name, version=version,
            code_hash=code_hash, methods=list(methods or []), has_upgrade=bool(has_upgrade),
            function_id=function_id, runtime_spec={**(runtime_spec or {}), "call_style": "object"},
            init=read["init"] if read else None, description=read["description"] if read else None,
        )
        data = event.model_dump(mode="json")
        # Saved here as well as through the projector, so an object can be
        # created with it right away.
        self._classes.save({**{k: data[k] for k in ("class_name", "version", "code_hash", "methods", "has_upgrade",
                                                     "function_id", "runtime_spec", "init", "description",
                                                     "created_at")},
                            "created_by": user_id})
        if self._publisher is not None:
            self._publisher.append_to_stream(
                stream_name_for(models.ACTIVE_OBJECT_CLASS_REGISTERED, "", data),
                models.ACTIVE_OBJECT_CLASS_REGISTERED, data)
        return {**_class_answer(self._classes.get(class_name, version)), "existing": False}

    def classes(self, class_name: Optional[str] = None) -> List[Dict[str, Any]]:
        found = self._classes.list(class_name)
        if class_name and not found:
            raise NotFoundError(f"No class {class_name}", error_name="ACTIVE_OBJECT_CLASS_NOT_FOUND")
        return found

    def check_class(self, source: str, class_name: str) -> Dict[str, Any]:
        """The browser's live check of a class being written (never runs it);
        ``existing_version``: the version with this same code, if any."""
        out = check_class_source(source.encode("utf-8"), class_name)
        same = self._classes.find_by_code_hash(class_name, out["code_hash"]) if class_name else None
        out["existing_version"] = same["version"] if same else None
        return out

    def delete_class_version(self, *, user_id: str, class_name: str, version: int) -> Dict[str, Any]:
        """Only a version no object is on (whatever its status); its code goes
        from the endpoints too, so a VE start doesn't bring it back."""
        doc = self._class(class_name, version)
        using = self._objects.using(class_name, int(version))
        if using:
            names = ", ".join(sorted(o["name"] for o in using)[:5])
            raise ConflictError(f"{class_name} v{version} is used by {len(using)} object(s): {names}",
                                error_name="ACTIVE_OBJECT_CLASS_IN_USE")
        if self._delete_function is not None and doc.get("function_id"):
            try:
                self._delete_function.execute(function_id=doc["function_id"], version=int(version),
                                              current_user_id=user_id)
            except NotFoundError:
                pass                                   # its code is already gone
        self._classes.remove(class_name, int(version))
        event = models.ActiveObjectClassVersionDeleted(user_id=user_id, virtual_environment_id=self._ve,
                                                       class_name=class_name, version=int(version))
        data = event.model_dump(mode="json")
        if self._publisher is not None:
            self._publisher.append_to_stream(
                stream_name_for(models.ACTIVE_OBJECT_CLASS_VERSION_DELETED, "", data),
                models.ACTIVE_OBJECT_CLASS_VERSION_DELETED, data)
        return {"class_name": class_name, "version": int(version), "deleted": True}

    def _class(self, class_name: str, version: Optional[int]) -> Dict[str, Any]:
        doc = self._classes.latest(class_name) if version is None else self._classes.get(class_name, int(version))
        if doc is None:
            which = f"{class_name} v{version}" if version is not None else class_name
            raise NotFoundError(f"No class {which}", error_name="ACTIVE_OBJECT_CLASS_NOT_FOUND")
        return doc

    def lookup(self, name: str) -> Optional[tuple]:
        """(the object, its class version) -- None if either is missing.
        What a choreography's object steps are checked against."""
        obj = self._objects.get(name)
        if obj is None or obj.get("status") not in (None, "ready"):
            return None
        cls = self._classes.get(obj["class_name"], int(obj["class_version"]))
        return (obj, cls) if cls is not None else None

    # ── objects ──────────────────────────────────────────────────────────────
    def objects(self) -> List[Dict[str, Any]]:
        return self._objects.list()

    def info(self, name: str) -> Dict[str, Any]:
        doc = self._objects.get(name)
        if doc is None:
            raise NotFoundError(f"No object {name}", error_name="ACTIVE_OBJECT_NOT_FOUND")
        return doc

    def create(
        self, *, user_id: str, name: str, class_name: str, class_version: Optional[int] = None,
        args: Optional[List[Any]] = None, kwargs: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None, max_wait: Optional[float] = None,
        upstream_waits_ms: Optional[Dict[str, float]] = None, keep_revisions: Optional[int] = None,
    ) -> Dict[str, Any]:
        began = self._clock()
        _check_name(name)
        self._check_keep(keep_revisions)
        cls = self._class(class_name, class_version)
        object_id = self._id_fn()
        reserved = self._objects.reserve({
            "name": name, "object_id": object_id, "class_name": cls["class_name"],
            "class_version": cls["version"], "function_id": cls["function_id"], "status": "creating",
            "created_by": user_id, "created_at": _now(), "revision": 0, "size_bytes": 0, "calls": 0,
            "keep_revisions": keep_revisions,
        })
        if not reserved:
            raise ConflictError(f"An object named {name} already exists", error_name="ACTIVE_OBJECT_EXISTS")
        try:
            answer = self._call(began, user_id, {
                "op": "create", "object_id": object_id, "name": name, "function_id": cls["function_id"],
                "class_name": cls["class_name"], "class_version": cls["version"], "keep_revisions": keep_revisions,
                "args": list(args or []), "kwargs": dict(kwargs or {}),
            }, job_id, max_wait, upstream_waits_ms)
        except Exception:
            self._objects.remove(name, object_id)
            raise
        return {**answer, "object_id": object_id, "name": name}

    def call(
        self, *, user_id: str, name: str, method: Optional[str] = None, params: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None, max_wait: Optional[float] = None,
        upstream_waits_ms: Optional[Dict[str, float]] = None, started_by_type: str = "user",
        started_by_id: Optional[str] = None, event: Any = None, output_buckets: Optional[List[str]] = None,
        step_ref: Optional[Dict[str, Any]] = None, started_by_name: Optional[str] = None,
        choreography: Optional[Dict[str, Any]] = None, keep_result: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queued behind the object's other calls (even its create). A
        choreography step passes ``event`` (``self.event``), the buckets
        it's connected to and itself as the starter."""
        began = self._clock()
        obj = self.info(name)
        cls = self._class(obj["class_name"], obj["class_version"])
        method = method or DEFAULT_METHOD
        callable_names = sorted(m.get("name") for m in cls.get("methods") or [])
        if callable_names and method not in callable_names:
            raise NotFoundError(f"{obj['class_name']} has no callable method {method!r} "
                                f"(it has: {', '.join(callable_names)})",
                                error_name="ACTIVE_OBJECT_METHOD_NOT_FOUND")
        extra: Dict[str, Any] = {"started_by_type": started_by_type, "started_by_id": started_by_id or user_id}
        if event is not None:
            extra["event"] = event
        if output_buckets:
            extra["output_buckets"] = list(output_buckets)
        extra.update(step_ref or {})
        extra["_choreography"], extra["_started_by_name"] = choreography, started_by_name
        extra["_keep_result"] = keep_result
        return self._call(began, user_id, {**self._target(obj), "op": "call", "method": method,
                                            "params": dict(params or {}), **extra},
                          job_id, max_wait, upstream_waits_ms)

    def upgrade(self, *, user_id: str, name: str, class_version: Optional[int] = None) -> Dict[str, Any]:
        """To a newer version of the object's class (the latest when None);
        the new class's ``upgrade(self, old_version)`` runs if it has one."""
        began = self._clock()
        obj = self.info(name)
        target = self._class(obj["class_name"], class_version)
        if int(target["version"]) <= int(obj["class_version"]):
            raise ConflictError(f"{name} is on {obj['class_name']} v{obj['class_version']}; "
                                f"upgrade only moves to a newer version",
                                error_name="ACTIVE_OBJECT_STATE_CONFLICT")
        return self._call(began, user_id, {**self._target(obj), "op": "upgrade",
                                            "class_version": target["version"],
                                            "from_version": obj["class_version"]}, None, None, None)

    def clone(self, *, user_id: str, source: str, name: str) -> Dict[str, Any]:
        """A new object with a copy of ``source``'s state (values shared
        until either changes); no code runs."""
        _check_name(name)
        obj = self.info(source)
        object_id = self._id_fn()
        reserved = self._objects.reserve({
            "name": name, "object_id": object_id, "class_name": obj["class_name"],
            "class_version": obj["class_version"], "function_id": obj["function_id"], "status": "creating",
            "created_by": user_id, "created_at": _now(), "cloned_from": source,
            "revision": 0, "size_bytes": 0, "calls": 0, "keep_revisions": obj.get("keep_revisions"),
        })
        if not reserved:
            raise ConflictError(f"An object named {name} already exists", error_name="ACTIVE_OBJECT_EXISTS")
        try:
            answer = self._send(wire.ACTIVE_OBJECT_CLONE, {
                "source_id": obj["object_id"], "object_id": object_id, "name": name,
                "class_name": obj["class_name"], "class_version": obj["class_version"], "started_by_id": user_id,
                "keep_revisions": obj.get("keep_revisions"),
            })
        except Exception:
            self._objects.remove(name, object_id)
            raise
        self._objects.mark_ready(name, object_id)
        return {**answer, "object_id": object_id, "name": name, "cloned_from": source}

    def state(self, name: str, revision: Optional[int] = None) -> Dict[str, Any]:
        obj = self.info(name)
        envelope: Dict[str, Any] = {"object_id": obj["object_id"]}
        if revision is not None:
            envelope["revision"] = int(revision)
        return {"name": name, **self._send(wire.ACTIVE_OBJECT_STATE, envelope)}

    def restore(self, *, user_id: str, name: str, revision: int) -> Dict[str, Any]:
        """Back to a kept revision (as a new revision; refused while it runs a call)."""
        obj = self.info(name)
        return {"name": name, **self._send(wire.ACTIVE_OBJECT_RESTORE, {
            "object_id": obj["object_id"], "revision": int(revision), "class_version": obj["class_version"],
            "started_by_id": user_id, "keep_revisions": obj.get("keep_revisions")})}

    def set_keep_revisions(self, *, user_id: str, name: str, keep_revisions: Optional[int]) -> Dict[str, Any]:
        """How many past revisions this object keeps (None = the plan's
        default); applies from its next saved call."""
        self._check_keep(keep_revisions)
        obj = self.info(name)
        self._objects.set_keep_revisions(name, keep_revisions)
        return {**obj, "keep_revisions": keep_revisions}

    def _check_keep(self, keep: Optional[int]) -> None:
        if keep is None:
            return
        problem = self._limits_fn().keep_revisions_problem(int(keep))
        if problem:
            raise InvalidRequestError(problem)

    def delete(self, *, user_id: str, name: str) -> Dict[str, Any]:
        obj = self.info(name)
        try:
            self._send(wire.ACTIVE_OBJECT_DELETE, {"object_id": obj["object_id"], "started_by_id": user_id})
        except NotFoundError:
            pass                                   # it never got any state (e.g. __init__ still queued)
        self._objects.remove(name, obj["object_id"])
        return {"name": name, "object_id": obj["object_id"], "deleted": True}

    # ── sending to the endpoints ─────────────────────────────────────────────
    @staticmethod
    def _target(obj: Dict[str, Any]) -> Dict[str, Any]:
        return {"object_id": obj["object_id"], "name": obj["name"], "function_id": obj["function_id"],
                "class_name": obj["class_name"], "class_version": obj["class_version"],
                "keep_revisions": obj.get("keep_revisions")}

    def _call(self, began: float, user_id: str, envelope: Dict[str, Any], job_id: Optional[str],
              max_wait: Optional[float], upstream_waits_ms: Optional[Dict[str, float]]) -> Dict[str, Any]:
        """An operation that runs code: a job, with the same usage fields as
        a function job (who it acts for, the layers' waits)."""
        choreography = envelope.pop("_choreography", None)
        started_by_name = envelope.pop("_started_by_name", None)
        keep_result = envelope.pop("_keep_result", None)
        envelope = {
            "started_by_type": "user", "started_by_id": user_id, **envelope, "acting_user_id": user_id,
            **({"job_id": job_id} if job_id else {}),
            **({"max_wait": max_wait} if max_wait is not None else {}),
        }
        answer = self._send(wire.ACTIVE_OBJECT_CALL, envelope, began=began, upstream_waits_ms=upstream_waits_ms)
        if self.jobs is not None and answer.get("job_id") and answer.get("status") != "DUPLICATE":
            op = envelope.get("op")
            self.jobs.submitted(
                answer["job_id"], function_id=envelope.get("function_id") or "",
                function_version=envelope.get("class_version"),
                params=envelope.get("params") if op == "call" else {"args": envelope.get("args"),
                                                                    "kwargs": envelope.get("kwargs")},
                endpoint_id=answer.get("endpoint_id"), acting_user_id=user_id,
                started_by_type=envelope["started_by_type"], started_by_id=envelope["started_by_id"],
                started_by_name=started_by_name, kind="object", function_name=envelope.get("class_name"),
                object_name=envelope.get("name"),
                object_method={"create": "__create__", "upgrade": "__upgrade__"}.get(op, envelope.get("method")),
                choreography=choreography, keep_result=keep_result,
            )
        return answer

    def _send(self, operation: str, envelope: Dict[str, Any], began: Optional[float] = None,
              upstream_waits_ms: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """To any running endpoint -- a follower passes it to the leader,
        whose answer names itself as the endpoint to poll."""
        began = self._clock() if began is None else began
        endpoint = self._picker.pick()
        if operation in (wire.ACTIVE_OBJECT_CALL, wire.ACTIVE_OBJECT_CLONE, wire.ACTIVE_OBJECT_RESTORE):
            # The plan's limits now (a plan change applies to the next call).
            envelope = {**envelope, "state_limits": self._limits_fn().state_limits()}
        if operation == wire.ACTIVE_OBJECT_CALL:
            envelope = {**envelope, "layer_waits_ms": {
                **(upstream_waits_ms or {}), "vem": round(max(0.0, self._clock() - began) * 1000, 3)}}
        command = Command(operation=operation, content_type="application/json", envelope=envelope)
        result = send_command(self._picker.rpc_uri(endpoint), command, self._timeout)
        if result.is_err:
            raise UpstreamTimeoutError(str(result.unwrap_err()))
        reply = result.unwrap()
        if not reply.ok:
            meta = reply.metadata or {}
            name = reply.error_name or meta.get("error_name") or None
            code = reply.error_code or meta.get("error_code") or None
            error = NotFoundError if name and name.endswith("_NOT_FOUND") else ConflictError
            raise error(reply.error, error_name=name, error_code=code)
        return {"endpoint_id": endpoint.endpoint_id, **(reply.metadata or {})}


def _class_answer(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"class_name": doc["class_name"], "version": doc["version"], "function_id": doc.get("function_id"),
            "code_hash": doc.get("code_hash")}


def _check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise InvalidRequestError("An object name is 1-63 letters, digits, '.', '-' or '_' (starting with a "
                                  "letter or digit)")


def _check_class_source(code: bytes, class_name: str) -> None:
    try:
        tree = ast.parse(code.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise InvalidRequestError(f"The class code doesn't parse: {exc}", error_name="ACTIVE_OBJECT_CLASS_INVALID")
    if not any(isinstance(node, ast.ClassDef) and node.name == class_name for node in tree.body):
        raise InvalidRequestError(f"The code doesn't define a top-level class {class_name}",
                                  error_name="ACTIVE_OBJECT_CLASS_INVALID")


def _code_hash(code_format: str, code: bytes) -> str:
    """The same hash the axo library computes (``Code.code_hash``)."""
    h = hashlib.sha256(("pickle" if code_format != "source" else "source").encode())
    h.update(b":")
    h.update(code)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
