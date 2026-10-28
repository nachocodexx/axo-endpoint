import base64

from option import Err, Ok

from axo_shared.active_objects.state import MemoryManifestStore, MemoryValueStore, StatePolicy, StateStore, codec
from axo_shared.events import models as event_models
from axo_shared.protocol import Command, CommandResult
from axo_shared import wire

from axo_endpoint.core.errors import FunctionExecError
from axo_endpoint.core.events import InMemoryEventBus
from axo_endpoint.core.external.bridge import ExternalEventForwardingBridge
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.objects import (
    ACTIVE_OBJECT_CALLED_EVENT,
    ACTIVE_OBJECT_CREATED_EVENT,
    ObjectCoordinator,
    StateAwareDataIO,
)
from axo_endpoint.core.runtime import InvocationHandle
from axo_endpoint.core.runtime.entry import OBJECT_OUTCOME, OBJECT_PARAM, STATE_GET
from axo_endpoint.core.dataio.ref import IORef
from axo_endpoint.core.storage import InMemoryStorageBackend
from axo_endpoint.core.storage.backend import StorageKey
from axo_endpoint.core.usage import JobUsageContext


class _Harness:
    def __init__(self, submit_ok=True):
        self.store = StateStore(MemoryManifestStore(), MemoryValueStore(), MemoryValueStore(),
                                StatePolicy(hot_max_bytes=16), now_fn=lambda: 1.0)
        self.bus = InMemoryEventBus()
        self.events = []
        for t in (ACTIVE_OBJECT_CALLED_EVENT, ACTIVE_OBJECT_CREATED_EVENT):
            self.bus.subscribe(t, self.events.append)
        self.submitted = []
        self.finished = []
        self.results = InMemoryStorageBackend()
        self.submit_ok = submit_ok
        ids = iter(f"job-{i:04d}" for i in range(1000))
        self.coordinator = ObjectCoordinator(store=self.store, submit_fn=self._submit, results=self.results,
                                             event_bus=self.bus, self_id="n0", job_id_fn=lambda: next(ids))
        self.on_complete = self.coordinator.wrap(lambda h, o, w=None: self.finished.append((h.job_id, o)))

    def _submit(self, command, job_id):
        self.submitted.append((command, job_id))
        if not self.submit_ok:
            return CommandResult(ok=False, error="no runner", error_code=3003, error_name="INVOCATION_FAILED")
        return CommandResult(ok=True, metadata={"job_id": job_id})

    def call(self, op="call", **extra):
        envelope = {"op": op, "object_id": "o1", "name": "acme", "function_id": "cls", "class_version": 1, **extra}
        return self.coordinator.handle_call(Command(operation=wire.ACTIVE_OBJECT_CALL,
                                                    content_type="application/json", envelope=envelope))

    def finish(self, job_id, changed=None, result=None, deleted=()):
        body = {"result": result, "deleted": list(deleted), "changed": {}}
        for name, value in (changed or {}).items():
            entry, data = codec.encode(value, 16).unwrap()
            body["changed"][name] = {"entry": entry.to_dict(), "data": base64.b64encode(data).decode()}
        self.on_complete(InvocationHandle(job_id=job_id, function_id="cls", started_at=0.5),
                         Ok({OBJECT_OUTCOME: body}), [])


def test_calls_on_one_object_run_one_at_a_time_in_order():
    h = _Harness()
    first, second = h.call(op="create"), h.call(method="run", params={"by": 2})
    assert first.metadata["status"] == "QUEUED" and second.metadata["ahead"] == 1
    assert [job for _, job in h.submitted] == ["job-0000"]            # the second waits
    h.finish("job-0000", changed={"count": 1})
    assert [job for _, job in h.submitted] == ["job-0000", "job-0001"]
    command = h.submitted[1][0]
    assert command.envelope["params"]["by"] == 2
    assert command.envelope["params"][OBJECT_PARAM]["values"] == {"count": 1}
    assert command.envelope["object_id"] == "o1" and command.envelope["object_method"] == "run"
    h.finish("job-0001", changed={"count": 3}, result=3)
    assert h.store.load("o1").unwrap().get("count").unwrap() == 3
    assert h.finished[-1] == ("job-0001", Ok(3))
    assert not h.coordinator.busy("o1")
    assert [e.event_type for e in h.events] == [ACTIVE_OBJECT_CALLED_EVENT, ACTIVE_OBJECT_CREATED_EVENT,
                                                ACTIVE_OBJECT_CALLED_EVENT]
    assert h.events[-1].payload["changed"] == ["count"] and h.events[-1].payload["revision"] == 2


def test_a_failed_call_saves_nothing_and_the_next_one_starts():
    h = _Harness()
    h.call(op="create")
    h.finish("job-0000", changed={"count": 1})
    h.call()
    h.call()
    h.on_complete(InvocationHandle(job_id="job-0001", function_id="cls"), Err(FunctionExecError("boom")), [])
    assert h.store.manifest("o1").unwrap().revision == 1
    assert h.events[-1].payload["outcome"] == "failed_user"
    assert h.submitted[-1][1] == "job-0002"


def test_a_call_that_cannot_start_fails_and_frees_the_object():
    h = _Harness(submit_ok=False)
    h.call(op="create")
    stored = h.results.get(StorageKey(id="job-0000")).unwrap()
    assert stored.ok is False and "no runner" in stored.error
    assert not h.coordinator.busy("o1")


