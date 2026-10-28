"""The VEM's choreography side for the dev UI server: the REAL choreography
service, engine, events and "who sees" code of axo_vem (imported from
../axo_vem) on mongomock, behind the real routes. Each step's job is run by
the dev VEM's fake functions / objects (fake_jobs / fake_objects), so step
jobs show in the Jobs list grouped under their run, take a few seconds like
real ones, and sometimes fail. Bucket steps read and write the dev
platform's buckets (FakeStorage)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

_VEM = Path(__file__).resolve().parents[2] / "axo_vem"
if str(_VEM) not in sys.path:
    sys.path.insert(0, str(_VEM))

import httpx  # noqa: E402
import mongomock  # noqa: E402
from fastapi import FastAPI, Header  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from axo_vem.application.choreography.engine import ChoreographyEngine  # noqa: E402
from axo_vem.application.choreography.events import EventsService  # noqa: E402
from axo_vem.application.choreography.jobs import COMPLETED, FAILED, PENDING, JobStatus, StepJobs  # noqa: E402
from axo_vem.application.choreography.service import ChoreographyService, StepCatalog  # noqa: E402
from axo_vem.application.visibility import VisibilityService  # noqa: E402
from axo_vem.domain.errors import NotFoundError  # noqa: E402
from axo_vem.domain.events.models import (  # noqa: E402
    ChoreographyEdge, ChoreographyGraph, ChoreographyNode, ChoreographySettings, ChoreographyTrigger, EdgeCondition,
)
from axo_vem.infrastructure.auth.service_token import ActingUser  # noqa: E402
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository  # noqa: E402
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository  # noqa: E402
from axo_vem.infrastructure.database.mongo.choreography_run_repository import (  # noqa: E402
    MongoChoreographyRunRepository,
)
from axo_vem.infrastructure.transport.api.controllers import (  # noqa: E402
    choreographies as choreography_routes, custom_events as event_routes, visibility as visibility_routes,
)
from axo_vem.infrastructure.transport.api.errors import register_exception_handlers  # noqa: E402

ACTIVE = ("QUEUED", "RUNNING", "WAITING_RETRY")
PATHS = ("/choreographies", "/choreography-", "/events", "/event-types", "/visibility", "/results")


def handles(path: str) -> bool:
    return path.startswith(PATHS) or path == "/bucket-events"


def _acting_user(
    x_acting_user: Optional[str] = Header(default=None, alias="X-Acting-User"),
    x_acting_role: Optional[str] = Header(default=None, alias="X-Acting-Role"),
    x_acting_name: Optional[str] = Header(default=None, alias="X-Acting-Name"),
    x_acting_key: Optional[str] = Header(default=None, alias="X-Acting-Key"),
    x_acting_key_name: Optional[str] = Header(default=None, alias="X-Acting-Key-Name"),
    x_acting_teams: Optional[str] = Header(default=None, alias="X-Acting-Teams"),
) -> ActingUser:
    return ActingUser(key=x_acting_user or "dev", role=(x_acting_role or None) and x_acting_role.lower(),
                      name=x_acting_name, api_key_id=x_acting_key, api_key_name=x_acting_key_name,
                      teams=frozenset(t.strip() for t in (x_acting_teams or "").split(",") if t.strip()))


class _Functions:
    """What StepCatalog reads (FunctionRepository.get / latest), from the
    fake functions of one VE."""

    def __init__(self, fve: Dict[str, Any]) -> None:
        self._fve = fve

    @staticmethod
    def _view(doc: Dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(function_id=doc["function_id"], version=doc["version"], name=doc["name"],
                               params_schema=doc["params_schema"], deleted_at=doc["deleted_at"],
                               runtime_spec=doc["runtime_spec"])

    def get(self, function_id: str, version: int):
        doc = self._fve["functions"].get((function_id, version))
        return self._view(doc) if doc is not None else None

    def latest(self, function_id: str):
        docs = [d for (f, _v), d in self._fve["functions"].items() if f == function_id and not d["deleted_at"]]
        return self._view(max(docs, key=lambda d: d["version"])) if docs else None


class _Publisher:
    """Kurrent stand-in: each event is one history row of the dev VEM."""

    def __init__(self, add_row: Callable[[str, Dict[str, Any], Optional[str]], None]) -> None:
        self._add = add_row

    def append_to_stream(self, stream: str, event_type: str, data: Dict[str, Any]) -> None:
        self._add(event_type, dict(data), data.get("user_id"))


class _Buckets:
    """The engine's bucket storage, on the dev platform's buckets."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    def _key(self, bucket: str) -> str:
        try:
            return self._platform.buckets.resolve(bucket)["_id"]
        except Exception:
            raise NotFoundError(f"no bucket {bucket}", error_name="BUCKET_NOT_FOUND")

    def list_objects(self, user_id: str, bucket: str) -> List[Dict[str, Any]]:
        key, storage = self._key(bucket), self._platform.storage
        return [{"name": r["name"], "version": r["version"], "format": r.get("format") or "raw",
                 "size": r["total_size"], "state": r["state"], "source": r.get("source")}
                for (b, _n), versions in storage.files.items() if b == key for r in versions.values()]

    def upload(self, user_id: str, bucket: str, key: str, data: bytes, format: str = "raw", version=None,
               source=None):
        record = self._platform.storage.upload(user_id, self._key(bucket), key, data)
        stored = self._platform.storage.files[(record["bucket"], key)][record["version"]]
        stored.update(format=format, source=source)
        return {"name": key, "version": record["version"], "format": format, "size": len(data), "source": source}


