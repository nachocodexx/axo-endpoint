import pytest
from fastapi.testclient import TestClient

from axo_vem.application.jobs.results import MongoJobResultStore
from axo_vem.application.jobs.service import JobsService
from axo_vem.domain.execution.job import JobStatus
from axo_vem.domain.virtual_environment.settings import VeSettings
from axo_vem.infrastructure.auth.service_token import ACTING_ROLE_HEADER, build_service_token_dependency
from axo_vem.infrastructure.database.mongo.job_attempt_repository import MongoJobAttemptRepository
from axo_vem.infrastructure.transport.api.app import create_app
from tests.infrastructure.transport.api.conftest import CP_HEADERS, SERVICE_TOKEN


@pytest.fixture
def jobs_client(db, collections, activity_repository, job_repository):
    attempts = MongoJobAttemptRepository(db["job_attempts"])
    service = JobsService(job_repository, attempts, MongoJobResultStore(db["job_results"]), endpoints=None,
                          settings_fn=VeSettings)
    app = create_app(collections=collections, activity_repository=activity_repository,
                     job_repository=job_repository, jobs_service=service,
                     current_user_dependency=build_service_token_dependency(SERVICE_TOKEN))
    return TestClient(app, headers=CP_HEADERS), job_repository, attempts


def _job(repo, job_id, at, run=None, status=JobStatus.COMPLETED, function_id="add"):
    repo.submitted(job_id, {"function_id": function_id, "function_version": 1, "created_at": at,
                            "choreography": {"run_id": run, "step": job_id} if run else None,
                            "row_id": f"run:{run}" if run else job_id, "row_at": "2026-01-01T00:00:00" if run else at})
    repo.advance(job_id, 1, status, {"outcome": "completed" if status == JobStatus.COMPLETED else "failed_user"})


def test_standalone_jobs_are_rows_and_a_runs_jobs_are_one_row_newest_first(jobs_client):
    client, repo, _ = jobs_client
    _job(repo, "job-a", "2026-01-01T00:00:01")
    _job(repo, "step-1", "2026-01-01T00:00:02", run="run-1")
    _job(repo, "step-2", "2026-01-01T00:00:03", run="run-1", status=JobStatus.FAILED)
    _job(repo, "job-b", "2026-01-01T00:00:04")

    page = client.get("/jobs").json()
    assert [(r["type"], r["row_id"]) for r in page["rows"]] == [
        ("job", "job-b"), ("job", "job-a"), ("run", "run:run-1")]
    assert [j["job_id"] for j in page["rows"][2]["jobs"]] == ["step-2", "step-1"]

    failed = client.get("/jobs", params={"status": "FAILED"}).json()["rows"]
    assert [(r["type"], r["shown"]) for r in failed] == [("run", 1)]     # the run keeps only its matching step


def test_a_called_runs_jobs_go_in_the_row_of_the_outermost_run(jobs_client):
    client, repo, _ = jobs_client
    service = JobsService(repo, None, None, endpoints=None, settings_fn=VeSettings)
    service.submitted("outer-job", function_id="add", function_version=1, params={}, endpoint_id=None,
                      acting_user_id="ana", started_by_type="choreography", started_by_id="run-1",
                      choreography={"run_id": "run-1", "step": "load", "run_started_at": "2026-01-01T00:00:00"})
    service.submitted("inner-job", function_id="add", function_version=1, params={}, endpoint_id=None,
                      acting_user_id="ana", started_by_type="choreography", started_by_id="sub-1",
                      choreography={"run_id": "sub-1", "step": "clean", "root_run_id": "run-1", "root_step": "visits",
                                    "run_started_at": "2026-01-01T00:00:01"})
    rows = client.get("/jobs").json()["rows"]
    assert [(r["row_id"], r["run"]["run_id"]) for r in rows] == [("run:run-1", "run-1")]
    assert sorted(j["job_id"] for j in rows[0]["jobs"]) == ["inner-job", "outer-job"]
    # one run asked for: its jobs and those of the runs its steps called
    one = client.get("/jobs", params={"run_id": "run-1"}).json()["rows"]
    assert sorted(j["job_id"] for j in one[0]["jobs"]) == ["inner-job", "outer-job"]


def test_pages_continue_where_the_last_one_ended(jobs_client):
    client, repo, _ = jobs_client
    for i in range(5):
        _job(repo, f"job-{i}", f"2026-01-01T00:00:0{i}")
    first = client.get("/jobs", params={"limit": 2}).json()
    assert [r["row_id"] for r in first["rows"]] == ["job-4", "job-3"]
    second = client.get("/jobs", params={"limit": 2, **first["next"]}).json()
    assert [r["row_id"] for r in second["rows"]] == ["job-2", "job-1"]
    third = client.get("/jobs", params={"limit": 2, **second["next"]}).json()
    assert [r["row_id"] for r in third["rows"]] == ["job-0"] and third["next"] is None


def test_a_traceback_is_shown_only_to_developers_and_above(jobs_client):
    client, repo, attempts = jobs_client
    _job(repo, "job-x", "2026-01-01T00:00:01", status=JobStatus.FAILED)
    attempts.save({"job_id": "job-x", "attempt": 1, "outcome": "failed_user",
                   "failure": {"error_class": "FUNCTION_EXEC_ERROR", "error_code": 3002, "component": "runtime",
                               "message": "boom", "traceback": "Traceback (most recent call last) ..."}})
    viewer = client.get("/jobs/job-x", headers={ACTING_ROLE_HEADER: "viewer"}).json()
    developer = client.get("/jobs/job-x", headers={ACTING_ROLE_HEADER: "developer"}).json()
    assert viewer["attempts"][0]["failure"]["traceback"] is None
    assert developer["attempts"][0]["failure"]["traceback"].startswith("Traceback")


def test_a_result_that_is_not_kept_answers_its_own_error(jobs_client):
    client, repo, _ = jobs_client
    _job(repo, "job-y", "2026-01-01T00:00:01", status=JobStatus.FAILED)
    answer = client.get("/jobs/job-y/result")
    assert answer.status_code == 404 and answer.json()["error"] == "JOB_RESULT_NOT_KEPT"
