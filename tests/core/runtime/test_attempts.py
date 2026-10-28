from option import Err, Ok

from axo_endpoint.core.errors import (
    ContainerCrashError, FunctionExecError, JobCancelledError, QueueWaitTimeoutError,
)
from axo_endpoint.core.runtime.attempts import AttemptPolicy, JobAttempts
from axo_endpoint.core.runtime.base import InvocationHandle
from axo_endpoint.core.storage import StorageKey

REF = StorageKey(id="fn", version=1)


class _Timer:
    """Runs only when the test says so (start() just records it)."""

    created = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.started, self.cancelled = delay, fn, False, False
        _Timer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn()


def _setup(policy=AttemptPolicy()):
    _Timer.created = []
    finals, failed, invoked = [], [], []
    attempts = JobAttempts(
        invoke_fn=lambda ref, job_id, params: invoked.append((job_id, params)) or Ok(None),
        on_attempt_failed=lambda *a: failed.append(a),
        timer_factory=_Timer,
        now_fn=lambda: 100.0,
    )
    on_complete = attempts.wrap(lambda handle, outcome, warnings=None: finals.append(
        (handle.job_id, outcome, attempts.attempt_of(handle.job_id))))
    attempts.begin("job1", REF, {"a": 1}, policy)
    return attempts, on_complete, finals, failed, invoked


def _handle():
    return InvocationHandle(job_id="job1", function_id="fn", version=1, started_at=99.0)


def test_a_success_goes_straight_on():
    attempts, on_complete, finals, failed, invoked = _setup()
    on_complete(_handle(), Ok(5))
    assert finals == [("job1", Ok(5), 1)] and failed == [] and invoked == []
    assert attempts.attempt_of("job1") is None


def test_a_code_failure_is_final_when_max_attempts_is_one():
    _attempts, on_complete, finals, failed, _ = _setup()
    on_complete(_handle(), Err(FunctionExecError("boom")))
    assert len(finals) == 1 and failed == []


def test_a_code_failure_is_tried_again_while_max_attempts_allows_with_backoff():
    attempts, on_complete, finals, failed, invoked = _setup(
        AttemptPolicy(max_attempts=3, retry_delay_seconds=2.0, retry_backoff="exponential"))
    on_complete(_handle(), Err(FunctionExecError("boom")))
    assert finals == [] and _Timer.created[0].delay == 2.0
    assert failed[0][3:] == (102.0, "code_failed") and attempts.waiting("job1")
    _Timer.created[0].fire()
    assert invoked == [("job1", {"a": 1})] and attempts.attempt_of("job1") == 2

    on_complete(_handle(), Err(FunctionExecError("boom")))
    assert _Timer.created[1].delay == 4.0
    _Timer.created[1].fire()
    on_complete(_handle(), Err(FunctionExecError("boom")))      # attempt 3 of 3: final
    assert len(finals) == 1 and finals[0][2] == 3


def test_platform_failures_are_tried_again_up_to_three_attempts_even_with_max_attempts_one():
    _attempts, on_complete, finals, failed, invoked = _setup()
    for timer_index in range(2):
        on_complete(_handle(), Err(ContainerCrashError("gone")))
        _Timer.created[timer_index].fire()
    assert [f[4] for f in failed] == ["platform_failed", "platform_failed"] and len(invoked) == 2
    on_complete(_handle(), Err(ContainerCrashError("gone")))
    assert len(finals) == 1 and finals[0][2] == 3


def test_queue_timeouts_and_cancels_are_never_tried_again():
    for error in (QueueWaitTimeoutError("late"), JobCancelledError("stop")):
        _attempts, on_complete, finals, failed, _ = _setup(AttemptPolicy(max_attempts=5))
        on_complete(_handle(), Err(error))
        assert len(finals) == 1 and failed == []


def test_cancelling_a_job_waiting_for_its_next_attempt_ends_it_as_cancelled():
    attempts, on_complete, finals, _failed, invoked = _setup(AttemptPolicy(max_attempts=2))
    on_complete(_handle(), Err(FunctionExecError("boom")))
    assert attempts.cancel("job1") is True
    assert _Timer.created[0].cancelled
    assert finals[0][1].unwrap_err().name == "JOB_CANCELLED"
    _Timer.created[0].fire()                                    # a late timer does nothing
    assert invoked == [] and attempts.cancel("job1") is False


def test_an_attempt_that_cannot_be_placed_is_itself_an_outcome():
    _Timer.created = []
    finals = []
    attempts = JobAttempts(invoke_fn=lambda *a: Err(ContainerCrashError("no runner")), timer_factory=_Timer)
    on_complete = attempts.wrap(lambda handle, outcome, warnings=None: finals.append(outcome))
    attempts.begin("job1", REF, {}, AttemptPolicy())
    on_complete(_handle(), Err(ContainerCrashError("gone")))
    _Timer.created[0].fire()                                    # attempt 2 can't start: platform failure
    _Timer.created[1].fire()                                    # attempt 3 can't start: final
    assert len(finals) == 1 and finals[0].is_err


def test_forwarding_hands_the_settings_on():
    attempts, *_ = _setup(AttemptPolicy(max_attempts=4, retry_delay_seconds=3.0))
    assert attempts.take_for_forward("job1") == {"max_attempts": 4, "retry_delay_seconds": 3.0,
                                                 "retry_backoff": "fixed"}
    assert attempts.attempt_of("job1") is None
