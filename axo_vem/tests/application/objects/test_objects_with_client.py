"""The axo client against the VEM's real object routes, with a fake
endpoint cluster that runs the classes through axo.runtime and sends back
the same events a real leader would (so the read models fill in as in
production)."""

import itertools

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from option import Ok

from axo import AxoClient
from axo.core.capture import Code, load
from axo.runtime import call_object, create_object, upgrade_object
from axo_shared import wire
from axo_shared.protocol import CommandResult

from axo_vem.application.compute import register_function as register_module
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.register_function import RegisterFunctionUseCase
from axo_vem.application.objects import service as service_module
from axo_vem.application.objects.service import ObjectService
from axo_vem.application.projector.dispatcher import apply_event
from axo_vem.application.projector.handlers import ProjectorHandlers
from axo_vem.domain.events import models
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.collections import ReadCollections
from axo_vem.infrastructure.database.mongo.consensus_repository import MongoConsensusRepository
from axo_vem.infrastructure.database.mongo.endpoint_repository import MongoEndpointRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository
from axo_vem.infrastructure.database.mongo.object_repository import (
    MongoActiveObjectRepository,
    MongoObjectClassRepository,
)
from axo_vem.infrastructure.transport.api.app import create_app
from axo_vem.infrastructure.transport.api.controllers import jobs as jobs_module

from tests.application.objects import counter_v1, counter_v2


class _Appender:
    def __init__(self, handlers):
        self._handlers = handlers

    def append_to_stream(self, stream_name, event_type, data):
        apply_event(self._handlers, event_type, data)


class FakeCluster:
    """What the endpoints would do: keep functions, run object operations
    one at a time (here: at once), answer JOB_RESULT."""

    def __init__(self, handlers, functions):
        self._handlers = handlers
        self._functions = functions                  # MongoFunctionRepository (FunctionRegistered)
        self.code = {}
        self.state = {}
        self.results = {}
        self.sent = []
        self._ids = (f"job-{i:05d}" for i in itertools.count())

    def send(self, rpc_uri, command, timeout):
        self.sent.append(command)
        env = command.envelope
        if command.operation == wire.FUNCTION_REGISTER:
            version = 1 + sum(1 for (fid, _) in self.code if fid == env["function_id"])
            self.code[(env["function_id"], version)] = (env["name"], env["code_format"], command.payload)
            self._emit(models.FUNCTION_REGISTERED, models.FunctionRegistered(
                endpoint_id="n0", function_id=env["function_id"], version=version, name=env["name"],
                runtime_spec=env.get("runtime_spec"), code_format=env["code_format"], code_hash=env.get("code_hash")))
            return Ok(CommandResult(ok=True, metadata={"function_id": env["function_id"], "version": version}))
        if command.operation == wire.ACTIVE_OBJECT_CALL:
            return Ok(self._run(env))
        if command.operation == wire.ACTIVE_OBJECT_STATE:
            values = self.state.get(env["object_id"])
            if values is None:
                return Ok(CommandResult(ok=False, error="no state", metadata={"error_name": "ACTIVE_OBJECT_NOT_FOUND"}))
            return Ok(CommandResult(ok=True, metadata={"object_id": env["object_id"], "attributes": {
                k: {"tier": "hot", "value": v} for k, v in values.items()}}))
        if command.operation == wire.ACTIVE_OBJECT_CLONE:
            self.state[env["object_id"]] = dict(self.state[env["source_id"]])
            self._emit(models.ACTIVE_OBJECT_CREATED, models.ActiveObjectCreated(
                object_id=env["object_id"], name=env["name"], class_name=env["class_name"],
                class_version=env["class_version"], cloned_from=env["source_id"]))
            return Ok(CommandResult(ok=True, metadata={"object_id": env["object_id"], "revision": 1}))
        if command.operation == wire.ACTIVE_OBJECT_DELETE:
            self.state.pop(env["object_id"], None)
            self._emit(models.ACTIVE_OBJECT_DELETED, models.ActiveObjectDeleted(object_id=env["object_id"]))
            return Ok(CommandResult(ok=True, metadata={"object_id": env["object_id"]}))
        if command.operation == wire.JOB_RESULT:
            return Ok(CommandResult(ok=True, metadata=self.results[env["job_id"]]))
        raise AssertionError(f"unexpected {command.operation}")

    def _run(self, env):
        job_id = env.get("job_id") or next(self._ids)
        name, fmt, data = self.code[(env["function_id"], env["class_version"])]
        cls = load(Code(name=name, kind="class", format="source" if fmt == "source" else "pickle", data=data)).unwrap()
        values = dict(self.state.get(env["object_id"], {}))
        method = env.get("method") or "run"
        try:
            if env["op"] == "create":
                outcome = create_object(cls, env["args"], env["kwargs"])
            elif env["op"] == "call":
                outcome = call_object(cls, method, env["params"], None, values)
            else:
                outcome = upgrade_object(cls, env["from_version"], None, values)
        except Exception as exc:
            self.results[job_id] = {"status": "FAILED", "result_ok": False, "error": repr(exc)}
            self._called(env, job_id, "failed_user")
            return CommandResult(ok=True, metadata={"job_id": job_id, "status": "QUEUED", "endpoint_id": "n0"})
        self.state[env["object_id"]] = {k: v for k, v in {**values, **outcome.values}.items()
                                        if k not in outcome.deleted}
        self.results[job_id] = {"status": "COMPLETED", "result_ok": True,
                                "output": {"value": outcome.result, "type": "json"}}
        self._called(env, job_id, "completed")
        if env["op"] == "create":
            self._emit(models.ACTIVE_OBJECT_CREATED, models.ActiveObjectCreated(
                object_id=env["object_id"], name=env["name"], class_name=env["class_name"],
                class_version=env["class_version"], created_by=env["started_by_id"]))
        if env["op"] == "upgrade":
            self._emit(models.ACTIVE_OBJECT_UPGRADED, models.ActiveObjectUpgraded(
                object_id=env["object_id"], from_version=env["from_version"], to_version=env["class_version"],
                revision=3))
        return CommandResult(ok=True, metadata={"job_id": job_id, "status": "QUEUED", "endpoint_id": "n0"})

    def _called(self, env, job_id, outcome):
        method = {"create": "__create__", "upgrade": "__upgrade__"}.get(env["op"], env.get("method") or "run")
        self._emit(models.ACTIVE_OBJECT_CALLED, models.ActiveObjectCalled(
            object_id=env["object_id"], job_id=job_id, trace_id=job_id, method=method,
            class_version=env["class_version"], outcome=outcome, revision=1, size_bytes=8))

    def _emit(self, event_type, event):
        apply_event(self._handlers, event_type, event.model_dump(mode="json"))


