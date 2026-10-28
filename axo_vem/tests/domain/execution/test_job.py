from axo_vem.domain.execution.job import FAULTS, Job, JobStatus, status_for_outcome


def test_a_job_round_trips_through_its_dict():
    job = Job(job_id="j1", function_id="add", status=JobStatus.WAITING_RETRY, function_version=2, attempt=2,
              max_attempts=3, next_attempt_at="2026-01-01T00:00:05+00:00", params={"a": 1})
    data = job.to_dict()
    assert data["status"] == "WAITING_RETRY" and data["attempt"] == 2
    assert Job.from_dict({**data, "_id": "j1", "phase": 2}) == job


def test_only_queued_running_and_waiting_jobs_are_active():
    assert [s.value for s in JobStatus if Job(job_id="j", function_id="f", status=s).active] == [
        "QUEUED", "RUNNING", "WAITING_RETRY"]


def test_outcomes_map_to_statuses_and_plain_fault_labels():
    assert status_for_outcome("completed") == JobStatus.COMPLETED
    assert status_for_outcome("cancelled") == JobStatus.CANCELLED
    assert status_for_outcome("failed_user") == JobStatus.FAILED
    assert FAULTS == {"failed_user": "code", "failed_platform": "platform", "timed_out_in_queue": "queue",
                      "cancelled": "cancelled"}
