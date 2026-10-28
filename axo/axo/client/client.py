"""``AxoClient``: functions, objects and jobs of one VE, through the Control
Plane with an API key.

    from axo import AxoClient
    client = AxoClient(key="axo_...", ve="ve-123")   # or AXO_API_KEY / AXO_VE
    job = client.call(add, a=1, b=2).unwrap()
    job.result().unwrap()                            # 3

    counter = client.create(Counter, "visits").unwrap()
    counter.run(by=2).unwrap().result()              # a method call is a job too

Every call returns an ``option.Result``: ``Ok(value)`` or ``Err(AxoError)``.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Tuple, Union

import httpx
from option import Err, Ok, Result

from axo.client.handles import JobHandle, ObjectHandle, RunHandle
from axo.core.axo import UPGRADE_METHOD, Axo, methods_of
from axo.core.capture import Code, capture
from axo.core.decorators import AxoFunction, FunctionOptions
from axo.errors import AxoApiError, AxoConnectionError, AxoError

if TYPE_CHECKING:
    from axo.choreography import Choreography

DEFAULT_BASE_URL = "http://localhost:9000"
CALL_STYLE = "kwargs"                     # parameters by name, ctx only if the function asks for it


@dataclass(frozen=True)
class FunctionRef:
    function_id: str
    version: int
    name: str


@dataclass(frozen=True)
class ClassRef:
    class_name: str
    version: int


class AxoClient:
    def __init__(self, key: Optional[str] = None, ve: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: float = 30.0, transport: Optional[httpx.BaseTransport] = None) -> None:
        key = key or os.environ.get("AXO_API_KEY")
        ve = ve or os.environ.get("AXO_VE")
        if not key or not ve:
            raise ValueError("AxoClient needs an API key and a VE (arguments, or AXO_API_KEY / AXO_VE)")
        self.ve = ve
        self._http = httpx.Client(base_url=base_url or os.environ.get("AXO_API_URL") or DEFAULT_BASE_URL,
                                  headers={"X-API-Key": key}, timeout=timeout, transport=transport)
        # code hash -> what the platform answered, so a script calling the
        # same function a thousand times registers it once.
        self._known: Dict[str, Union[FunctionRef, ClassRef]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AxoClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── HTTP ─────────────────────────────────────────────────────────────────
    def api(self, method: str, path: str, **kwargs: Any) -> Result[Any, AxoError]:
        """One call to the VE's API (``/ves/<ve>/api<path>``)."""
        try:
            response = self._http.request(method, f"/ves/{self.ve}/api{path}", **kwargs)
        except httpx.HTTPError as exc:
            return Err(AxoConnectionError(f"Can't reach the platform: {exc}"))
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text
        if response.status_code >= 400:
            return Err(AxoApiError.from_response(response.status_code, body))
        return Ok(body)

    # ── functions ────────────────────────────────────────────────────────────
    def register(self, fn: Any) -> Result[FunctionRef, AxoError]:
        """Saves a function on the VE (a new version only if its code changed)."""
        if not isinstance(fn, AxoFunction):
            from axo.core.decorators import axo_function
            fn = axo_function(fn)
        code = capture(fn)
        if code.is_err:
            return code
        code = code.unwrap()
        with self._lock:
            known = self._known.get(code.code_hash)
        if isinstance(known, FunctionRef):
            return Ok(known)
        data = {
            "name": fn.name, "code_format": code.format, "code_hash": code.code_hash, "call_style": CALL_STYLE,
            "runtime_spec": json.dumps(fn.options.runtime_spec()),
            "params_schema": json.dumps(fn.spec.params_schema()),
        }
        if code.display:
            data["display_source"] = code.display
        sent = self.api("POST", "/functions", data=data, files={"code": (f"{fn.name}.py", code.data)})
        if sent.is_err:
            return sent
        body = sent.unwrap()
        ref = FunctionRef(function_id=body.get("function_id") or fn.name, version=int(body["version"]),
                          name=fn.name)
        with self._lock:
            self._known[code.code_hash] = ref
        return Ok(ref)

    def call(self, fn: Union[AxoFunction, FunctionRef, Any], max_wait: Optional[float] = None,
             job_id: Optional[str] = None, keep_result: Optional[str] = None,
             max_attempts: Optional[int] = None, retry_delay: Optional[float] = None,
             retry_backoff: Optional[str] = None, **params: Any) -> Result[JobHandle, AxoError]:
        """Runs a function as a job; ``params`` are its arguments by name.
        ``keep_result`` (1h 1d 7d 30d 90d 1y never; else the function's or the
        VE's default) and the attempt settings apply to this job only."""
        if not isinstance(fn, FunctionRef):
            registered = self.register(fn)
            if registered.is_err:
                return registered
            fn = registered.unwrap()
        ref = fn
        body: Dict[str, Any] = {"params": params}
        if max_wait is not None:
            body["max_wait"] = max_wait
        if job_id is not None:
            body["job_id"] = job_id
        for key, value in (("keep_result", keep_result), ("max_attempts", max_attempts),
                           ("retry_delay_seconds", retry_delay), ("retry_backoff", retry_backoff)):
            if value is not None:
                body[key] = value
        sent = self.api("POST", f"/functions/{ref.function_id}/{ref.version}/jobs", json=body)
        return sent.map(lambda b: JobHandle.from_answer(self, b))

    def function(self, name: str, version: Optional[int] = None) -> Result[FunctionRef, AxoError]:
        """A function already registered in the VE, by name or id, to
        ``call`` without its code: ``version`` or else its default version
        (the newest unless someone picked another on the Versions tab)."""
        listed = self.api("GET", "/functions")
        if listed.is_err:
            return listed
        live = [d for d in listed.unwrap() or []
                if name in (d.get("name"), d.get("function_id")) and not d.get("deleted_at")]
        if version is not None:
            live = [d for d in live if int(d.get("version") or 0) == version]
        else:
            live = [d for d in live if d.get("is_default")] or sorted(live, key=lambda d: -int(d.get("version") or 0))[:1]
        if not live:
            which = f"{name} v{version}" if version is not None else name
            return Err(AxoApiError(f"No function {which} in this VE", name="FUNCTION_NOT_FOUND", code=2001, status=404))
        doc = live[0]
        return Ok(FunctionRef(function_id=doc["function_id"], version=int(doc["version"]), name=doc.get("name") or name))

    def job(self, job_id: str, endpoint_id: Optional[str] = None) -> JobHandle:
        return JobHandle(self, job_id, endpoint_id)

    # ── objects ──────────────────────────────────────────────────────────────
    def register_class(self, cls: type, python_version: Optional[str] = None,
                       requirements: Tuple[str, ...] = ()) -> Result[ClassRef, AxoError]:
        """Saves an Axo class on the VE (a new version only if its code changed)."""
        if not (isinstance(cls, type) and issubclass(cls, Axo)):
            return Err(AxoError(f"{cls!r} is not an Axo class", name="ACTIVE_OBJECT_CLASS_INVALID", code=9410))
        code = capture(cls)
        if code.is_err:
            return code
        code = code.unwrap()
        with self._lock:
            known = self._known.get(code.code_hash)
        if isinstance(known, ClassRef):
            return Ok(known)
        spec = FunctionOptions(python_version=python_version, requirements=requirements).runtime_spec()
        sent = self.api("POST", "/object-classes", data={
            "class_name": cls.__name__, "code_format": code.format, "code_hash": code.code_hash,
            "methods": json.dumps([m.to_dict() for m in methods_of(cls).values()]),
            "has_upgrade": json.dumps(callable(getattr(cls, UPGRADE_METHOD, None))),
            "runtime_spec": json.dumps(spec),
        }, files={"code": (f"{cls.__name__}.py", code.data)})
        if sent.is_err:
            return sent
        ref = ClassRef(class_name=cls.__name__, version=int(sent.unwrap()["version"]))
        with self._lock:
            self._known[code.code_hash] = ref
        return Ok(ref)

    def create(self, cls: Union[type, ClassRef], name: str, *args: Any, **kwargs: Any) -> Result[ObjectHandle, AxoError]:
        """A new object named ``name`` (unique in the VE); ``__init__`` runs on
        the platform with these arguments (JSON values). The handle's
        ``created`` job finishes when it did."""
        if not isinstance(cls, ClassRef):
            registered = self.register_class(cls)
            if registered.is_err:
                return registered
            cls = registered.unwrap()
        ref = cls
        sent = self.api("POST", "/objects", json={"name": name, "class_name": ref.class_name,
                                                  "class_version": ref.version, "args": list(args),
                                                  "kwargs": kwargs})
        return sent.map(lambda b: ObjectHandle(self, name, created=JobHandle.from_answer(self, b)))

    def object(self, name: str) -> ObjectHandle:
        """A handle on an existing object (nothing is checked until it's used)."""
        return ObjectHandle(self, name)

    def objects(self) -> Result[list, AxoError]:
        return self.api("GET", "/objects")

    def clone(self, source: str, name: str) -> Result[ObjectHandle, AxoError]:
        """A new object with a copy of ``source``'s state (shared until either changes)."""
        return self.api("POST", f"/objects/{source}/clone", json={"name": name}).map(
            lambda _: ObjectHandle(self, name))

    # ── choreographies ───────────────────────────────────────────────────────
    def save(self, choreography: "Choreography") -> Result[Dict[str, Any], AxoError]:
        """Saves a choreography by name: registers its functions first (only
        code that changed) and saves the choreographies it calls given as
        ``Choreography`` objects, then a new version if the graph changed
        (``existing: true`` when it didn't). Triggers declared with
        ``on_bucket`` / ``on_event`` replace the saved ones; none declared =
        the newest version's are kept. ``visibility`` set = applied after."""
        refs: Dict[int, FunctionRef] = {}
        for step in choreography.steps:
            if step.kind != "function":
                continue
            target = step.target
            ref = target if isinstance(target, FunctionRef) else None
            if ref is None:
                registered = self.register(target)
                if registered.is_err:
                    return registered
                ref = registered.unwrap()
            refs[id(step)] = ref
        called: Dict[int, tuple] = {}
        for step in choreography.steps:
            if step.kind != "choreography":
                continue
            target = step.target
            if not isinstance(target, str):
                inner = self.save(target)              # a Choreography: saved first (only what changed)
                if inner.is_err:
                    return inner
                target = target.name
            found = self.api("GET", f"/choreographies/{target}")
            if found.is_err:
                return found
            called[id(step)] = (found.unwrap()["choreography_id"], found.unwrap()["name"])
        graph = choreography.graph(lambda step: (refs[id(step)].function_id, None, refs[id(step)].name),
                                   lambda step: called[id(step)])
        if choreography.triggers is None:
            current = self.api("GET", f"/choreographies/{choreography.name}")
            if current.is_ok:
                graph["triggers"] = (current.unwrap().get("graph") or {}).get("triggers") or []
        body: Dict[str, Any] = {"name": choreography.name, "graph": graph}
        if choreography.description is not None:
            body["description"] = choreography.description
        if choreography.settings:
            body["settings"] = choreography.settings
        saved = self.api("POST", "/choreographies/save", json=body)
        if saved.is_err or choreography.visibility is None:
            return saved
        return self.set_visibility("choreography", choreography.name, choreography.visibility).map(
            lambda visibility: {**saved.unwrap(), "visibility": visibility})

    def set_visibility(self, kind: str, ref: str,
                       visibility: Union[str, Sequence[str]]) -> Result[Dict[str, Any], AxoError]:
        """Who sees the runs and results of a ``function`` (its id), a
        ``choreography`` (its name) or an ``object`` (its name) besides who
        started them: ``"runner"`` (default), ``"ve"`` or a list of team ids.
        Its creator and the VE's Admins / Owner change it."""
        if isinstance(visibility, str):
            body: Dict[str, Any] = {"mode": visibility, "teams": []}
        else:
            body = {"mode": "teams", "teams": list(visibility)}
        return self.api("PUT", f"/visibility/{kind}/{ref}", json=body)

    def emit(self, event_type: str, data: Any = None, id: Optional[str] = None, source: Optional[str] = None,
             subject: Optional[str] = None) -> Result[Dict[str, Any], AxoError]:
        """Sends an event: every choreography with a trigger on
        ``event_type`` (whose condition its data matches) starts a run. The
        same ``id`` twice is one event. Answers the event with ``runs``."""
        body: Dict[str, Any] = {"type": event_type, "data": data}
        for key, value in (("event_id", id), ("source", source), ("subject", subject)):
            if value is not None:
                body[key] = value
        return self.api("POST", "/events", json=body)

    def events(self, event_type: Optional[str] = None, limit: int = 50) -> Result[Dict[str, Any], AxoError]:
        """The newest events you can see (yours, and those that started your triggers)."""
        params: Dict[str, Any] = {"limit": limit}
        if event_type:
            params["type"] = event_type
        return self.api("GET", "/events", params=params)

    def run(self, choreography: Union["Choreography", str], input: Any = None, version: Optional[int] = None,
            run_id: Optional[str] = None) -> Result[RunHandle, AxoError]:
        """Starts a run (the newest version unless one is given). Given a
        ``Choreography``, it is saved first. The same ``run_id`` twice = one run."""
        from axo.choreography import Choreography

        if isinstance(choreography, Choreography):
            saved = self.save(choreography)
            if saved.is_err:
                return saved
            choreography = choreography.name
        body: Dict[str, Any] = {"input": input}
        if version is not None:
            body["version"] = version
        if run_id is not None:
            body["run_id"] = run_id
        sent = self.api("POST", f"/choreographies/{choreography}/runs", json=body)
        return sent.map(lambda b: RunHandle.from_answer(self, b))

    def run_of(self, run_id: str) -> RunHandle:
        """A handle on an existing run."""
        return RunHandle(self, run_id)

    def choreographies(self) -> Result[list, AxoError]:
        return self.api("GET", "/choreographies")

    def designer(self) -> Result[Dict[str, Any], AxoError]:
        """Every step / arrow / choreography setting with its description,
        the merge strategies and the condition comparisons."""
        return self.api("GET", "/choreographies/designer")

    def code_of(self, obj: Any) -> Result[Code, AxoError]:
        """What would be sent for this class or function (to look at it)."""
        return capture(obj)
