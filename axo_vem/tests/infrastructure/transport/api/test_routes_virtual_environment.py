import pytest
from fastapi.testclient import TestClient

from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.events import models
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
from axo_vem.infrastructure.transport.api.app import create_app

from .conftest import CP_HEADERS, SERVICE_TOKEN


@pytest.fixture
def ve_client(collections, activity_repository, kurrent_appender):
    service = VeSettingsService("ml-prod", PlanLimits(max_endpoints=4, max_retention_days=30), kurrent_appender)
    app = create_app(
        collections=collections, activity_repository=activity_repository,
        current_user_dependency=build_service_token_dependency(SERVICE_TOKEN),
        ve_settings_service=service,
    )
    return TestClient(app, headers=CP_HEADERS)


def test_ve_shows_identity_limits_and_settings(ve_client):
    body = ve_client.get("/ve").json()
    assert body["virtual_environment_id"] == "ml-prod"
    assert body["plan_limits"]["max_endpoints"] == 4
    assert body["settings"]["target_endpoints"] == 1
    assert body["effective_retention_days"] == 30


def test_patch_changes_only_given_fields_and_lands_in_activity(ve_client):
    response = ve_client.patch("/ve/settings", json={"target_endpoints": 3, "reason": "launch week"})
    assert response.status_code == 200
    assert response.json()["settings"]["target_endpoints"] == 3
    assert ve_client.get("/ve/settings").json()["min_endpoints"] == 1

    history = ve_client.get("/history").json()
    change = next(e for e in history if e["event_type"] == models.VE_SETTINGS_CHANGED)
    assert change["user_id"] == "user-1"


def test_patch_beyond_the_plan_is_409_with_the_reason(ve_client):
    response = ve_client.patch("/ve/settings", json={"target_endpoints": 5, "retention_days": 60})
    assert response.status_code == 409
    assert "plan maximum (4)" in response.json()["detail"]
    assert "plan maximum (30)" in response.json()["detail"]
