import json

import httpx
import pytest
from fastapi.testclient import TestClient

from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.infrastructure.api.app import create_app
from axo_control_plane.infrastructure.api.deps import Services


class FakeObjectStore:
    def __init__(self):
        self.objects = {}

    def list_objects(self, user, bucket, prefix=None):
        return [{"name": n, "version": v} for (b, n), (v, _) in self.objects.items() if b == bucket]

    def upload(self, user, bucket, name, data):
        version = self.objects.get((bucket, name), (0, b""))[0] + 1
        self.objects[(bucket, name)] = (version, data)
        return {"bucket": bucket, "name": name, "version": version, "total_size": len(data)}

    def object_info(self, user, bucket, name, version):
        return {"version": self.objects[(bucket, name)][0]}

    def download(self, user, bucket, name, version):
        yield self.objects[(bucket, name)][1]

    def delete_object(self, user, bucket, name, version):
        self.objects.pop((bucket, name), None)
        return {"deleted": True}


@pytest.fixture
def api(platform):
    seen = []

    def vem(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path, "acting": request.headers.get("x-acting-user"),
                                         "auth": request.headers.get("authorization"),
                                         "body": json.loads(request.content or b"null")})

    sleep = SleepManager(platform.db, platform.catalog, platform.vem, platform.leases, platform.notifications,
                         platform.history, instance_id="cp-1", now_fn=platform.clock)
    svc = Services(
        people=platform.people, teams=platform.teams, ves=platform.ves, buckets=platform.buckets,
        sharing=platform.sharing, keys=platform.keys, notifications=platform.notifications,
        catalog=platform.catalog, capacity=platform.capacity,
        front_door=FrontDoor(platform.db, platform.authz, sleep, platform.catalog, platform.notifications,
                             now_fn=platform.clock),
        sleep=sleep, storage_access=platform.storage_access, cleanup=None,
        vem_base_url=lambda ve_id: f"http://ve-{ve_id}-vem:8080",
        vem_headers=lambda ve_id, user: {"Authorization": f"Bearer vem-token-{ve_id}", "X-Acting-User": user},
        vem_ws_url=lambda ve_id, path: f"ws://ve-{ve_id}-vem:8080{path}?token=x",
        object_store=FakeObjectStore(), storage_check_token="storage-secret", db=platform.db,
    )
    app = create_app(svc, http_client=httpx.AsyncClient(transport=httpx.MockTransport(vem)))
    with TestClient(app) as client:
        client.seen = seen
        yield client


def _login(api, platform, username):
    platform.user(username)
    data = api.post("/auth/login", json={"username": username, "password": "password123"}).json()
    return {"Authorization": f"Bearer {data['access_token']}", "Temporal-Secret-Key": data["temporal_secret"]}


def test_signup_login_me_logout(api, platform):
    assert api.post("/auth/signup", json={"username": "ana", "email": "ana@x.io", "password": "short"}).status_code == 422
    assert api.post("/auth/signup", json={"username": "ana", "email": "ana@x.io",
                                          "password": "password123"}).status_code == 201
    session = api.post("/auth/login", json={"username": "ana", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {session['access_token']}", "Temporal-Secret-Key": session["temporal_secret"]}
    assert api.get("/me", headers=headers).json()["username"] == "ana"
    assert api.post("/auth/logout", headers=headers).status_code == 204
    assert api.get("/me", headers=headers).status_code == 401
    assert api.get("/ves").status_code == 401


def test_admin_must_change_password_first(api, platform):
    session = api.post("/auth/login", json={"username": "admin", "password": "admin-password"}).json()
    headers = {"Authorization": f"Bearer {session['access_token']}", "Temporal-Secret-Key": session["temporal_secret"]}
    assert session["user"]["password_change_required"] is True
    assert api.get("/admin/capacity", headers=headers).json()["error"] == "PASSWORD_CHANGE_REQUIRED"
    assert api.post("/auth/password", headers=headers, json={"new_password": "a-better-one"}).status_code == 204
    assert api.get("/admin/capacity", headers=headers).json()["total"]["cpu_cores"] == 32.0


def test_buy_and_pass_through_to_the_vem(api, platform):
    headers = _login(api, platform, "alice")
    created = api.post("/ves", headers=headers, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 2,
                                                       "quota_memory_mb": 2048, "target_endpoints": 1})
    assert created.status_code == 201
    ve_id = created.json()["ve"]["ve_id"]
    # Still being created: the front door says so instead of waiting.
    assert api.get(f"/ves/{ve_id}/api/functions", headers=headers).status_code == 503
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "lifecycle": V.RUNNING, "heartbeat_at": platform.clock()}})
    answer = api.post(f"/ves/{ve_id}/api/functions/f1/1/jobs", headers=headers, json={"params": {"x": 1}}).json()
    assert answer["path"] == "/functions/f1/1/jobs" and answer["body"] == {"params": {"x": 1}}
    assert answer["acting"] == platform.people.find("alice")["user_id"]
    assert answer["auth"] == f"Bearer vem-token-{ve_id}"
    assert api.post(f"/ves/{ve_id}/api/ve/stop", headers=headers).status_code == 404     # not in the table
    bob = _login(api, platform, "bob")
    assert api.get(f"/ves/{ve_id}/api/functions", headers=bob).status_code == 404        # no hint it exists


def test_sdk_key_through_the_front_door(api, platform):
    headers = _login(api, platform, "alice")
    ve_id = api.post("/ves", headers=headers, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 2,
                                                    "quota_memory_mb": 2048}).json()["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "lifecycle": V.RUNNING, "heartbeat_at": platform.clock()}})
    key = api.post("/sdk-keys", headers=headers, json={"name": "ci", "ve_id": ve_id, "max_role": "viewer"}).json()["key"]
    assert api.get(f"/ves/{ve_id}/api/functions", headers={"X-API-Key": key}).status_code == 200
    assert api.post(f"/ves/{ve_id}/api/functions/f/1/jobs", headers={"X-API-Key": key}, json={}).status_code == 403
    assert api.post("/sdk-keys", headers={"X-API-Key": key}, json={"name": "x", "max_role": "viewer"}).status_code == 403


def test_buckets_pages_and_the_storage_question(api, platform):
    headers = _login(api, platform, "alice")
    bucket = api.post("/buckets", headers=headers, json={"name": "reports"}).json()
    assert bucket["name"] == "alice/reports"
    put = api.put("/buckets/alice/reports/objects/2026/q1.csv", headers=headers, content=b"a,b\n1,2\n")
    assert put.status_code == 201 and put.json()["version"] == 1
    got = api.get("/buckets/alice/reports/objects/2026/q1.csv", headers=headers)
    assert got.content == b"a,b\n1,2\n" and got.headers["x-object-version"] == "1"
    question = {"user_id": platform.people.find("alice")["user_id"], "bucket": "alice/reports", "mode": "write"}
    assert api.post("/internal/storage/access", json=question).status_code == 401
    answer = api.post("/internal/storage/access", json=question,
                      headers={"Authorization": "Bearer storage-secret"}).json()
    assert answer["allowed"] and answer["bucket_key"] == bucket["bucket_id"]


def test_errors_are_json_with_a_code(api, platform):
    headers = _login(api, platform, "alice")
    response = api.post("/ves", headers=headers, json={"name": "big", "plan_id": "basic", "quota_cpu_cores": 1,
                                                        "quota_memory_mb": 1024, "target_endpoints": 9})
    assert response.status_code == 422 and response.json()["error"] == "PLAN_LIMIT"
