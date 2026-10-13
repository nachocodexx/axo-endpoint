"""The Control Plane's endpoint commands: create N of size X, remove one, restart one."""
import pytest
from fastapi.testclient import TestClient

from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
from axo_vem.infrastructure.transport.api.app import create_app

from tests.application.virtual_environment.test_reconciler import Clock, FakeOps, _Log

from .conftest import CP_HEADERS, SERVICE_TOKEN


@pytest.fixture
def world(collections, activity_repository):
    clock = Clock()
    log = _Log(clock)
    settings = VeSettingsService("ve", PlanLimits(max_endpoints=3), log)
    fleet = FleetService("ve", log, now_fn=clock)
    ops = FakeOps(clock)
    reconciler = Reconciler(settings, fleet, ops, JobTracker(now_fn=clock), now_fn=clock)
    app = create_app(
        collections=collections, activity_repository=activity_repository,
        current_user_dependency=build_service_token_dependency(SERVICE_TOKEN),
        ve_settings_service=settings, reconciler=reconciler, fleet=fleet,
    )
    return TestClient(app, headers=CP_HEADERS), reconciler, ops, clock, settings


def test_create_raises_the_target_and_lists_the_new_endpoints(world):
    client, reconciler, ops, clock, settings = world
    reconciler.tick()                                   # target 1 -> ep-1
    response = client.post("/endpoints", json={"count": 2, "cpu_cores": 2.0, "memory_mb": 4096})

    assert response.status_code == 201
    assert [e["endpoint_id"] for e in response.json()] == ["ep-2", "ep-3"]
    assert response.json()[0]["cpu_cores"] == 2.0 and response.json()[0]["lifecycle"] == "creating"
    assert settings.current().target_endpoints == 3
    listed = {e["endpoint_id"]: e["lifecycle"] for e in client.get("/endpoints").json()}
    assert listed == {"ep-1": "creating", "ep-2": "creating", "ep-3": "creating"}


def test_create_beyond_the_plan_is_409(world):
    client, *_ = world
    response = client.post("/endpoints", json={"count": 3})
    assert response.status_code == 409
    assert "plan maximum (3)" in response.json()["detail"]


def test_remove_drains_that_endpoint_and_lowers_the_target(world):
    client, reconciler, ops, clock, settings = world
    settings.change({"target_endpoints": 2})
    reconciler.tick()
    clock.advance(1)
    ops.report_in("ep-1", "ep-2")
    reconciler.tick()

    response = client.delete("/endpoints/ep-1", params={"reason": "too many"})
    assert response.status_code == 200 and response.json()["lifecycle"] == "draining"
    assert settings.current().target_endpoints == 1
    reconciler.tick()
    assert client.get("/endpoints/ep-1").json()["lifecycle"] == "removed"
    assert "ep-1" not in {e["endpoint_id"] for e in client.get("/endpoints").json()}


def test_restart_and_unknown_endpoints(world):
    client, reconciler, *_ = world
    reconciler.tick()
    assert client.post("/endpoints/ep-1/restart").json()["lifecycle"] == "starting"
    assert client.post("/endpoints/nope/restart").status_code == 404
    assert client.delete("/endpoints/nope").status_code == 404
