"""ContainerFunctionRuntime when the runner budget has no room: the job waits
(retrying placement) instead of failing, bounded by max_wait and cancel."""
import time

from option import Err, Ok

from axo_endpoint.core.errors import RunnerBudgetExhaustedError
from axo_endpoint.core.runtime.queue_deadlines import QueueDeadlines
from axo_endpoint.core.storage.backend import StorageKey
from axo_endpoint.service.container.handle import ContainerHandle, ContainerStatus
from axo_endpoint.service.runtime.container_runtime import ContainerFunctionRuntime
from axo_shared.runtime.spec import RuntimeSpec


class _FakeConfig:
    def __init__(self, scratch_root):
        self.AXO_ENDPOINT_SCRATCH_ROOT = scratch_root


class _FakeRecord:
    runtime_spec = RuntimeSpec(type="container")


class _FakeRegistry:
    def get(self, function_ref):
        return Ok(_FakeRecord())


class _FullThenFreeSummoner:
    """No room for the first ``full_for`` summon() calls, then a ready handle."""

    def __init__(self, full_for):
        self.full_for = full_for
        self.calls = 0
        self.handle = ContainerHandle(
            function_id="fn1", version=1, service_name="fn-fn1-v1", mode="docker",
            zmq_address="tcp://127.0.0.1:1", http_address="http://x",
        )
        self.handle.ready_event.set()
        self.handle.status = ContainerStatus.IDLE

    def summon(self, function_id, version, spec):
        self.calls += 1
        if self.calls <= self.full_for:
            return Err(RunnerBudgetExhaustedError("no room", context={"function_id": function_id}))
        return Ok(self.handle)

    def list_handles(self):
        return []


def _runtime(tmp_path, summoner, deadlines=None):
    completions, dispatched = [], []
    runtime = ContainerFunctionRuntime(
        function_registry=_FakeRegistry(),
        summoner=summoner,
        on_complete=lambda inv, result, warnings=None: completions.append((inv, result)),
        config=_FakeConfig(str(tmp_path)),
        queue_deadlines=deadlines,
    )
    runtime._room_poll_seconds = 0.01
    runtime._dispatch_to = lambda handle, ref, job_id, params, t0: dispatched.append((handle, job_id))
    return runtime, completions, dispatched


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


KEY = StorageKey(id="fn1", version=1, alias="fn1")


def test_job_waits_for_room_then_runs(tmp_path):
    summoner = _FullThenFreeSummoner(full_for=3)
    runtime, completions, dispatched = _runtime(tmp_path, summoner)

    assert runtime.invoke(KEY, "job1", {}).is_ok   # accepted right away, never refused
    assert _wait_until(lambda: dispatched)
    assert dispatched == [(summoner.handle, "job1")]
    assert completions == []
    assert summoner.calls == 4


def test_waiting_job_times_out_after_max_wait(tmp_path):
    deadlines = QueueDeadlines()
    runtime, completions, dispatched = _runtime(tmp_path, _FullThenFreeSummoner(full_for=10_000), deadlines)

    deadlines.set("job1", 0.05, received_at=time.time())
    runtime.invoke(KEY, "job1", {})

    assert _wait_until(lambda: completions)
    assert completions[0][1].unwrap_err().name == "QUEUE_WAIT_TIMEOUT"
    assert dispatched == []


def test_max_wait_zero_fails_at_once_when_there_is_no_room(tmp_path):
    deadlines = QueueDeadlines()
    runtime, completions, dispatched = _runtime(tmp_path, _FullThenFreeSummoner(full_for=10_000), deadlines)

    deadlines.set("job1", 0, received_at=time.time())
    runtime.invoke(KEY, "job1", {})

    assert completions[0][1].unwrap_err().name == "QUEUE_WAIT_TIMEOUT"


def test_cancel_stops_a_job_waiting_for_room(tmp_path):
    summoner = _FullThenFreeSummoner(full_for=10_000)
    runtime, completions, dispatched = _runtime(tmp_path, summoner)

    runtime.invoke(KEY, "job1", {})
    assert runtime.cancel("job1") is True
    assert completions[0][1].unwrap_err().name == "JOB_CANCELLED"

    summoner.full_for = 0  # room appears -- the cancelled job must not run anyway
    time.sleep(0.1)
    assert dispatched == []
