import pytest
from option import Err, Ok

from axo_endpoint.core.events import Event, InMemoryEventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_shared.protocol import Command
from axo_endpoint.core.runtime import FunctionRuntime, FunctionRuntimeError, InvocationHandle
from axo_endpoint.core.storage import InMemoryStorageBackend, StorageKey
from axo_endpoint.service.handlers.job_submit import JobSubmitHandler, build_completion_recorder
from axo_endpoint.core.results import FunctionResult


class _FakeRuntime(FunctionRuntime):
    """Accepts invocations without spawning anything real -- handler tests
    must not need a real subprocess."""

    def __init__(self):
        self.invocations = []

    def invoke(self, function_ref, job_id, params):
        self.invocations.append((function_ref, job_id, params))
        return Ok(InvocationHandle(job_id=job_id, function_id=function_ref.id))

    def cancel(self, job_id):
        return False


class _RejectingRuntime(FunctionRuntime):
    def invoke(self, function_ref, job_id, params):
        return Err(FunctionRuntimeError("no such function"))

    def cancel(self, job_id):
        return False


@pytest.fixture
def results_store():
    return InMemoryStorageBackend()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def function_registry(event_bus):
    """Empty registry -- every lookup returns Ok(None), so JobSubmitHandler
    skips params_schema validation entirely, preserving these tests'
    pre-existing "arbitrary params blob" behavior."""
    return FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)


def test_returns_job_id_and_queued_status_immediately(results_store, event_bus, function_registry):
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(
        runtime=runtime, results=results_store, event_bus=event_bus,
        function_registry=function_registry, job_id_fn=lambda: "job1",
    )
    command = Command(
        operation="JOB_SUBMIT",
        content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"a": 1, "b": 2}},
    )

    result = handler.handle(command)

    assert result.ok is True
    assert result.metadata == {"job_id": "job1", "status": "QUEUED"}
    assert runtime.invocations == [(StorageKey(id="add", version=1, alias="add"), "job1", {"a": 1, "b": 2})]


def test_writes_pending_placeholder_before_returning(results_store, event_bus, function_registry):
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(), results=results_store, event_bus=event_bus,
        function_registry=function_registry, job_id_fn=lambda: "job1",
    )
    handler.handle(
        Command(
            operation="JOB_SUBMIT",
            content_type="application/json",
            envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {}},
        )
    )

    placeholder = results_store.get(StorageKey(id="job1")).unwrap()
    assert placeholder.ok is False
    assert placeholder.error == "PENDING"


def test_emits_job_submitted_event(results_store, event_bus, function_registry):
    received = []
    event_bus.subscribe("JOB_SUBMITTED", received.append)
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(),
        results=results_store,
        event_bus=event_bus,
        function_registry=function_registry,
        job_id_fn=lambda: "job1",
        now_fn=lambda: 100.0,
    )

    handler.handle(
        Command(
            operation="JOB_SUBMIT",
            content_type="application/json",
            envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {}},
        )
    )

    assert received == [
        Event(
            event_type="JOB_SUBMITTED",
            payload={"job_id": "job1", "function_id": "add", "function_version": 1, "params": {},
                     "usage": {"job_id": "job1", "function_id": "add", "function_version": 1,
                               "received_at": 100.0, "trace_id": "job1", "attempt": 1,
                               "started_by_type": "user", "started_by_id": "unknown",
                               "upstream_waits_ms": {}, "max_wait_s": -1.0,
                               "acting_user_id": "", "object_id": None, "object_method": None,
                               "choreography_run_id": None, "choreography_step": None,
                               "choreography_version": None, "choreography_calls": [],
                               "reason": "first", "retried_by": None, "max_attempts": 1}},
            timestamp=100.0,
        )
    ]


def test_runtime_rejection_overwrites_placeholder_with_failure(results_store, event_bus, function_registry):
    handler = JobSubmitHandler(
        runtime=_RejectingRuntime(), results=results_store, event_bus=event_bus,
        function_registry=function_registry, job_id_fn=lambda: "job1",
    )
    result = handler.handle(
        Command(
            operation="JOB_SUBMIT",
            content_type="application/json",
            envelope={"function_id": "missing", "function_name": "missing", "function_version": 1, "params": {}},
        )
    )

    assert result.ok is False
    placeholder = results_store.get(StorageKey(id="job1")).unwrap()
    assert placeholder.ok is False
    assert placeholder.error != "PENDING"


