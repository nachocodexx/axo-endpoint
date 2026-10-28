"""The axo client's Choreography builder against the VEM's real routes and
engine, with a fake endpoint cluster that runs the functions through
axo.runtime (so parameters, defaults and the event arrive as they would)."""

import pytest
from option import Ok

from axo import AxoClient, Choreography, axo_function
from axo.core.capture import Code, load
from axo.core.context import LocalContext
from axo.runtime import call_function
from axo_shared import wire
from axo_shared.metrics import MetricAggregator
from axo_shared.protocol import CommandResult

from axo_vem.application.choreography import jobs as jobs_module
from axo_vem.application.choreography.engine import ChoreographyEngine
from axo_vem.application.choreography.jobs import EndpointStepJobs
from axo_vem.application.choreography.service import ChoreographyService, StepCatalog
from axo_vem.application.compute import submit_function_job as submit_module
from axo_vem.application.compute.submit_function_job import SubmitFunctionJobUseCase
from axo_vem.domain.events import models
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository

from tests.application.choreography.fakes import FakeBucketStorage
from tests.application.objects.test_objects_with_client import FakeCluster, build_vem
from tests.application.objects import counter_v1


class Cluster(FakeCluster):
    """Also runs function jobs (JOB_SUBMIT) as the endpoint would."""

    def send(self, rpc_uri, command, timeout):
        env = command.envelope
        if command.operation == wire.JOB_SUBMIT:
            self.sent.append(command)
            name, fmt, data = self.code[(env["function_id"], env["function_version"])]
            fn = load(Code(name=name, kind="function", format="source" if fmt == "source" else "pickle",
                           data=data)).unwrap()
            ctx = LocalContext(job_id=env["job_id"], event=env.get("event"),
                               output_buckets=env.get("output_buckets") or [])
            try:
                value = call_function(fn, env["params"], ctx)
                self.results[env["job_id"]] = {"status": "COMPLETED", "result_ok": True,
                                               "output": {"value": value, "type": "json"}}
            except Exception as exc:
                self.results[env["job_id"]] = {"status": "FAILED", "result_ok": False, "error": repr(exc)}
            return Ok(CommandResult(ok=True, metadata={"job_id": env["job_id"], "status": "QUEUED"}))
        if command.operation == wire.FUNCTION_REGISTER:
            env = command.envelope
            version = 1 + sum(1 for (fid, _) in self.code if fid == env["function_id"])
            self.code[(env["function_id"], version)] = (env["name"], env["code_format"], command.payload)
            self._emit(models.FUNCTION_REGISTERED, models.FunctionRegistered(   # as the endpoint's bridge sends it
                endpoint_id="n0", function_id=env["function_id"], version=version, name=env["name"],
                runtime_spec=env.get("runtime_spec"), code_format=env["code_format"],
                code_hash=env.get("code_hash"), params_schema=env.get("params_schema")))
            return Ok(CommandResult(ok=True, metadata={"function_id": env["function_id"], "version": version}))
        if command.operation == wire.JOB_CANCEL:
            return Ok(CommandResult(ok=True))
        return super().send(rpc_uri, command, timeout)


@axo_function(name="count_rows")
def count_rows(day, ctx):
    return {"rows": len(day) * 10, "day": day, "run": ctx.event["run"]["choreography"]}


@axo_function(name="report")
def report(rows, unit="rows", ctx=None):
    return {"line": f"{rows} {unit}", "from": sorted(ctx.event["previous"])}


@pytest.fixture
def vem(monkeypatch):
    engines = []

    def choreographies(db, parts):
        repository = MongoChoreographyRepository(db["choreographies"], db["choreography_versions"],
                                                 db["choreography_names"])
        runs = MongoChoreographyRunRepository(db["choreography_runs"])
        catalog = StepCatalog(parts["functions"], parts["object_service"].lookup)
        service = ChoreographyService(repository, runs, catalog, parts["appender"], "ve1")
        submit = SubmitFunctionJobUseCase(parts["functions"], parts["picker"], 1.0)
        engine = ChoreographyEngine(
            service, repository, runs, catalog,
            EndpointStepJobs(submit, parts["object_service"], parts["endpoints"], 1.0), parts["appender"],
            bucket_storage=FakeBucketStorage(),
            metrics=MetricAggregator(width_s=10), virtual_environment_id="ve1", poll_interval_seconds=0.005,
        )
        engines.append(engine)
        return {"choreography_service": service, "choreography_engine": engine}

    client, cluster, db = build_vem(monkeypatch, Cluster, choreographies)
    for module in (submit_module, jobs_module):
        monkeypatch.setattr(module, "send_command", cluster.send)
    yield client, cluster, db
    for engine in engines:
        engine.shutdown()


def test_a_choreography_built_in_python_saves_and_runs(vem):
    client, cluster, db = vem
    ch = Choreography("daily", max_parallel_runs=3)
    count = ch.step(count_rows)
    count >> ch.step(report, params={"unit": "lines"})
    saved = client.save(ch).unwrap()
    assert saved["version"] == 1 and saved["settings"]["max_parallel_runs"] == 3
    assert client.save(ch).unwrap()["existing"] is True               # nothing changed

    run = client.run("daily", input={"x": 1}).unwrap()
    assert run.result(poll_seconds=0.01).unwrap_err().name == "CHOREOGRAPHY_STEP_FAILED"   # no "day" anywhere
    failed = run.info().unwrap()
    assert failed["steps"]["count_rows"]["error_name"] == "CHOREOGRAPHY_STEP_PARAM_MISSING"

    ch2 = Choreography("daily")
    ch2.step(count_rows, params={"day": "mon"}) >> ch2.step(report)
    run = client.run(ch2).unwrap()                                    # saved first: version 2
    assert run.result(poll_seconds=0.01).unwrap() == {"report": {"line": "30 rows", "from": ["count_rows"]}}
    info = run.info().unwrap()
    assert info["version"] == 2 and info["steps"]["count_rows"]["status"] == "completed"
    submitted = [c for c in cluster.sent if c.operation == wire.JOB_SUBMIT]
    assert submitted[-1].envelope["started_by_type"] == "choreography"
    assert submitted[-1].envelope["params"] == {"rows": 30}            # unit: the code's own default


def test_object_steps_triggers_and_resume_through_the_client(vem):
    client, cluster, db = vem
    counter = client.create(counter_v1.Counter, "visits", 5).unwrap()
    counter.created.result(poll_seconds=0.01).unwrap()

    ch = Choreography("count-visits")
    ch.step(counter, params={"by": 2})
    ch.on_bucket("alice/raw", prefix="in/")
    saved = client.save(ch).unwrap()
    assert [(t["bucket"], t["prefix"]) for t in saved["triggers"]] == [("alice/raw", "in/")]
    ch.triggers = []
    assert client.save(ch).unwrap()["triggers"] == []                 # declared none: removed

    run = client.run("count-visits").unwrap()
    assert run.result(poll_seconds=0.01).unwrap() == {"visits": 7}
    assert run.cancel().unwrap_err().name == "CHOREOGRAPHY_RUN_NOT_ACTIVE"
    again = client.run_of(run.run_id)
    assert again.resume().unwrap_err().name == "CHOREOGRAPHY_RUN_NOT_RESUMABLE"
    assert [c["name"] for c in client.choreographies().unwrap()] == ["count-visits"]
