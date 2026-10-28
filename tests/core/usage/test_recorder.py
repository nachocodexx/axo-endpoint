import pytest

from axo_shared.events import models as event_models

from axo_endpoint.core.errors import (
    ContainerCrashError,
    FunctionExecError,
    JobCancelledError,
    JobTimeoutError,
    QueueWaitTimeoutError,
    WorkerCrashedError,
)
from axo_endpoint.core.events.bus import Event as BusEvent
from axo_endpoint.core.usage import JobUsageContext, UsageRecorder, classify_outcome


@pytest.mark.parametrize("error,outcome", [
    (None, "completed"),
    (FunctionExecError("boom"), "failed_user"),
    (JobTimeoutError("too slow"), "failed_user"),
    (JobCancelledError("stop"), "cancelled"),
    (QueueWaitTimeoutError("waited"), "timed_out_in_queue"),
    (WorkerCrashedError("segv"), "failed_platform"),
    (ContainerCrashError("oom"), "failed_platform"),
])
def test_classify_outcome(error, outcome):
    assert classify_outcome(error) == outcome


def _recorder(published, now=1000.0):
    return UsageRecorder(
        endpoint_id="ep-1", virtual_environment_id="ve-a", owner_type="team", owner_id="ml-lab",
        endpoint_cores=2.0, endpoint_ram_mb=4096,
        publish=lambda event_type, data: published.append((event_type, data)),
        now_fn=lambda: now,
    )


def _submitted(job_id="job-1", **envelope):
    context = JobUsageContext.from_envelope(
        {"function_id": "add", "function_version": 3, **envelope}, job_id, now=100.0,
    )
    return BusEvent(event_type="JOB_SUBMITTED", payload={"job_id": job_id, "usage": context.to_dict()}, timestamp=100.0)


def _finished(job_id="job-1", outcome="completed", started_at=100.5, finished_at=102.5):
    event_type = "JOB_COMPLETED" if outcome == "completed" else "JOB_FAILED"
    return BusEvent(event_type=event_type, payload={
        "job_id": job_id, "outcome": outcome, "started_at": started_at, "finished_at": finished_at,
    }, timestamp=finished_at)


def test_completed_job_produces_one_billable_record():
    published = []
    recorder = _recorder(published)
    recorder.on_job_submitted(_submitted(trace_id="t-9", started_by_type="api_key", started_by_id="k-1",
                                         layer_waits_ms={"control_plane": 5.0, "vem": 3.0}))
    recorder.on_job_finished(_finished())

    (event_type, data), = published
    assert event_type == event_models.JOB_ATTEMPT_RECORDED
    record = event_models.JobAttemptRecorded(**data)
    assert (record.job_id, record.attempt, record.trace_id, record.outcome) == ("job-1", 1, "t-9", "completed")
    assert record.billable
    assert (record.owner_type, record.owner_id, record.virtual_environment_id) == ("team", "ml-lab", "ve-a")
    assert (record.started_by_type, record.started_by_id) == ("api_key", "k-1")
    assert (record.endpoint_cores, record.endpoint_ram_mb) == (2.0, 4096)
    assert record.layer_waits_ms == {"control_plane": 5.0, "vem": 3.0, "endpoint": 500.0}
    assert record.execution_ms == 2000.0
    assert recorder.pending_count() == 0


def test_job_that_never_started_has_no_execution_time():
    published = []
    recorder = _recorder(published)
    recorder.on_job_submitted(_submitted())
    recorder.on_job_finished(_finished(outcome="timed_out_in_queue", started_at=None))
    record = event_models.JobAttemptRecorded(**published[0][1])
    assert record.execution_ms == 0.0 and "endpoint" not in record.layer_waits_ms
    assert not record.billable


def test_finish_without_known_context_records_nothing():
    published = []
    _recorder(published).on_job_finished(_finished(job_id="unknown"))
    assert published == []


def test_forward_hands_context_over_and_target_records_it():
    origin_published, target_published = [], []
    origin, target = _recorder(origin_published), _recorder(target_published)
    origin.on_job_submitted(_submitted(trace_id="t-1"))

    usage = origin.take_for_forward("job-1")
    assert origin.pending_count() == 0
    forwarded = JobUsageContext.from_envelope({"usage": usage, "function_id": "add"}, "job-1", now=999.0)
    target.on_job_submitted(BusEvent(event_type="JOB_SUBMITTED",
                                     payload={"job_id": "job-1", "usage": forwarded.to_dict()}, timestamp=101.0))
    target.on_job_finished(_finished())

    assert origin_published == []
    record = event_models.JobAttemptRecorded(**target_published[0][1])
    assert record.trace_id == "t-1"
    assert record.received_at.timestamp() == 100.0  # origin's received time, not the target's


def test_restore_after_failed_forward():
    recorder = _recorder([])
    recorder.on_job_submitted(_submitted())
    usage = recorder.take_for_forward("job-1")
    recorder.restore(usage)
    assert recorder.pending_count() == 1


def test_retried_attempts_are_each_recorded():
    published = []
    recorder = _recorder(published, now=101.0)
    recorder.on_job_submitted(_submitted())
    recorder.on_attempt_failed("job-1", JobTimeoutError("slow"), 100.2, 103.0, "code_failed")
    recorder.on_attempt_failed("job-1", ContainerCrashError("oom"), 101.0, 104.0, "platform_failed")
    recorder.on_job_finished(_finished(started_at=101.5, finished_at=102.0))

    records = [event_models.JobAttemptRecorded(**data) for _, data in published]
    assert [(r.attempt, r.outcome, r.billable, r.reason) for r in records] == [
        (1, "failed_user", True, "first"),
        (2, "failed_platform", False, "code_failed"),
        (3, "completed", True, "platform_failed"),
    ]
    assert (records[0].failure.error_class, records[0].failure.message) == (JobTimeoutError.name, "slow")
    assert records[0].retry_at.timestamp() == 103.0 and records[2].retry_at is None
    assert len({r.event_id for r in records}) == 3  # distinct ids: none deduplicated away


def test_files_a_job_wrote_go_on_its_attempt():
    published = []
    recorder = _recorder(published)
    recorder.on_job_submitted(_submitted())
    recorder.on_io("job-1", "reports", bytes_written=40,
                   file={"bucket": "reports", "name": "out.csv", "version": 2, "size": 40, "format": "csv"})
    recorder.on_job_finished(_finished())
    record = event_models.JobAttemptRecorded(**published[0][1])
    assert [(f.name, f.version, f.size) for f in record.files_written] == [("out.csv", 2, 40)]


def test_from_envelope_defaults():
    context = JobUsageContext.from_envelope({"function_id": "f", "function_version": 1, "user_id": "u-1"}, "j", now=5.0)
    assert (context.trace_id, context.attempt, context.received_at) == ("j", 1, 5.0)
    assert (context.started_by_type, context.started_by_id) == ("user", "u-1")


def test_bytes_per_bucket_and_cache_hits_go_on_the_attempt():
    published = []
    recorder = _recorder(published)
    recorder.on_job_submitted(_submitted())
    recorder.on_io("job-1", "sales", bytes_read=100, cache_hit=False)
    recorder.on_io("job-1", "sales", cache_hit=True)
    recorder.on_io("job-1", "reports", bytes_written=40)
    recorder.on_io("unknown-job", "sales", bytes_read=5)        # not running here: ignored
    recorder.on_job_finished(_finished())
    record = event_models.JobAttemptRecorded(**published[0][1])
    assert record.bytes_read == {"sales": 100} and record.bytes_written == {"reports": 40}
    assert (record.cache_hits, record.cache_misses) == (1, 1)