def test_missing_function_ref_returns_error(results_store, event_bus, function_registry):
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(), results=results_store, event_bus=event_bus, function_registry=function_registry,
    )
    result = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope={}))
    assert result.ok is False
    assert result.error_name == "MISSING_FIELD"
    assert result.error_code == 1001


def test_params_schema_fills_defaults_and_rejects_unknown_keys(results_store, event_bus):
    from axo_shared.functions.params_schema import ParamSpec

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(
        function_id="add", name="add", code=b"", now=0.0,
        params_schema=[
            ParamSpec(name="a", type="number", required=True),
            ParamSpec(name="b", type="number", required=False, default=1),
        ],
    )
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(
        runtime=runtime, results=results_store, event_bus=event_bus,
        function_registry=registry, job_id_fn=lambda: "job1",
    )

    result = handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"a": 2}},
    ))

    assert result.ok is True
    assert runtime.invocations == [(StorageKey(id="add", version=1, alias="add"), "job1", {"a": 2, "b": 1})]


def test_params_schema_rejects_missing_required_field(results_store, event_bus):
    from axo_shared.functions.params_schema import ParamSpec

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(
        function_id="add", name="add", code=b"", now=0.0,
        params_schema=[ParamSpec(name="a", type="number", required=True)],
    )
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(), results=results_store, event_bus=event_bus,
        function_registry=registry, job_id_fn=lambda: "job1",
    )

    result = handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {}},
    ))

    assert result.ok is False
    assert result.error_name == "INVALID_FIELD"


def test_params_schema_rejects_undeclared_key(results_store, event_bus):
    from axo_shared.functions.params_schema import ParamSpec

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(
        function_id="add", name="add", code=b"", now=0.0,
        params_schema=[ParamSpec(name="a", type="number", required=True)],
    )
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(), results=results_store, event_bus=event_bus,
        function_registry=registry, job_id_fn=lambda: "job1",
    )

    result = handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"a": 1, "extra": True}},
    ))

    assert result.ok is False
    assert result.error_name == "INVALID_FIELD"


def test_empty_params_schema_skips_validation(results_store, event_bus):
    """A function registered before params_schema existed (or with no
    schema declared) keeps accepting an arbitrary params blob."""
    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(function_id="add", name="add", code=b"", now=0.0)
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(
        runtime=runtime, results=results_store, event_bus=event_bus,
        function_registry=registry, job_id_fn=lambda: "job1",
    )

    result = handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"anything": "goes"}},
    ))

    assert result.ok is True
    assert runtime.invocations == [(StorageKey(id="add", version=1, alias="add"), "job1", {"anything": "goes"})]


def test_completion_recorder_writes_success_result_and_emits_job_completed(results_store):
    event_bus = InMemoryEventBus()
    received = []
    event_bus.subscribe("JOB_COMPLETED", received.append)
    on_complete = build_completion_recorder(results=results_store, event_bus=event_bus, now_fn=lambda: 100.0)

    on_complete(InvocationHandle(job_id="job1", function_id="add"), Ok(5))

    result = results_store.get(StorageKey(id="job1")).unwrap()
    assert result.ok is True
    assert result.output == {"value": 5, "type": "json"}
    assert received == [
        Event(
            event_type="JOB_COMPLETED",
            payload={"job_id": "job1", "function_id": "add", "function_version": None, "duration_ms": None,
                     "outcome": "completed", "started_at": None, "finished_at": 100.0,
                     "failure": None, "attempt": 1},
            timestamp=100.0,
        )
    ]