def build_vem(monkeypatch, cluster_cls=None, extend=None):
    """The VEM's object (and function) routes behind a front door, with a
    fake cluster. ``extend(db, parts)`` returns more create_app arguments
    (``parts``: functions, endpoints, picker, object_service, appender)."""
    db = mongomock.MongoClient()["test"]
    endpoints = MongoEndpointRepository(db["endpoints"])
    db["endpoints"].insert_one({"_id": "n0", "endpoint_id": "n0", "router_bind": "tcp://0.0.0.0:5555",
                                "status": "running", "created_at": "2026-10-17T00:00:00"})
    functions = MongoFunctionRepository(db["functions"])
    classes = MongoObjectClassRepository(db["object_classes"])
    objects = MongoActiveObjectRepository(db["active_objects"])
    activity = MongoActivityRepository(db["unified_activity"])
    handlers = ProjectorHandlers(
        activity_recorder=activity, endpoint_repository=endpoints, function_repository=functions,
        consensus_recorder=MongoConsensusRepository(db["consensus"]), job_repository=MongoJobRepository(db["jobs"]),
        choreography_repository=MongoChoreographyRepository(db["choreographies"]),
        object_class_repository=classes, active_object_repository=objects,
    )
    cluster = (cluster_cls or FakeCluster)(handlers, functions)
    for module in (register_module, service_module, jobs_module):
        monkeypatch.setattr(module, "send_command", cluster.send)
    picker = EndpointPicker(endpoints)
    register = RegisterFunctionUseCase("ve1", picker, 1.0, function_repository=functions)
    appender = _Appender(handlers)
    service = ObjectService("ve1", picker, register, classes, objects, appender, 1.0)
    extra = extend(db, {"functions": functions, "endpoints": endpoints, "picker": picker,
                        "object_service": service, "appender": appender}) if extend else {}
    vem_app = create_app(
        collections=ReadCollections(endpoints=db["endpoints"], functions=db["functions"], consensus=db["consensus"]),
        activity_repository=activity, job_repository=MongoJobRepository(db["jobs"]), object_service=service,
        # /functions is mounted only with all four; these tests only register.
        register_function_use_case=register, delete_function_use_case=object(),
        update_function_use_case=object(), purge_function_version_use_case=object(),
        current_user_dependency=lambda: ActingUser(key="ana"),
        **extra,
    )
    front_door = FastAPI()
    front_door.mount("/ves/ve1/api", vem_app)
    http = TestClient(front_door)
    client = AxoClient(key="axo_k_s", ve="ve1", base_url="http://testserver", transport=http._transport)
    return client, cluster, db


