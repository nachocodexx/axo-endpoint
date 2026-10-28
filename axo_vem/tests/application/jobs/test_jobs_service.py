from types import SimpleNamespace

import mongomock
import pytest
from option import Ok

from axo_shared.protocol import CommandResult
from axo_vem.application.jobs import service as service_module
from axo_vem.application.jobs.results import MongoJobResultStore, ResultNotSaved
from axo_vem.application.jobs.service import JobsService
from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.domain.virtual_environment.settings import VeSettings
from axo_vem.infrastructure.database.mongo.job_attempt_repository import MongoJobAttemptRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository


class _Endpoints:
    def get(self, endpoint_id):
        return SimpleNamespace(router_bind="tcp://0.0.0.0:5555")


class _Log:
    def __init__(self):
        self.events = []

    def append_to_stream(self, stream, event_type, data):
        self.events.append((event_type, data))


class _ImmediatePool:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **_):
        pass


@pytest.fixture
def setup(monkeypatch):
    db = mongomock.MongoClient()["test"]
    attempts = MongoJobAttemptRepository(db["job_attempts"])
    jobs = MongoJobRepository(db["jobs"], attempts=attempts)
    sent, forwarded, log = [], [], _Log()
    answers = {}

    def fake_send(rpc, command, timeout):
        sent.append((command.operation, command.envelope))
        return Ok(answers.get(command.operation, CommandResult(ok=True, metadata={})))

    monkeypatch.setattr(service_module, "send_command", fake_send)
    settings = [VeSettings()]
    svc = JobsService(jobs, attempts, MongoJobResultStore(db["job_results"]), _Endpoints(),
                      settings_fn=lambda: settings[0], event_log=log, forward_attempt=lambda r: forwarded.append(r) or True,
                      virtual_environment_id="ve1", now_fn=lambda: 1_000_000.0, sleep_fn=lambda s: None)
    svc._pool = _ImmediatePool()
    return SimpleNamespace(svc=svc, jobs=jobs, attempts=attempts, sent=sent, answers=answers, forwarded=forwarded,
                           log=log, settings=settings, db=db)


def _attempt(**over):
    record = {"job_id": "job-1", "attempt": 1, "outcome": "completed", "function_id": "add", "function_version": 1,
              "endpoint_id": "ep-1", "received_at": "1970-01-12T13:46:30+00:00",
              "finished_at": "1970-01-12T13:46:40+00:00", "execution_ms": 12.0}
    record.update(over)
    return record


def _submit(svc, **over):
    args = dict(function_id="add", function_version=1, params={"a": 1}, endpoint_id="ep-1", acting_user_id="u1",
                started_by_type="user", started_by_id="u1", started_by_name="ana")
    args.update(over)
    svc.submitted("job-1", **args)


def test_a_successful_job_has_its_result_kept_for_the_ve_default(setup):
    _submit(setup.svc)
    setup.answers["JOB_RESULT"] = CommandResult(ok=True, metadata={"status": "COMPLETED",
                                                                   "output": {"value": {"sum": 3}}, "warnings": []})
    assert setup.svc.on_attempt(_attempt()) is True
    job = setup.jobs.get("job-1")
    assert (job.status.value, job.outcome, job.started_by_name) == ("COMPLETED", "completed", "ana")
    assert job.result["stored"] and job.keep_result == "7d"
    assert job.keep_until == "1970-01-19T13:46:40+00:00"          # finished + 7 days
    assert setup.svc.result("job-1") == {"sum": 3}


def test_a_failed_attempt_that_will_be_retried_marks_the_job_waiting_and_hides_the_traceback_from_the_cp(setup):
    _submit(setup.svc, max_attempts=3)
    failure = {"error_class": "FUNCTION_EXEC_ERROR", "error_code": 3002, "component": "runtime",
               "message": "KeyError: 'a'", "traceback": "Traceback ...", "error_type": "KeyError"}
    setup.svc.on_attempt(_attempt(outcome="failed_user", failure=failure, retry_at="1970-01-12T13:46:45+00:00"))
    job = setup.jobs.get("job-1")
    assert (job.status.value, job.next_attempt_at, job.error["fault"]) == (
        "WAITING_RETRY", "1970-01-12T13:46:45+00:00", "code")
    assert setup.forwarded[0]["failure"]["traceback"] is None
    assert setup.svc.detail("job-1", with_traceback=True)["attempts"][0]["failure"]["traceback"] == "Traceback ..."
    assert setup.svc.detail("job-1")["attempts"][0]["failure"]["traceback"] is None


def test_a_job_asking_for_longer_than_the_ve_allows_is_capped(setup):
    setup.settings[0] = VeSettings(result_keep_max="30d", result_keep_default="1d")
    _submit(setup.svc, keep_result="never")
    setup.answers["JOB_RESULT"] = CommandResult(ok=True, metadata={"status": "COMPLETED", "output": {"value": 1}})
    setup.svc.on_attempt(_attempt())
    assert setup.jobs.get("job-1").keep_result == "30d"