def test_completion_recorder_computes_duration_from_started_at(results_store):
    event_bus = InMemoryEventBus()
    received = []
    event_bus.subscribe("JOB_COMPLETED", received.append)
    on_complete = build_completion_recorder(results=results_store, event_bus=event_bus, now_fn=lambda: 100.5)

    on_complete(InvocationHandle(job_id="job1", function_id="add", version=2, started_at=100.0), Ok(5))

    assert received == [
        Event(
            event_type="JOB_COMPLETED",
            payload={"job_id": "job1", "function_id": "add", "function_version": 2, "duration_ms": 500.0,
                     "outcome": "completed", "started_at": 100.0, "finished_at": 100.5,
                     "failure": None, "attempt": 1},
            timestamp=100.5,
        )
    ]


def test_completion_recorder_writes_failure_result_and_emits_job_failed(results_store):
    event_bus = InMemoryEventBus()
    received = []
    event_bus.subscribe("JOB_FAILED", received.append)
    on_complete = build_completion_recorder(results=results_store, event_bus=event_bus, now_fn=lambda: 100.0)

    on_complete(InvocationHandle(job_id="job1", function_id="add"), Err(FunctionRuntimeError("boom")))

    result = results_store.get(StorageKey(id="job1")).unwrap()
    assert result.ok is False
    assert result.error == "boom"
    assert len(received) == 1


@pytest.mark.parametrize("value", [b"raw-bytes", {1, 2, 3}, object()])
def test_non_json_results_fail_the_job_as_the_users_error(results_store, value):
    event_bus = InMemoryEventBus()
    failed = []
    event_bus.subscribe("JOB_FAILED", failed.append)
    on_complete = build_completion_recorder(results=results_store, event_bus=event_bus, now_fn=lambda: 100.0)

    on_complete(InvocationHandle(job_id="job1", function_id="add"), Ok(value))

    result = results_store.get(StorageKey(id="job1")).unwrap()
    assert result.ok is False
    assert "results must be JSON" in result.error and "dataio.write" in result.error
    assert failed[0].payload["outcome"] == "failed_user"


def test_invalid_max_wait_is_rejected_before_anything_is_recorded(results_store, event_bus, function_registry):
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(
        runtime=runtime, results=results_store, event_bus=event_bus,
        function_registry=function_registry, job_id_fn=lambda: "job1",
    )
    command = Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_version": 1, "params": {}, "max_wait": -5}, payload=b"",
    )
    result = handler.handle(command)
    assert result.ok is False
    assert result.error_name == "INVALID_FIELD"
    assert runtime.invocations == []


def test_max_wait_registers_a_deadline_from_received_time(results_store, event_bus, function_registry):
    from axo_endpoint.core.runtime.queue_deadlines import QueueDeadlines

    deadlines = QueueDeadlines()
    handler = JobSubmitHandler(
        runtime=_FakeRuntime(), results=results_store, event_bus=event_bus,
        function_registry=function_registry, job_id_fn=lambda: "job1", now_fn=lambda: 100.0,
        queue_deadlines=deadlines, default_max_wait_s=-1,
    )
    command = Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_version": 1, "params": {}, "max_wait": 3}, payload=b"",
    )
    assert handler.handle(command).ok is True
    assert deadlines.take("job1", now=103.0) is None

    handler._job_id_fn = lambda: "job2"
    handler.handle(command)
    assert deadlines.take("job2", now=103.5).name == "QUEUE_WAIT_TIMEOUT"


def test_output_buckets_ride_in_the_params_and_survive_a_forward_revalidation(results_store, event_bus):
    """A choreography's FN -> bucket edges: the function sees them as
    ctx.output_buckets. A JOB_FORWARD brings params already carrying the
    reserved key; validation must not reject it as an undeclared param."""
    from axo_shared.functions.params_schema import ParamSpec
    from axo_endpoint.core.runtime import OUTPUT_BUCKETS_PARAM, build_invocation

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(function_id="add", name="add", code=b"", now=0.0,
                      params_schema=[ParamSpec(name="a", type="number", required=True)])
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(runtime=runtime, results=results_store, event_bus=event_bus,
                               function_registry=registry, job_id_fn=lambda: "job1")

    assert handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"a": 2},
                  "output_buckets": ["reports"]},
    )).ok
    params = runtime.invocations[0][2]
    assert params == {"a": 2, OUTPUT_BUCKETS_PARAM: ["reports"]}

    # what the worker does with them
    ctx, call_params = build_invocation("job1", "/tmp/x", params)
    assert ctx.output_buckets == ["reports"] and call_params == {"a": 2}

    # the same params arriving again (as a forward would send them)
    assert handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": params},
    )).ok
    assert runtime.invocations[1][2] == params