@pytest.fixture
def vem(monkeypatch):
    return build_vem(monkeypatch)


def test_an_object_lives_through_the_client(vem):
    client, cluster, db = vem
    counter = client.create(counter_v1.Counter, "visits", 5).unwrap()
    assert counter.created.result().unwrap() is None
    assert counter.run(by=2).unwrap().result().unwrap() == 7
    assert counter.call("run", by=3).unwrap().result().unwrap() == 10
    failed = counter.explode().unwrap().result()
    assert failed.is_err and "boom" in failed.unwrap_err().message

    info = counter.info().unwrap()
    assert info["status"] == "ready" and info["class_version"] == 1 and info["calls"] == 4
    assert counter.state().unwrap()["attributes"]["count"]["value"] == 10
    history = counter.history().unwrap()
    assert [e["meta"]["method"] for e in history if e["event_type"] == models.ACTIVE_OBJECT_CALLED][:1] == ["explode"]

    copy = client.clone("visits", "visits-copy").unwrap()
    assert copy.state().unwrap()["attributes"]["count"]["value"] == 10
    assert sorted(o["name"] for o in client.objects().unwrap()) == ["visits", "visits-copy"]

    client.register_class(counter_v2.Counter).unwrap()
    assert counter.upgrade().unwrap().result().is_ok
    assert counter.info().unwrap()["class_version"] == 2
    assert counter.state().unwrap()["attributes"] == {"total": {"tier": "hot", "value": 100}}

    assert counter.delete().is_ok
    assert counter.info().unwrap_err().name == "ACTIVE_OBJECT_NOT_FOUND"


def test_the_same_class_code_is_registered_once(vem):
    client, cluster, db = vem
    first = client.register_class(counter_v1.Counter).unwrap()
    fresh = AxoClient(key="axo_k_s", ve="ve1", base_url="http://testserver", transport=client._http._transport)
    again = fresh.register_class(counter_v1.Counter).unwrap()                 # no memory of the first
    assert first == again
    assert sum(1 for c in cluster.sent if c.operation == wire.FUNCTION_REGISTER) == 1
    registered = next(c for c in cluster.sent if c.operation == wire.FUNCTION_REGISTER)
    assert registered.envelope["runtime_spec"]["call_style"] == "object"
    saved = db["object_classes"].find_one()
    assert sorted(m["name"] for m in saved["methods"]) == ["explode", "run"]
    assert saved["has_upgrade"] is False and saved["created_by"] == "ana"


def test_names_are_unique_and_a_failed_create_frees_the_name(vem):
    client, cluster, db = vem
    assert client.create(counter_v1.Counter, "c1").is_ok
    taken = client.create(counter_v1.Counter, "c1")
    assert taken.unwrap_err().name == "ACTIVE_OBJECT_EXISTS"
    broken = client.create(counter_v1.Counter, "c2", "not-a-number")          # __init__ ok, run would fail
    assert broken.is_ok
    bad = client.create(counter_v1.Counter, "c3", 1, 2, 3)                   # __init__ raises TypeError
    assert bad.unwrap().created.result().is_err
    assert client.object("c3").info().unwrap_err().name == "ACTIVE_OBJECT_NOT_FOUND"
    assert client.create(counter_v1.Counter, "c3").is_ok                      # the name is free again