class _StepJobs(StepJobs):
    """A step's job = a job of the dev VEM's fake functions (an object step:
    a fake object call), re-keyed to the engine's job id."""

    def __init__(self, bridge: "_VeChoreographies") -> None:
        self._b = bridge

    def _rekey(self, job: Dict[str, Any], job_id: str) -> None:
        fve = self._b.fve
        old = job["job_id"]
        fve["jobs"][job_id] = fve["jobs"].pop(old)
        fve["attempts"][job_id] = fve["attempts"].pop(old)
        job["job_id"] = job_id
        pending = self._b.ove["pending"]
        if old in pending:
            pending[job_id] = pending.pop(old)

    def _stamp(self, job, acting_user_id, choreography) -> None:
        job.update(started_by_type="choreography", started_by_id=choreography["choreography_id"],
                   started_by_name=choreography["choreography_name"], choreography=choreography,
                   acting_user_id=acting_user_id)
        self._b.note_run(choreography["run_id"])

    def submit_function(self, *, function_id, version, params, event, output_buckets, job_id, acting_user_id,
                        choreography_id, max_wait, step_ref=None, attempts=None, keep_result=None,
                        choreography=None):
        with self._b.lock:
            fve = self._b.fve
            if job_id in fve["jobs"]:
                return {"job_id": job_id, "endpoint_id": fve["jobs"][job_id]["endpoint_id"], "status": "DUPLICATE"}
            if (function_id, version) not in fve["functions"]:
                raise NotFoundError(f"no function {function_id} v{version}", error_name="FUNCTION_NOT_FOUND")
            job = self._b.functions._new_job(fve, self._b.ve_id, function_id, version, dict(params or {}),
                                             acting_user_id, "choreography", time.time(), keep=keep_result,
                                             attempts=attempts)
            job["_plan"]["fail"] = job["_plan"]["fail"] or self._b.rng_fail()
            self._rekey(job, job_id)
            self._stamp(job, acting_user_id, choreography or {})
            return {"job_id": job_id, "endpoint_id": job["endpoint_id"]}

    def call_object(self, *, name, method, params, event, output_buckets, job_id, acting_user_id,
                    choreography_id, max_wait, step_ref=None, keep_result=None, choreography=None):
        with self._b.lock:
            fve, ove = self._b.fve, self._b.ove
            if job_id in fve["jobs"]:
                return {"job_id": job_id, "endpoint_id": fve["jobs"][job_id]["endpoint_id"], "status": "DUPLICATE"}
            obj = ove["objects"].get(name)
            if obj is None or obj.get("status") != "ready":
                raise NotFoundError(f"no object {name}", error_name="ACTIVE_OBJECT_NOT_FOUND")
            job = self._b.objects._job(ove, fve, self._b.ve_id, obj, method or "run", dict(params or {}),
                                       acting_user_id, time.time(), keep=keep_result)
            self._rekey(job, job_id)
            self._stamp(job, acting_user_id, choreography or {})
            return {"job_id": job_id, "endpoint_id": job["endpoint_id"]}

    def status(self, endpoint_id: str, job_id: str) -> JobStatus:
        with self._b.lock:
            self._b.advance()
            job = self._b.fve["jobs"].get(job_id)
            if job is None:
                return JobStatus("UNKNOWN", error="job not found")
            if job["status"] in ACTIVE:
                return JobStatus(PENDING)
            if job["status"] == "COMPLETED":
                return JobStatus(COMPLETED, output=self._b.fve["results"].get(job_id), duration_ms=job["duration_ms"])
            error = job.get("error") or {}
            return JobStatus(FAILED, error=error.get("message") or "the job failed",
                             error_name=error.get("name"), duration_ms=job.get("duration_ms"))

    def cancel(self, endpoint_id: str, job_id: str) -> None:
        with self._b.lock:
            job = self._b.fve["jobs"].get(job_id)
            if job is not None and job["status"] in ACTIVE:
                now = time.time()
                job.update(status="CANCELLED", outcome="cancelled", _plan=None, finished_at=_iso(now),
                           error={"name": "JOB_CANCELLED", "code": 3008, "message": "cancelled with its run",
                                  "part": "axo_vem.jobs", "error_type": None, "fault": "cancelled"})