def test_a_full_bucket_means_the_result_is_not_saved_and_the_job_says_why(setup):
    _submit(setup.svc)
    setup.answers["JOB_RESULT"] = CommandResult(ok=True, metadata={"status": "COMPLETED", "output": {"value": 1}})

    class _Full:
        def put(self, job_id, value):
            raise ResultNotSaved("STORAGE_QUOTA_EXCEEDED: the bucket is full")

    setup.svc._results = _Full()
    setup.svc.on_attempt(_attempt())
    result = setup.jobs.get("job-1").result
    assert result["stored"] is False and "full" in result["reason"]


def test_results_past_their_keep_time_are_deleted_and_lowering_the_longest_time_shortens_them(setup):
    _submit(setup.svc, keep_result="never")
    setup.answers["JOB_RESULT"] = CommandResult(ok=True, metadata={"status": "COMPLETED", "output": {"value": 1}})
    setup.svc.on_attempt(_attempt())
    assert setup.jobs.get("job-1").keep_until is None
    assert setup.svc.shortened_by("1h") == 1

    setup.settings[0] = VeSettings(result_keep_max="1h", result_keep_default="1h")
    assert setup.svc.sweep() == 0                                     # shortened to 1 h after it finished
    assert setup.jobs.get("job-1").keep_until == "1970-01-12T14:46:40+00:00"
    setup.svc._now_fn = lambda: 1_000_000.0 + 7200                    # two hours later
    assert setup.svc.sweep() == 1
    job = setup.jobs.get("job-1")
    assert job.result["deleted_at"] and job.keep_result == "1h"
    with pytest.raises(NotFoundError):
        setup.svc.result("job-1")


def test_cancel_goes_to_the_endpoint_and_is_recorded(setup):
    _submit(setup.svc)
    assert setup.svc.cancel("job-1", "u2") == {"job_id": "job-1", "cancelled": True}
    assert setup.sent[-1] == ("JOB_CANCEL", {"job_id": "job-1", "cancelled_by": "u2"})
    assert setup.jobs.get("job-1").cancelled_by == "u2"
    assert setup.log.events[0][0] == "JobCancelRequested"


def test_only_a_failed_or_cancelled_standalone_job_can_be_retried_as_its_next_attempt(setup):
    _submit(setup.svc)
    with pytest.raises(ConflictError):
        setup.svc.retry("job-1", "u2", submit=lambda envelope: {})
    setup.svc.on_attempt(_attempt(outcome="failed_platform",
                                  failure={"error_class": "CONTAINER_CRASH_ERROR", "error_code": 3006,
                                           "component": "runtime", "message": "gone"}))
    sent = []
    answer = setup.svc.retry("job-1", "u2", submit=lambda envelope: sent.append(envelope) or {"endpoint_id": "ep-2"})
    assert answer == {"job_id": "job-1", "attempt": 2, "endpoint_id": "ep-2"}
    assert sent[0]["retried_by"] == "u2" and sent[0]["attempt"] == 2 and sent[0]["params"] == {"a": 1}
    job = setup.jobs.get("job-1")
    assert (job.status.value, job.attempt, job.retried_by, job.error) == ("QUEUED", 2, "u2", None)

    _submit(setup.svc, choreography={"run_id": "r1", "step": "s"})
    setup.svc.on_attempt(_attempt(attempt=2, outcome="failed_user"))
    with pytest.raises(ConflictError):
        setup.svc.retry("job-1", "u2", submit=lambda envelope: {})


def test_a_job_on_a_lost_endpoint_is_tried_again_elsewhere_then_fails_as_the_platforms(setup):
    _submit(setup.svc)
    sent = []

    def submit(envelope):
        sent.append(envelope)
        return {"endpoint_id": f"ep-{len(sent) + 1}"}

    assert setup.svc.rescue_lost(["ep-9"], submit) == 0                  # not its endpoint
    assert setup.svc.rescue_lost(["ep-1"], submit) == 1
    job = setup.jobs.get("job-1")
    assert job.status.value == "QUEUED" and job.attempt == 2 and job.endpoint_id == "ep-2"
    assert sent[0]["job_id"] == "job-1" and sent[0]["reason"] == "platform_failed" and sent[0]["attempt"] == 2
    assert setup.attempts.list("job-1")[0]["failure"]["error_class"] == "ENDPOINT_LOST"

    setup.svc.rescue_lost(["ep-2"], submit)                              # attempt 3 on ep-3
    setup.svc.rescue_lost(["ep-3"], submit)                              # 3 platform tries: done
    job = setup.jobs.get("job-1")
    assert job.status.value == "FAILED" and job.outcome == "failed_platform" and len(sent) == 2
    assert job.error["fault"] == "platform"
