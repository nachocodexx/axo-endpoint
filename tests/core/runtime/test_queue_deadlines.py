import pytest

from axo_endpoint.core.runtime.queue_deadlines import QueueDeadlines, parse_max_wait


def test_wait_forever_is_not_tracked():
    deadlines = QueueDeadlines()
    deadlines.set("j1", -1, received_at=100.0)
    assert "j1" not in deadlines
    assert deadlines.admit("j1", runner_free=False) is None
    assert deadlines.take("j1", now=10_000.0) is None


def test_zero_rejects_only_when_the_runner_is_busy():
    deadlines = QueueDeadlines()
    deadlines.set("busy", 0, received_at=100.0)
    deadlines.set("free", 0, received_at=100.0)
    err = deadlines.admit("busy", runner_free=False)
    assert err is not None and err.name == "QUEUE_WAIT_TIMEOUT"
    assert deadlines.admit("free", runner_free=True) is None
    # Once admitted, a max_wait=0 job is no longer tracked (it starts now).
    assert deadlines.take("free", now=1_000.0) is None


def test_n_seconds_fails_on_take_after_deadline():
    deadlines = QueueDeadlines()
    deadlines.set("on_time", 5, received_at=100.0)
    deadlines.set("late", 5, received_at=100.0)
    assert deadlines.admit("late", runner_free=False) is None  # N>0 never fails at enqueue
    assert deadlines.take("on_time", now=104.0) is None
    err = deadlines.take("late", now=106.0)
    assert err is not None and err.context["max_wait_s"] == 5
    assert "late" not in deadlines


def test_expired_lists_overdue_jobs_without_forgetting_them():
    deadlines = QueueDeadlines()
    deadlines.set("a", 1, received_at=100.0)
    deadlines.set("b", 10, received_at=100.0)
    assert [job_id for job_id, _ in deadlines.expired(now=102.0)] == ["a"]
    assert "a" in deadlines
    deadlines.discard("a")
    assert deadlines.expired(now=102.0) == []


@pytest.mark.parametrize("raw, expected", [
    (None, 7.0), (0, 0.0), (-1, -1.0), ("2.5", 2.5), (30, 30.0),
    (-2, None), ("soon", None), (True, None), ([1], None),
])
def test_parse_max_wait(raw, expected):
    assert parse_max_wait(raw, default=7.0) == expected
