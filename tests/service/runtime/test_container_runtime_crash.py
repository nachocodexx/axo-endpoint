import queue
import time

from option import Ok

from axo_endpoint.core.consensus.concurrency import SlotDecision
from axo_endpoint.core.runtime.base import InvocationHandle
from axo_endpoint.service.container.handle import ContainerHandle, ContainerStatus
from axo_endpoint.service.runtime.container_runtime import ContainerFunctionRuntime
from axo_endpoint.core.storage import StorageKey
from axo_shared.runtime.spec import RuntimeSpec


class _FakeConfig:
    def __init__(self, scratch_root):
        self.AXO_ENDPOINT_SCRATCH_ROOT = scratch_root


class _FakeRecord:
    def __init__(self, spec):
        self.runtime_spec = spec


class _FakeRegistry:
    def __init__(self, spec):
        self._spec = spec

    def get(self, function_ref):
        return Ok(_FakeRecord(self._spec))


class _FakeSummoner:
    """Only what a crash and a re-invoke need to reach a real placement
    decision -- see test_container_runtime_placement.py for the full
    duck-type this mirrors."""

    def __init__(self, idle=None, least_busy=None, summon_at_handle=None):
        self._idle = idle
        self._least_busy = least_busy
        self._summon_at_handle = summon_at_handle
        self.summon_at_calls = []
        self.dismiss_calls = []

    def find_idle(self, function_id, version):
        return self._idle

    def least_busy(self, function_id, version):
        return self._least_busy

    def summon_at(self, function_id, version, spec, pool_index):
        self.summon_at_calls.append(pool_index)
        return Ok(self._summon_at_handle)

    def dismiss(self, handle, reason="dismissed"):
        self.dismiss_calls.append((handle, reason))


class _FakeConcurrencyClient:
    def __init__(self, decision, self_id="self-endpoint"):
        self._decision = decision
        self.self_id = self_id
        self.release_calls = []

    def request_growth(self, function_id, version, max_concurrency):
        return Ok(self._decision)

    def release(self, function_id, version, slot_index):
        self.release_calls.append((function_id, version, slot_index))


def _handle(function_id="fn1", version=1, pool_index=0):
    h = ContainerHandle(
        function_id=function_id, version=version, service_name=f"{function_id}-svc-{pool_index}",
        mode="docker", zmq_address="tcp://127.0.0.1:1", http_address="http://x",
        pool_index=pool_index,
    )
    h.ready_event.set()
    h.status = ContainerStatus.IDLE
    return h


def _runtime(tmp_path, summoner):
    completions = []

    def on_complete(inv_handle, result, warnings=None):
        completions.append((inv_handle, result))

    runtime = ContainerFunctionRuntime(
        function_registry=_FakeRegistry(RuntimeSpec(type="container", max_concurrency=2)),
        summoner=summoner,
        on_complete=on_complete,
        config=_FakeConfig(str(tmp_path)),
    )
    return runtime, completions


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_crash_ends_the_attempt_and_dismisses_the_container_at_once(tmp_path):
    """The runtime never retries on its own any more: the crash is this
    attempt's outcome (JobAttempts, in front of on_complete, decides)."""
    summoner = _FakeSummoner()
    runtime, completions = _runtime(tmp_path, summoner)
    handle = _handle()
    inv_handle = InvocationHandle(job_id="job1", function_id="fn1", version=1)

    runtime._handle_crash(handle, inv_handle, "boom", {"a": 1})

    assert len(completions) == 1
    assert completions[0][1].unwrap_err().name == "CONTAINER_CRASH_ERROR"
    # Regression check: the crashed container's real Docker container is
    # dismissed immediately (not left for a later periodic sweep), which is
    # what stops its deterministic name from 409-conflicting on reuse.
    assert summoner.dismiss_calls == [(handle, "crashed")]


def test_queued_jobs_behind_a_crashed_container_each_end_their_attempt(tmp_path):
    summoner = _FakeSummoner()
    runtime, completions = _runtime(tmp_path, summoner)
    handle = _handle()
    handle._job_queue = queue.Queue()
    handle._job_queue.put(("job2", "/scratch/job2", {"b": 2}))
    handle._job_queue.put(("job3", "/scratch/job3", {"c": 3}))
    inv_handle = InvocationHandle(job_id="job1", function_id="fn1", version=1)

    runtime._handle_crash(handle, inv_handle, "boom", {"a": 1})

    assert _wait_until(lambda: len(completions) == 3)
    assert {c[0].job_id for c in completions} == {"job1", "job2", "job3"}
    assert all(c[1].is_err for c in completions)
    assert handle._job_queue.empty()


def test_a_re_invoked_job_can_land_on_a_different_container_via_real_placement(tmp_path):
    """What JobAttempts does after a crash -- invoke the same job again --
    goes through real cluster placement (grow/place), not a resend to the
    container that just died."""
    grown_handle = _handle(pool_index=1)
    summoner = _FakeSummoner(idle=None, summon_at_handle=grown_handle)
    runtime, completions = _runtime(tmp_path, summoner)
    concurrency_client = _FakeConcurrencyClient(SlotDecision.granted(1), self_id="self-endpoint")
    runtime.attach_cluster_placement(concurrency_client, forward_job_fn=lambda *a: Ok(None))
    crashed_handle = _handle(pool_index=0)
    inv_handle = InvocationHandle(job_id="job1", function_id="fn1", version=1)
    runtime._handle_crash(crashed_handle, inv_handle, "boom", {"a": 1})

    runtime.invoke(StorageKey(id="fn1", version=1), "job1", {"a": 1})

    assert _wait_until(lambda: summoner.summon_at_calls == [1])
    assert summoner.dismiss_calls == [(crashed_handle, "crashed")]
    assert concurrency_client.release_calls == [("fn1", 1, crashed_handle.pool_index)]