class _Results:
    """The engine's kept results (JobsService.result / kept / result_ref)."""

    def __init__(self, bridge: "_VeChoreographies") -> None:
        self._b = bridge

    def result(self, job_id: str) -> Any:
        with self._b.lock:
            if job_id not in self._b.fve["results"]:
                raise NotFoundError("the job's result is not kept", error_name="JOB_RESULT_NOT_KEPT")
            return self._b.fve["results"][job_id]

    def kept(self, job_id: str) -> bool:
        with self._b.lock:
            return job_id in self._b.fve["results"]

    def result_ref(self, job_id: str, wait_seconds: float = 30.0) -> Optional[Dict[str, Any]]:
        return {"bucket": f"axo/ve-{self._b.ve_id}-results", "name": f"{job_id}.json", "version": 1,
                "format": "json"}


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e6):06d}Z"


class _VeChoreographies:
    """One VE's choreography side: service, engine, events, visibility and
    the real routes (a FastAPI app, called in process)."""

    def __init__(self, ve_id: str, fve: Dict[str, Any], ove: Dict[str, Any], functions: Any, objects: Any,
                 platform: Any, lock: threading.RLock, add_row: Callable, owner: str) -> None:
        self.ve_id, self.fve, self.ove = ve_id, fve, ove
        self.functions, self.objects = functions, objects
        self.lock = lock
        self._runs_seen: set = set()
        self._fails = 0
        self.db = db = mongomock.MongoClient()[f"vem-{ve_id}"]
        self.repository = MongoChoreographyRepository(db["choreographies"], db["choreography_versions"],
                                                      db["choreography_names"])
        self.runs = MongoChoreographyRunRepository(db["choreography_runs"])
        self.activity = MongoActivityRepository(db["unified_activity"])
        publisher = _Publisher(add_row)
        catalog = StepCatalog(_Functions(fve), self._object, default_version=self._default_version)
        self.service = ChoreographyService(self.repository, self.runs, catalog, publisher, ve_id)
        self.engine = ChoreographyEngine(
            self.service, self.repository, self.runs, catalog, _StepJobs(self), publisher,
            bucket_storage=_Buckets(platform), results=_Results(self), broadcaster=None, metrics=None,
            virtual_environment_id=ve_id, step_timeout_seconds=600.0, poll_interval_seconds=0.5, workers=8)
        self.visibility = VisibilityService(db["result_visibility"], publisher, creator_of=self._creator,
                                            virtual_environment_id=ve_id)
        self.events = EventsService(db["events"], db["event_types"], self.repository, self.engine.on_event, ve_id)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(choreography_routes.build_router(self.service, self.engine, _acting_user,
                                                            visibility=self.visibility, events=self.events))
        app.include_router(event_routes.build_router(self.events, _acting_user))
        app.include_router(visibility_routes.build_router(self.visibility, _acting_user, resolve=self._resolve))
        self.client = TestClient(app, raise_server_exceptions=False)
        self.owner = owner

    # ── catalog ──────────────────────────────────────────────────────────────
    def _object(self, name: str) -> Optional[tuple]:
        with self.lock:
            obj = self.ove["objects"].get(name)
            if obj is None:
                return None
            return dict(obj), self.ove["classes"].get((obj["class_name"], obj["class_version"]))

    def _default_version(self, function_id: str) -> Optional[int]:
        with self.lock:
            versions = [v for (f, v), d in self.fve["functions"].items() if f == function_id and not d["deleted_at"]]
            return self.fve["defaults"].get(function_id) or (max(versions) if versions else None)

    def _creator(self, kind: str, ref: str) -> Optional[str]:
        if kind == "choreography":
            found = self.repository.get(ref)
            return found.owner_user_id if found is not None else None
        with self.lock:
            if kind == "function":
                docs = [d for (f, _v), d in sorted(self.fve["functions"].items()) if f == ref]
                return docs[0]["registered_by"] if docs else None
            obj = self.ove["objects"].get(ref)
            return (obj or {}).get("created_by")

    def _resolve(self, kind: str, ref: str) -> str:
        if kind == "choreography":
            found = self.repository.get(ref) or self.repository.get_by_name(ref)
            return found.choreography_id if found is not None else ref
        return ref

    # ── jobs ─────────────────────────────────────────────────────────────────
    def rng_fail(self) -> bool:
        """About one step in twelve fails (its code), so failures show up."""
        self._fails += 1
        return self._fails % 12 == 5

    def advance(self) -> None:
        self.functions._advance(self.fve, self.ve_id)
        self.objects._advance(self.ove, self.ve_id, self.fve)

    def note_run(self, run_id: str) -> None:
        self._runs_seen.add(run_id)

    def sync_runs(self) -> None:
        """The jobs list groups a run's step jobs under the run's summary."""
        for run_id in list(self._runs_seen):
            run = self.runs.get(run_id)
            if run is None:
                continue
            self.fve["runs"][run_id] = {
                "run_id": run.run_id, "choreography_id": run.choreography_id,
                "choreography_name": run.choreography_name, "version": run.version, "status": run.status,
                "started_by_type": run.started_by_type, "started_by_id": run.started_by_id,
                "queued_at": run.queued_at, "started_at": run.started_at, "finished_at": run.finished_at,
                "duration_ms": run.duration_ms, "steps_total": len(run.steps), "parent": run.parent}

    def results_page(self, who: str, role: str) -> Dict[str, Any]:
        """GET /results: the results bucket as this person may browse it."""
        everyone = role in ("admin", "owner", "")
        rows = []
        for job_id, value in self.fve["results"].items():
            job = self.fve["jobs"].get(job_id) or {}
            if not everyone and job.get("acting_user_id") != who:
                continue
            rows.append({"job_id": job_id, "name": f"{job_id}.json", "bucket": f"axo/ve-{self.ve_id}-results",
                         "size": len(str(value)), "saved_at": job.get("finished_at"),
                         "function_name": job.get("function_name"), "object_name": job.get("object_name"),
                         "choreography": job.get("choreography"), "keep_until": job.get("keep_until")})
        rows.sort(key=lambda r: r["saved_at"] or "", reverse=True)
        return {"results": rows[:100], "files": len(rows), "bytes": sum(r["size"] for r in rows), "next": None}