def test_a_caller_job_id_is_used_and_a_repeat_runs_once(results_store, event_bus, function_registry):
    """The Control Plane brings its own id for a job it accepted while the VE
    slept; delivering it twice must not run it twice."""
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(runtime=runtime, results=results_store, event_bus=event_bus,
                               function_registry=function_registry, job_id_fn=lambda: "minted1")
    envelope = {"function_id": "add", "function_name": "add", "function_version": 1, "params": {},
                "job_id": "cp-job-0001"}
    first = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope=envelope))
    again = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope=envelope))
    assert first.metadata == {"job_id": "cp-job-0001", "status": "QUEUED"}
    assert again.ok and again.metadata == {"job_id": "cp-job-0001", "status": "DUPLICATE"}
    assert len(runtime.invocations) == 1
    bad = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json",
                                 envelope={**envelope, "job_id": "../x"}))
    assert bad.ok is False and runtime.invocations[-1][1] == "cp-job-0001"


def test_a_choreography_event_reaches_ctx_event_and_optional_params_keep_their_code_default(results_store, event_bus):
    from axo_shared.functions.params_schema import ParamSpec
    from axo_endpoint.core.runtime import EVENT_PARAM, build_invocation

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=event_bus)
    registry.register(function_id="add", name="add", code=b"", now=0.0, params_schema=[
        ParamSpec(name="a", type="json", required=True), ParamSpec(name="b", type="json", required=False)])
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(runtime=runtime, results=results_store, event_bus=event_bus,
                               function_registry=registry, job_id_fn=lambda: "job1")
    event = {"run": {"run_id": "r1"}, "previous": {"a": {"output": {"x": 1}}}}
    assert handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": {"a": 2},
                  "event": event},
    )).ok
    params = runtime.invocations[0][2]
    assert params == {"a": 2, EVENT_PARAM: event}          # "b" left out: the code's default applies
    ctx, call_params = build_invocation("job1", "/tmp/x", params)
    assert ctx.event == event and call_params == {"a": 2}

    # forwarded again with the event already inside the params
    handler._job_id_fn = lambda: "job2"
    assert handler.handle(Command(
        operation="JOB_SUBMIT", content_type="application/json",
        envelope={"function_id": "add", "function_name": "add", "function_version": 1, "params": params},
    )).ok
    assert runtime.invocations[1][2] == params


def test_a_persons_retry_runs_a_finished_job_again_as_its_next_attempt(results_store, event_bus, function_registry):
    runtime = _FakeRuntime()
    handler = JobSubmitHandler(runtime=runtime, results=results_store, event_bus=event_bus,
                               function_registry=function_registry)
    envelope = {"function_id": "add", "function_name": "add", "function_version": 1, "params": {},
                "job_id": "job-retry-1"}
    handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope=envelope))
    retry = {**envelope, "retried_by": "u-7", "attempt": 2}
    still_running = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope=retry))
    assert still_running.metadata["status"] == "DUPLICATE"           # not finished yet: nothing to retry

    results_store.put(StorageKey(id="job-retry-1"), FunctionResult(job_id="job-retry-1", ok=False, error="boom"))
    again = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope=retry))
    assert again.metadata == {"job_id": "job-retry-1", "status": "QUEUED"} and len(runtime.invocations) == 2


def test_a_job_with_impossible_attempt_settings_is_refused(results_store, event_bus, function_registry):
    handler = JobSubmitHandler(runtime=_FakeRuntime(), results=results_store, event_bus=event_bus,
                               function_registry=function_registry)
    answer = handler.handle(Command(operation="JOB_SUBMIT", content_type="application/json", envelope={
        "function_id": "add", "function_name": "add", "function_version": 1, "params": {}, "max_attempts": 0}))
    assert not answer.ok
