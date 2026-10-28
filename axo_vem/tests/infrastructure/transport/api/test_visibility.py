"""Who sees runs and results: Admins / Owner everything, others what they
started plus what a function's / choreography's / object's setting shares."""

import pytest
from fastapi.testclient import TestClient

from axo_vem.application.jobs.results import MongoJobResultStore
from axo_vem.application.jobs.service import JobsService
from axo_vem.application.visibility import VisibilityService
from axo_vem.domain.execution.job import JobStatus
from axo_vem.domain.virtual_environment.settings import VeSettings
from axo_vem.infrastructure.auth.service_token import (
    ACTING_ROLE_HEADER, ACTING_TEAMS_HEADER, ACTING_USER_HEADER, build_service_token_dependency,
)
from axo_vem.infrastructure.database.mongo.job_attempt_repository import MongoJobAttemptRepository
from axo_vem.infrastructure.transport.api.app import create_app
from tests.infrastructure.transport.api.conftest import CP_HEADERS, SERVICE_TOKEN

# made-up people: ana registered "resize" and runs it; ben is a Developer in team-blue
ANA = {ACTING_USER_HEADER: "ana", ACTING_ROLE_HEADER: "developer"}
BEN = {ACTING_USER_HEADER: "ben", ACTING_ROLE_HEADER: "developer", ACTING_TEAMS_HEADER: "team-blue"}
ADMIN = {ACTING_USER_HEADER: "carla", ACTING_ROLE_HEADER: "admin"}


@pytest.fixture
def client(db, collections, activity_repository, job_repository):
    attempts = MongoJobAttemptRepository(db["job_attempts"])
    jobs = JobsService(job_repository, attempts, MongoJobResultStore(db["job_results"]), endpoints=None,
                       settings_fn=VeSettings)
    creators = {("function", "resize"): "ana"}
    visibility = VisibilityService(db["result_visibility"], creator_of=lambda kind, ref: creators.get((kind, ref)))
    app = create_app(collections=collections, activity_repository=activity_repository,
                     job_repository=job_repository, jobs_service=jobs, visibility_service=visibility,
                     current_user_dependency=build_service_token_dependency(SERVICE_TOKEN))
    for job_id, who in (("job-ana", "ana"), ("job-ben", "ben")):
        job_repository.submitted(job_id, {"function_id": "resize", "function_version": 1, "acting_user_id": who,
                                          "created_at": f"2026-01-01T00:00:0{len(who)}", "row_id": job_id})
        job_repository.advance(job_id, 1, JobStatus.COMPLETED, {"outcome": "completed"})
        job_repository.update(job_id, {"result": {"stored": True, "name": f"{job_id}.json", "size": 10,
                                                  "saved_at": f"2026-01-01T00:01:0{len(who)}", "deleted_at": None}})
    return TestClient(app, headers=CP_HEADERS)


def _ids(client, headers):
    return sorted(r["job"]["job_id"] for r in client.get("/jobs", headers=headers).json()["rows"])


def test_by_default_each_person_sees_only_what_they_started(client):
    assert _ids(client, ANA) == ["job-ana"]
    assert _ids(client, ADMIN) == ["job-ana", "job-ben"]
    refused = client.get("/jobs/job-ana", headers=BEN)
    assert refused.status_code == 403 and refused.json()["error"] == "RESULT_NOT_VISIBLE"
    assert client.get("/jobs/job-ana/result", headers=BEN).status_code == 403
    assert client.get("/jobs/job-ana", headers=ADMIN).status_code == 200


def test_the_creator_shares_runs_with_a_team(client):
    assert client.put("/visibility/function/resize", json={"mode": "teams", "teams": ["team-blue"]},
                      headers=BEN).json()["error"] == "RESULT_VISIBILITY_FORBIDDEN"
    shared = client.put("/visibility/function/resize", json={"mode": "teams", "teams": ["team-blue"]}, headers=ANA)
    assert shared.status_code == 200 and shared.json()["mode"] == "teams"
    assert _ids(client, BEN) == ["job-ana", "job-ben"]
    assert client.get("/jobs/job-ana", headers=BEN).status_code == 200
    stranger = {ACTING_USER_HEADER: "dora", ACTING_ROLE_HEADER: "viewer"}
    assert _ids(client, stranger) == []

    client.put("/visibility/function/resize", json={"mode": "ve"}, headers=ADMIN)
    assert _ids(client, stranger) == ["job-ana", "job-ben"]
    assert client.get("/visibility/function/resize", headers=stranger).json()["can_change"] is False


def test_the_creator_is_found_for_each_kind_the_way_the_server_wires_it(db):
    from axo_vem.application.visibility import creator_lookup
    from axo_vem.domain.choreography.choreography import Choreography
    from axo_vem.domain.events.models import ChoreographyGraph
    from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository

    db["functions"].insert_many([{"_id": "resize:2", "function_id": "resize", "version": 2, "owner_user_id": "ben"},
                                 {"_id": "resize:1", "function_id": "resize", "version": 1, "owner_user_id": "ana"}])
    db["active_objects"].insert_one({"_id": "cart-42", "created_by": "ben"})
    choreographies = MongoChoreographyRepository(db["choreographies"])
    choreographies.save(Choreography(choreography_id="c-1", name="charts", owner_user_id="dora", version=1,
                                     graph=ChoreographyGraph(nodes=[], edges=[])))
    creator_of = creator_lookup(db["functions"], db["active_objects"], choreographies)
    assert creator_of("function", "resize") == "ana"
    assert creator_of("object", "cart-42") == "ben"
    assert creator_of("choreography", "c-1") == "dora"
    assert creator_of("function", "nothing") is None


def test_the_results_bucket_lists_only_what_each_person_may_see(client):
    mine = client.get("/results", headers=ANA).json()
    assert [r["job_id"] for r in mine["results"]] == ["job-ana"] and (mine["files"], mine["bytes"]) == (1, 10)
    everything = client.get("/results", headers=ADMIN).json()
    assert everything["files"] == 2 and [r["name"] for r in everything["results"]] == ["job-ben.json", "job-ana.json"]
