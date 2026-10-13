import pytest
from fastapi.testclient import TestClient

from axo_vem.application.policies.policy_service import PolicyService
from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
from axo_vem.infrastructure.transport.api.app import create_app

from .conftest import CP_HEADERS, SERVICE_TOKEN


@pytest.fixture
def client(collections, activity_repository, kurrent_appender):
    policies = PolicyService("ve", kurrent_appender, policies_allowed=True)
    app = create_app(
        collections=collections, activity_repository=activity_repository,
        current_user_dependency=build_service_token_dependency(SERVICE_TOKEN), policy_service=policies,
    )
    return TestClient(app, headers=CP_HEADERS)


def test_create_from_a_template_change_it_and_delete_it(client):
    created = client.post("/policies", json={"template": "keep-response-time-under", "name": "fast", "threshold": 300})
    assert created.status_code == 201
    body = created.json()
    assert body["condition"] == "p95 job.total_ms > 300" and body["dry_run"] is False

    changed = client.patch(f"/policies/{body['policy_id']}", json={"dry_run": True})
    assert changed.json()["dry_run"] is True
    assert [p["name"] for p in client.get("/policies").json()["policies"]] == ["fast"]

    assert client.delete(f"/policies/{body['policy_id']}").status_code == 204
    assert client.get("/policies").json()["policies"] == []


def test_invalid_policies_and_unknown_templates(client):
    assert client.post("/policies", json={"template": "nope", "name": "x"}).status_code == 404
    bad = client.post("/policies", json={"template": "keep-cpu-under", "name": "x", "delta": 0})
    assert bad.status_code == 409 and "delta" in bad.json()["detail"]


def test_templates_and_pause(client):
    assert {t["template"] for t in client.get("/policies/templates").json()} >= {"keep-queue-short", "remove-when-quiet"}
    assert client.post("/policies/pause", json={"minutes": 10}).json()["paused_until"] is not None
    assert client.get("/policies").json()["pause_reason"] == "paused by user-1"