class FakeChoreographies:
    """Per VE: the real choreography routes. ``route`` answers a dev VEM
    call for /choreographies..., /events..., /event-types..., /visibility,
    /results; everything else is the dev VEM's own."""

    def __init__(self, functions: Any, objects: Any, lock: threading.RLock) -> None:
        self._functions = functions
        self._objects = objects
        self._lock = lock
        self._ves: Dict[str, _VeChoreographies] = {}
        self.platform: Any = None                 # DevPlatform sets it (buckets + storage)
        self.add_row: Callable[[str, str, Dict[str, Any], Optional[str]], None] = lambda *a: None

    def ve(self, ve_id: str, fve: Dict[str, Any], ove: Dict[str, Any], owner: str) -> _VeChoreographies:
        found = self._ves.get(ve_id)
        if found is None:
            found = self._ves[ve_id] = _VeChoreographies(
                ve_id, fve, ove, self._functions, self._objects, self.platform, self._lock,
                lambda kind, meta, user: self.add_row(ve_id, kind, meta, user), owner)
            _seed(found, owner)
        return found

    def sync(self) -> None:
        for ve in self._ves.values():
            ve.sync_runs()

    def route(self, ve: _VeChoreographies, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        headers = {k: v for k, v in request.headers.items() if k.lower().startswith("x-acting-")}
        if request.method == "GET" and path == "/results":
            return httpx.Response(200, json=ve.results_page(headers.get("x-acting-user", ""),
                                                            (headers.get("x-acting-role") or "").lower()))
        answer = ve.client.request(request.method, path, params=dict(request.url.params),
                                   content=request.content or None,
                                   headers={**headers, "content-type": "application/json"})
        return httpx.Response(answer.status_code, content=answer.content,
                              headers={"content-type": answer.headers.get("content-type", "application/json")})


# ── sample choreographies ────────────────────────────────────────────────────
def _edge(i: int, source: str, target: str, **kw) -> ChoreographyEdge:
    return ChoreographyEdge(edge_id=f"e{i}", source_node_id=source, target_node_id=target, **kw)


def _seed(ve: _VeChoreographies, owner: str) -> None:
    """Choreographies on the ML VE (one calls another as a step) and one on
    the ETL VE, each with a few runs started at once (they finish over the
    next minute)."""
    fids = {d["name"]: f for (f, _v), d in ve.fve["functions"].items()}
    samples = []
    if {"train_model", "predict", "build_features"} <= set(fids):
        samples.append(("nightly-training", "Builds features from the newest visits, trains, then scores.",
                        ChoreographyGraph(
                            nodes=[
                                ChoreographyNode(node_id="visits", kind="bucket", bucket_name="demo/ml-data",
                                                 label="Visits", selected_items=[{"name": "2026-10.csv"}]),
                                ChoreographyNode(node_id="features", kind="function",
                                                 function_id=fids["build_features"], label="Build features",
                                                 params={"source": "visits", "window_days": 30}),
                                ChoreographyNode(node_id="train", kind="function", function_id=fids["train_model"],
                                                 label="Train", params={"dataset": "2026-10.csv", "epochs": 12},
                                                 max_attempts=2, retry_delay_seconds=2.0),
                                ChoreographyNode(node_id="score", kind="function", function_id=fids["predict"],
                                                 label="Score", params={"features": [0.2, 0.4, 0.9]}),
                                ChoreographyNode(node_id="small", kind="function", function_id=fids["predict"],
                                                 label="Too few rows", params={"features": [0.0]}),
                                ChoreographyNode(node_id="alert", kind="function", function_id=fids["predict"],
                                                 label="Alert", params={"features": [1.0]}),
                                ChoreographyNode(node_id="models", kind="bucket", bucket_name="demo/ml-models",
                                                 label="Scores", key_pattern="scores/{run_id}.json"),
                            ],
                            edges=[_edge(0, "visits", "features"),
                                   _edge(1, "features", "train", condition=EdgeCondition.model_validate(
                                       {"rules": [{"field": "rows", "op": ">", "value": 1000}]})),
                                   _edge(2, "features", "small", otherwise=True),
                                   _edge(3, "train", "score"), _edge(4, "train", "alert", on_failure=True),
                                   _edge(5, "score", "models")],
                            triggers=[ChoreographyTrigger(event_type="file.written", bucket="demo/ml-data",
                                                          prefix="raw/", targets=["visits"])]),
                        ChoreographySettings(max_parallel_runs=2)))
    if "ShoppingCart" in {o["class_name"] for o in ve.ove["objects"].values()} and "predict" in fids:
        cart = next(o["name"] for o in ve.ove["objects"].values() if o["class_name"] == "ShoppingCart")
        samples.append(("order-paid", "When an order is paid: check it out and score the basket.",
                        ChoreographyGraph(
                            nodes=[ChoreographyNode(node_id="checkout", kind="object", object_name=cart,
                                                    method="checkout", label="Check out"),
                                   ChoreographyNode(node_id="score", kind="function", function_id=fids["predict"],
                                                    label="Score basket", params={"features": [0.5]})],
                            edges=[_edge(0, "checkout", "score")],
                            triggers=[ChoreographyTrigger(
                                event_type="order.paid",
                                condition=EdgeCondition.model_validate(
                                    {"rules": [{"field": "total", "op": ">", "value": 100}]}))]),
                        None))
    if {"ingest_csv", "clean_rows"} <= set(fids):
        samples.append(("ingest-uploads", "Reads each uploaded CSV, then cleans its rows.",
                        ChoreographyGraph(
                            nodes=[ChoreographyNode(node_id="uploads", kind="bucket", bucket_name="demo/ml-data",
                                                    label="Uploads", selected_items=[{"name": "2026-10.csv"}]),
                                   ChoreographyNode(node_id="ingest", kind="function", function_id=fids["ingest_csv"],
                                                    label="Ingest"),
                                   ChoreographyNode(node_id="clean", kind="function", function_id=fids["clean_rows"],
                                                    label="Clean")],
                            edges=[_edge(0, "uploads", "ingest"), _edge(1, "ingest", "clean")]),
                        None))
    if {"build_features", "predict"} <= set(fids):
        # an inner one, and one that runs it as a step (saved after it, below)
        samples.append(("score-visits", "Builds the features of some visits and scores them.",
                        ChoreographyGraph(
                            nodes=[ChoreographyNode(node_id="features", kind="function", function_id=fids["build_features"],
                                                    label="Build features", params={"source": "visits"}),
                                   ChoreographyNode(node_id="score", kind="function", function_id=fids["predict"],
                                                    label="Score", params={"features": [0.3, 0.6]})],
                            edges=[_edge(0, "features", "score")]),
                        None))
        samples.append(("weekly-report", "Every week: scores the visits (with score-visits), then writes the report.",
                        lambda saved_ids: ChoreographyGraph(
                            nodes=[ChoreographyNode(node_id="prepare", kind="function", function_id=fids["build_features"],
                                                    label="Prepare", params={"source": "visits", "window_days": 7}),
                                   ChoreographyNode(node_id="scores", kind="choreography",
                                                    choreography_id=saved_ids["score-visits"],
                                                    choreography_name="score-visits", label="Score the visits"),
                                   ChoreographyNode(node_id="report", kind="function", function_id=fids["predict"],
                                                    label="Report", params={"features": [1.0]})],
                            edges=[_edge(0, "prepare", "scores"), _edge(1, "scores", "report")]),
                        None))
    saved_ids: Dict[str, str] = {}
    for name, description, graph, settings in samples:
        try:
            if callable(graph):
                graph = graph(saved_ids)
            saved = ve.service.create(user_id=owner, name=name, graph=graph, settings=settings, description=description)
            saved_ids[name] = saved["choreography_id"]
        except Exception as exc:                       # keep the dev server up; say why
            print(f"[dev] sample choreography {name} not saved: {exc}")
            continue
        if name != "order-paid":
            for _ in range(3):
                try:
                    ve.engine.start(saved["choreography_id"], user_id=owner)
                except Exception as exc:
                    print(f"[dev] sample run of {name} not started: {exc}")
    try:
        ve.events.save_type(user_id=owner, event_type="order.paid",
                            description="A customer paid an order (sent by the shop).",
                            fields=[{"name": "order_id", "type": "string"}, {"name": "total", "type": "number"}])
        sender = {"user_id": owner, "started_by_type": "user", "started_by_id": owner, "started_by_name": "Dana Demo"}
        for order_id, total in (("ord-1001", 240.5), ("ord-1002", 35.0)):
            ve.events.emit(sender=sender, event_type="order.paid", data={"order_id": order_id, "total": total},
                           event_id=order_id)
    except Exception as exc:
        print(f"[dev] sample events not sent: {exc}")