def test_unknown_methods_and_classes_are_refused_before_running(vem):
    client, cluster, db = vem
    client.create(counter_v1.Counter, "c1").unwrap()
    sent_before = len(cluster.sent)
    missing = client.object("c1").call("nope")
    assert missing.unwrap_err().name == "ACTIVE_OBJECT_METHOD_NOT_FOUND"
    assert client.object("ghost").call("run").unwrap_err().name == "ACTIVE_OBJECT_NOT_FOUND"
    no_class = client.api("POST", "/objects", json={"name": "x1", "class_name": "Nope"})
    assert no_class.unwrap_err().name == "ACTIVE_OBJECT_CLASS_NOT_FOUND"
    assert len(cluster.sent) == sent_before
    assert client.object("c1").upgrade().unwrap_err().name == "ACTIVE_OBJECT_STATE_CONFLICT"   # no newer version


def test_function_code_is_registered_once_per_hash(vem):
    from axo import axo_function

    @axo_function(name="double")
    def double(x):
        return 2 * x

    client, cluster, db = vem
    first = client.register(double).unwrap()
    fresh = AxoClient(key="axo_k_s", ve="ve1", base_url="http://testserver", transport=client._http._transport)
    assert fresh.register(double).unwrap() == first
    sent = [c for c in cluster.sent if c.operation == wire.FUNCTION_REGISTER]
    assert len(sent) == 1
    assert sent[0].envelope["runtime_spec"]["call_style"] == "kwargs"
    assert sent[0].envelope["code_format"] in ("source", "cloudpickle") and sent[0].envelope["code_hash"]


def test_object_changes_go_out_live():
    from axo_vem.application.projector import object_handler

    class _Broadcaster:
        def __init__(self):
            self.sent = []

        def broadcast(self, topic, message):
            self.sent.append((topic, message["event_type"]))

    broadcaster = _Broadcaster()
    objects = MongoActiveObjectRepository(mongomock.MongoClient()["t"]["active_objects"])
    called = models.ActiveObjectCalled(object_id="obj-1", job_id="j", trace_id="j", method="run",
                                       class_version=1, outcome="completed", revision=2, size_bytes=8)
    object_handler.apply(None, objects, models.ACTIVE_OBJECT_CALLED, called.model_dump(mode="json"), broadcaster)
    object_handler.apply(None, objects, models.ACTIVE_OBJECT_STATE_SIZES_REPORTED, {"objects": {"obj-1": 9}},
                         broadcaster)
    assert broadcaster.sent == [("objects", models.ACTIVE_OBJECT_CALLED), ("objects:obj-1", models.ACTIVE_OBJECT_CALLED)]


def test_plan_limits_go_with_every_object_operation_and_cap_kept_revisions(monkeypatch):
    from axo_vem.domain.virtual_environment.settings import PlanLimits

    plan = {"limits": PlanLimits(object_max_state_bytes=1000, object_hot_max_bytes=64, object_keep_revisions=3,
                                 object_max_keep_revisions=5)}

    def extend(db, parts):
        parts["object_service"]._limits_fn = lambda: plan["limits"]
        return {}

    client, cluster, db = build_vem(monkeypatch, extend=extend)
    too_many = client.api("POST", "/objects", json={"name": "c", "class_name": "Counter", "keep_revisions": 9})
    assert too_many.is_err and "at most 5 past revisions" in too_many.unwrap_err().message
    counter = client.create(counter_v1.Counter, "visits", 5).unwrap()
    counter.created.result().unwrap()
    assert client.api("PATCH", "/objects/visits", json={"keep_revisions": 4}).unwrap()["keep_revisions"] == 4
    assert client.api("PATCH", "/objects/visits", json={"keep_revisions": 6}).is_err
    counter.run(by=1).unwrap().result().unwrap()
    call = [c for c in cluster.sent if c.operation == wire.ACTIVE_OBJECT_CALL][-1]
    assert call.envelope["keep_revisions"] == 4
    assert call.envelope["state_limits"] == {"max_state_bytes": 1000, "hot_max_bytes": 64, "keep_revisions": 3,
                                             "max_keep_revisions": 5}
    # A plan change reaches the next call, with no endpoint restart.
    plan["limits"] = PlanLimits(object_max_state_bytes=50, object_max_keep_revisions=2)
    client.clone("visits", "visits-2").unwrap()
    clone = [c for c in cluster.sent if c.operation == wire.ACTIVE_OBJECT_CLONE][-1]
    assert clone.envelope["state_limits"]["max_state_bytes"] == 50 and clone.envelope["keep_revisions"] == 4