def test_creating_an_object_that_exists_fails_without_running():
    h = _Harness()
    h.call(op="create")
    h.finish("job-0000", changed={"count": 1})
    h.call(op="create")
    assert len(h.submitted) == 1
    assert "ACTIVE_OBJECT_EXISTS" in h.results.get(StorageKey(id="job-0001")).unwrap().error


def test_a_result_without_state_is_refused():
    h = _Harness()
    h.call(op="create")
    h.on_complete(InvocationHandle(job_id="job-0000", function_id="cls"), Ok({"plain": 1}), [])
    assert h.finished[-1][1].is_err and h.store.manifest("o1").unwrap() is None


def test_other_jobs_pass_straight_through():
    h = _Harness()
    h.on_complete(InvocationHandle(job_id="other", function_id="fn"), Ok(5), [])
    assert h.finished == [("other", Ok(5))] and h.events == []


def test_a_running_call_reads_big_values_through_dataio():
    h = _Harness()
    h.call(op="create")
    h.finish("job-0000", changed={"text": "z" * 100})
    h.call()
    dataio = StateAwareDataIO(inner=None, coordinator=h.coordinator)
    raw = dataio.resolve("job-0001", STATE_GET, IORef(bucket="axo-object-state", name="text"), None).unwrap()
    assert raw == codec.encode("z" * 100, 16).unwrap()[1]
    assert dataio.resolve("job-0001", STATE_GET, IORef(bucket="b", name="nope"), None).is_err
    assert dataio.resolve("unknown", STATE_GET, IORef(bucket="b", name="text"), None).is_err


def test_restore_is_refused_while_the_object_is_busy():
    h = _Harness()
    h.call(op="create")
    restored = h.coordinator.handle_restore(Command(operation=wire.ACTIVE_OBJECT_RESTORE, content_type="",
                                                    envelope={"object_id": "o1", "revision": 0}))
    assert not restored.ok and restored.error_name == "ACTIVE_OBJECT_STATE_CONFLICT"


def test_a_repeated_job_id_is_a_duplicate():
    h = _Harness()
    assert h.call(op="create", job_id="client-chosen-1").metadata["status"] == "QUEUED"
    assert h.call(op="create", job_id="client-chosen-1").metadata["status"] == "DUPLICATE"


def test_object_events_reach_the_vem_as_typed_events():
    class _Publisher:
        def __init__(self):
            self.published = []

        def publish(self, event_type, data):
            self.published.append((event_type, data))

    publisher = _Publisher()
    bridge = ExternalEventForwardingBridge(
        publisher=publisher, endpoint_id="n0", results=InMemoryStorageBackend(),
        function_registry=FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=InMemoryEventBus()))
    h = _Harness()
    h.call(op="create", started_by_id="ana")
    h.finish("job-0000", changed={"count": 1})
    for event in h.events:
        bridge.on_active_object_event(event)
    types = [t for t, _ in publisher.published]
    assert types == [event_models.ACTIVE_OBJECT_CALLED, event_models.ACTIVE_OBJECT_CREATED]
    assert publisher.published[1][1]["created_by"] == "ana" and publisher.published[0][1]["method"] == "__create__"


def test_usage_carries_the_object():
    usage = JobUsageContext.from_envelope({"function_id": "cls", "function_version": 1, "object_id": "o1",
                                           "object_method": "run"}, "j1", 1.0)
    assert (usage.object_id, usage.object_method) == ("o1", "run")


def test_the_plan_limits_sent_with_a_call_apply_to_it():
    h = _Harness()
    limits = {"hot_max_bytes": 8, "max_state_bytes": 40, "keep_revisions": 1, "max_keep_revisions": 1}
    h.call(op="create", state_limits=limits)
    assert h.submitted[0][0].envelope["params"][OBJECT_PARAM]["hot_max_bytes"] == 8
    h.finish("job-0000", changed={"note": "x" * 10})                  # 12 bytes of JSON: over 8, kept as a blob
    assert h.store.manifest("o1").unwrap().attributes["note"].tier == "blob"
    h.call(state_limits=limits)
    h.finish("job-0001", changed={"note": "y" * 60})                  # over the 40-byte cap
    assert h.store.manifest("o1").unwrap().revision == 1
    assert h.finished[-1][0] == "job-0001" and h.finished[-1][1].unwrap_err().name == "ACTIVE_OBJECT_STATE_TOO_LARGE"
    for n in range(3):
        h.call(state_limits=limits, keep_revisions=10)                # asks for 10, the plan allows 1
        h.finish(f"job-{n + 2:04d}", changed={"n": n})
    assert [m.revision for m in h.store.revisions("o1").unwrap()] == [4, 3]


def test_a_choreography_steps_run_and_step_reach_the_billing_record():
    ref = {"choreography_run_id": "r1", "choreography_step": "n2", "choreography_version": 3}
    usage = JobUsageContext.from_envelope({"function_id": "f", "function_version": 1, **ref}, "j1", 1.0)
    assert (usage.choreography_run_id, usage.choreography_step, usage.choreography_version) == ("r1", "n2", 3)
    h = _Harness()
    h.call(op="create", **ref)                                        # an object step: passed on to its job
    assert {k: h.submitted[0][0].envelope[k] for k in ref} == ref
