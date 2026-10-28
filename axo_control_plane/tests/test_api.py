import json

import httpx
import pytest
from fastapi.testclient import TestClient

from axo_control_plane.application.bucket_files import BucketFiles
from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.billing_views import BillingViews
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.storage_metering import StorageMetering
from axo_control_plane.application.metric_tiers import cp_tiers
from axo_control_plane.application.statistics import Statistics, VeStatistics
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.infrastructure.api.app import create_app
from axo_control_plane.infrastructure.api.deps import Services


@pytest.fixture
def api(platform):
    seen = []

    def vem(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/trigger-buckets":
            return httpx.Response(200, json={"triggers": [{"bucket": "alice/in", "created_by": "u1"}],
                                             "at": "2030-01-01T00:00:00+00:00"})
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
        object_store=platform.storage, storage_check_token="storage-secret", db=platform.db,
        bucket_files=BucketFiles(platform.db, platform.buckets, platform.storage, platform.people,
                                 now_fn=platform.clock),
        statistics=VeStatistics(Statistics(platform.db, platform.catalog, cp_tiers(), now_fn=platform.clock),
                                platform.authz, list_fn=platform.ves.list_for),
        billing=BillingViews(platform.db, platform.authz, platform.teams, platform.ves,
                             VeBills(platform.db, platform.history, now_fn=platform.clock),
                             StorageMetering(platform.db, platform.catalog, platform.history, now_fn=platform.clock),
                             Statistics(platform.db, platform.catalog, cp_tiers(), now_fn=platform.clock),
                             now_fn=platform.clock, bucket_stats=platform.storage.bucket_stats),
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
    api.post("/teams", headers=headers, json={"name": "lab"})
    assert [(t["name"], t["role"]) for t in api.get("/me", headers=headers).json()["teams"]] == [("lab", "owner")]
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
    assert float(api.seen[-1].headers["x-axo-wait-ms"]) >= 0        # the CP's own wait
    api.get(f"/ves/{ve_id}/api/functions", headers=headers)
    assert "x-axo-wait-ms" not in api.seen[-1].headers                # only on submissions
    assert answer["acting"] == platform.people.find("alice")["user_id"]
    assert answer["auth"] == f"Bearer vem-token-{ve_id}"
    assert api.post(f"/ves/{ve_id}/api/ve/stop", headers=headers).status_code == 404     # not in the table
    bob = _login(api, platform, "bob")
    assert api.get(f"/ves/{ve_id}/api/functions", headers=bob).status_code == 404        # no hint it exists


def test_a_trigger_change_is_known_before_the_answer(api, platform):
    """Saving a choreography (or adding a trigger) reads the VEM's trigger
    list before answering: a file written right after is routed at once."""
    headers = _login(api, platform, "alice")
    ve_id = api.post("/ves", headers=headers, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 2,
                                                    "quota_memory_mb": 2048}).json()["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "lifecycle": V.RUNNING, "heartbeat_at": platform.clock()}})
    answer = api.post(f"/ves/{ve_id}/api/choreographies/flow/triggers", headers=headers, json={"bucket": "alice/in"})
    assert answer.status_code == 200 and answer.json()["path"] == "/choreographies/flow/triggers"
    assert [r.url.path for r in api.seen[-2:]] == ["/choreographies/flow/triggers", "/trigger-buckets"]
    assert platform.db["virtual_environments"].find_one({"_id": ve_id})["triggers"] == [
        {"bucket": "alice/in", "created_by": "u1"}]
    api.get(f"/ves/{ve_id}/api/choreographies", headers=headers)      # reading changes nothing
    assert api.seen[-1].url.path == "/choreographies"


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


def test_the_api_page_has_an_authorize_button_for_a_key_or_a_session(api, platform):
    spec = api.get("/openapi.json").json()
    assert set(spec["components"]["securitySchemes"]) == {"API key", "Session token", "Session secret"}
    headers = _login(api, platform, "kim")
    key = api.post("/sdk-keys", headers=headers, json={"name": "laptop"}).json()["key"]
    assert api.get("/me", headers={"X-API-Key": key}).status_code == 200
    assert api.get("/me", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert api.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_notifications_page_by_page_filtered_by_group(api, platform):
    headers = _login(api, platform, "nora")
    user_id = api.get("/me", headers=headers).json()["user_id"]
    for i in range(5):
        platform.clock.t += 1
        platform.notifications.notify([user_id], "ve_state" if i % 2 else "bill_closed", f"n{i}")
    first = api.get("/notifications", headers=headers, params={"limit": 2}).json()
    assert [n["text"] for n in first["items"]] == ["n4", "n3"] and first["unread"] == 5
    last = first["items"][-1]
    more = api.get("/notifications", headers=headers,
                   params={"limit": 10, "before": last["created_at"], "before_id": last["notification_id"]}).json()
    assert [n["text"] for n in more["items"]] == ["n2", "n1", "n0"]
    billing = api.get("/notifications", headers=headers, params={"groups": "billing"}).json()
    assert [n["text"] for n in billing["items"]] == ["n4", "n2", "n0"]
    assert api.get("/notifications", headers=headers, params={"groups": "nope"}).status_code == 422
    assert api.post("/notifications/read", headers=headers, json={"groups": ["ves"]}).json()["marked"] == 2
    assert api.get("/notifications", headers=headers).json()["unread"] == 3
    platform.clock.t += 91 * 86400
    platform.notifications.notify([user_id], "job_finished", "new")
    assert platform.notifications.purge(90) == 5
    assert [n["text"] for n in api.get("/notifications", headers=headers).json()["items"]] == ["new"]


def test_buckets_pages_and_the_storage_question(api, platform):
    headers = _login(api, platform, "alice")
    bucket = api.post("/buckets", headers=headers, json={"name": "reports"}).json()
    assert bucket["name"] == "alice/reports"
    put = api.put("/buckets/alice/reports/objects/2026/q1.csv", headers=headers, content=b"a,b\n1,2\n")
    assert put.status_code == 201 and put.json()["version"] == 1
    got = api.get("/buckets/alice/reports/objects/2026/q1.csv", headers=headers)
    assert got.content == b"a,b\n1,2\n" and got.headers["x-object-version"] == "1"
    assert got.headers["content-disposition"].startswith("attachment")
    shown = api.get("/buckets/alice/reports/objects/2026/q1.csv?inline=true", headers=headers)
    assert shown.headers["content-type"].startswith("text/csv") and shown.headers["content-disposition"].startswith("inline")
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


def test_statistics_routes_show_money_only_to_owner_and_admin(api, platform):
    owner = _login(api, platform, "olga")
    ve_id = api.post("/ves", headers=owner, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 4,
                                                  "quota_memory_mb": 8192}).json()["ve"]["ve_id"]
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog,
                       now_fn=platform.clock)
    now = platform.clock()
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": "j1", "attempt": 1, "trace_id": "t", "outcome": "completed",
        "owner_type": "user", "owner_id": "u", "started_by_type": "user", "started_by_id": "u",
        "function_id": "f", "function_version": 1, "endpoint_cores": 1.0, "endpoint_ram_mb": 1024,
        "received_at": now - 3, "finished_at": now - 1, "execution_ms": 2000.0,
        "layer_waits_ms": {"control_plane": 4.0}})
    window = {"start": now - 3600, "end": now}
    top = api.get(f"/ves/{ve_id}/stats/top", headers=owner, params={"by": "function", **window}).json()
    assert top["rows"][0]["attempts"] == 1 and top["rows"][0]["cost_ucents"] > 0
    bar = api.get(f"/ves/{ve_id}/stats/jobs/j1", headers=owner).json()
    assert [p["part"] for p in bar["attempts"][0]["parts"]] == ["control_plane", "execution"]
    # A Viewer sees the figures but no money; someone else gets nothing.
    viewer = _login(api, platform, "vic")
    inv = api.post(f"/ves/{ve_id}/invitations", headers=owner, json={"who": "vic", "role": "viewer"}).json()
    assert api.post(f"/invitations/{inv['id']}/accept", headers=viewer).status_code in (200, 204)
    seen = api.get(f"/ves/{ve_id}/stats/top", headers=viewer, params={"by": "function", **window}).json()
    assert seen["rows"][0]["attempts"] == 1 and "cost_ucents" not in seen["rows"][0]
    assert "cost_ucents" not in api.get(f"/ves/{ve_id}/stats/jobs/j1", headers=viewer).json()["attempts"][0]
    assert api.get(f"/ves/{ve_id}/stats/top", headers=viewer,
                   params={"by": "function", "sort": "cost", **window}).status_code == 422
    stranger = _login(api, platform, "sam")
    assert api.get(f"/ves/{ve_id}/stats/heatmap", headers=stranger, params=window).status_code == 404
    chart = api.get(f"/ves/{ve_id}/stats/chart", headers=viewer,
                    params={"metric": "job.total_ms", "statistics": "count,p95", **window}).json()
    assert chart["points"] == [] and chart["bin_width_s"] == 60


def test_billing_routes_follow_the_access_table(api, platform):
    owner = _login(api, platform, "olga")
    ve_id = api.post("/ves", headers=owner, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 4,
                                                  "quota_memory_mb": 8192}).json()["ve"]["ve_id"]
    costs = api.get(f"/ves/{ve_id}/costs", headers=owner).json()
    assert costs["current"]["fees_cents"] == 500 and costs["bills"] == []
    mine = api.get("/me/billing", headers=owner).json()
    assert [v["ve_id"] for v in mine["ves"]] == [ve_id]
    dev = _login(api, platform, "dina")
    inv = api.post(f"/ves/{ve_id}/invitations", headers=owner, json={"who": "dina", "role": "developer"}).json()
    api.post(f"/invitations/{inv['id']}/accept", headers=dev)
    assert api.get(f"/ves/{ve_id}/costs", headers=dev).status_code == 403
    assert api.get("/me/billing", headers=dev).json()["ves"] == []
    assert api.get("/admin/revenue", headers=dev).status_code == 403


def test_the_error_catalog_is_public_and_errors_carry_name_and_number(api, platform):
    catalog = api.get("/errors").json()
    by_name = {e["name"]: e for e in catalog["errors"]}
    assert by_name["REQUEST_LIMIT"]["code"] == 9512 and by_name["REQUEST_LIMIT"]["hint"]
    assert by_name["FUNCTION_EXEC_FAILED"]["component"] == "endpoint"
    missing = api.get("/ves/nope/costs", headers=_login(api, platform, "zed")).json()
    assert (missing["error"], missing["error_code"]) == ("NOT_FOUND", 9502)
    bad = api.post("/auth/login", json={"username": "zed"}).json()           # no password
    assert (bad["error"], bad["error_code"]) == ("REQUEST_INVALID", 9508)
    assert bad["details"]["fields"][0]["field"] == "password"


def test_my_stats_merge_every_ve_with_a_role_and_hide_money_unless_admin_of_all(api, platform):
    olga, dina = _login(api, platform, "olga"), _login(api, platform, "dina")
    body = {"plan_id": "basic", "quota_cpu_cores": 4, "quota_memory_mb": 8192}
    mine = api.post("/ves", headers=dina, json={"name": "own", **body}).json()["ve"]["ve_id"]
    shared = api.post("/ves", headers=olga, json={"name": "ml", **body}).json()["ve"]["ve_id"]
    inv = api.post(f"/ves/{shared}/invitations", headers=olga, json={"who": "dina", "role": "developer"}).json()
    api.post(f"/invitations/{inv['id']}/accept", headers=dina)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog,
                       now_fn=platform.clock)
    now = platform.clock()
    for ve_id, job in ((mine, "a"), (shared, "b"), (shared, "c")):
        intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
            "virtual_environment_id": ve_id, "job_id": job, "attempt": 1, "trace_id": job, "outcome": "completed",
            "owner_type": "user", "owner_id": "u", "started_by_type": "user", "started_by_id": "u",
            "function_id": "f", "function_version": 1, "endpoint_cores": 1.0, "endpoint_ram_mb": 1024,
            "received_at": now - 3, "finished_at": now - 1, "execution_ms": 1000.0})
    window = {"start": now - 3600, "end": now}
    every = api.get("/me/stats/top", headers=dina, params={"by": "function", **window}).json()
    assert every["rows"][0]["attempts"] == 3 and every["money"] is False and "cost_ucents" not in every["rows"][0]
    own = api.get("/me/stats/top", headers=dina, params={"by": "function", "ve_ids": mine, **window}).json()
    assert own["rows"][0]["attempts"] == 1 and own["money"] is True and own["rows"][0]["cost_ucents"] > 0
    heat = api.get("/me/stats/heatmap", headers=dina, params=window).json()
    assert sum(map(sum, heat["attempts"])) == 3 and sorted(heat["ve_ids"]) == sorted([mine, shared])
    # A VE the person has no role in is "not found", never merged.
    sam = _login(api, platform, "sam")
    assert api.get("/me/stats/top", headers=sam, params={"by": "function", "ve_ids": shared, **window}
                   ).status_code == 404
    assert api.get("/me/stats/top", headers=sam, params={"by": "function", **window}).json()["rows"] == []
    cards = {c["ve_id"]: c for c in api.get("/me/overview", headers=dina).json()}
    assert cards[mine]["current_total_cents"] == 500 and cards[shared]["current_total_cents"] is None
    assert cards[shared]["my_role"] == "developer"


def test_plans_list_the_endpoint_sizes_for_the_buy_form(api):
    body = api.get("/plans").json()
    assert {p["plan_id"] for p in body["plans"]} >= {"free", "basic"}
    assert body["endpoint_sizes"]["small"] == {"cpu_cores": 1.0, "memory_mb": 1024}


def test_settings_follow_the_platform_defaults_until_a_person_chooses(api, platform):
    ana = _login(api, platform, "ana")
    mine = api.get("/me/settings", headers=ana).json()
    assert mine["settings"]["time_zone"] == "America/Mexico_City" and mine["settings"]["date_format"] == "MM/DD/YYYY"
    assert mine["own"] == {} and {f["name"] for f in mine["fields"]} >= {"theme", "font_size", "clock"}

    changed = api.patch("/me/settings", headers=ana, json={"time_zone": "Europe/Madrid", "clock": "24h"}).json()
    assert changed["own"] == {"time_zone": "Europe/Madrid", "clock": "24h"}
    bad = api.patch("/me/settings", headers=ana, json={"time_zone": "Mars/Olympus"})
    assert bad.status_code == 422 and bad.json()["error"] == "SETTINGS_INVALID"
    assert api.patch("/me/settings", headers=ana, json={"colour": "red"}).json()["error"] == "SETTINGS_INVALID"
    assert api.patch("/me/settings", headers=ana, json={"theme": "pink"}).status_code == 422

    session = api.post("/auth/login", json={"username": "admin", "password": "admin-password"}).json()
    admin = {"Authorization": f"Bearer {session['access_token']}", "Temporal-Secret-Key": session["temporal_secret"]}
    api.post("/auth/password", headers=admin, json={"new_password": "a-better-one"})
    assert api.patch("/admin/settings/defaults", headers=ana, json={"theme": "dark"}).status_code == 403
    api.patch("/admin/settings/defaults", headers=admin, json={"theme": "dark", "clock": "12h"})
    now = api.get("/me/settings", headers=ana).json()["settings"]
    assert now["theme"] == "dark" and now["clock"] == "24h"             # a default reaches who didn't choose
    back = api.patch("/me/settings", headers=ana, json={"clock": None}).json()
    assert back["own"] == {"time_zone": "Europe/Madrid"} and back["settings"]["clock"] == "12h"
    assert "America/Mexico_City" in api.get("/settings/time-zones").json()["time_zones"]


# ── the bucket pages ─────────────────────────────────────────────────────────
def _upload(api, headers, bucket, name, data):
    started = api.post(f"/buckets/{bucket}/uploads", headers=headers, json={"name": name, "size": len(data)})
    assert started.status_code == 201, started.json()
    body = started.json()
    piece = body["piece_bytes"]
    for i in range(body["pieces"]):
        put = api.put(f"/buckets/{bucket}/uploads/{body['version']}/pieces/{i}", headers=headers,
                      params={"file": name}, content=data[i * piece:(i + 1) * piece])
        assert put.status_code == 200, put.json()
    return body


def test_files_upload_in_pieces_and_list_one_row_per_file(api, platform):
    alice = _login(api, platform, "alice")
    assert api.post("/buckets", headers=alice, json={"name": "photos", "quota_bytes": 100}).status_code == 201
    _upload(api, alice, "alice/photos", "2024/a.txt", b"0123456789")
    again = _upload(api, alice, "alice/photos", "2024/a.txt", b"abcdefghij")
    assert again["version"] == 2
    _upload(api, alice, "alice/photos", "top.txt", b"x")
    root = api.get("/buckets/alice/photos/objects", headers=alice).json()
    assert [(e["kind"], e["name"]) for e in root["entries"]] == [("folder", "2024/"), ("file", "top.txt")]
    inside = api.get("/buckets/alice/photos/objects", headers=alice, params={"prefix": "2024/"}).json()["entries"]
    a = inside[0]
    assert (a["version"], a["versions"], a["size"], a["state"]) == (2, 2, 10, "complete")
    assert a["by"]["kind"] == "person" and a["by"]["user_name"] == "alice"
    versions = api.get("/buckets/alice/photos/versions/2024/a.txt", headers=alice).json()
    assert [v["version"] for v in versions] == [2, 1]
    listed = api.get("/buckets", headers=alice).json()
    assert [(b["name"], b["files"], b["used_bytes"], b["relation"]) for b in listed] == [("alice/photos", 2, 21, "mine")]
    # No room: refused before anything is sent; too big for the page.
    full = api.post("/buckets/alice/photos/uploads", headers=alice, json={"name": "b.bin", "size": 90})
    assert full.status_code == 409
    huge = api.post("/buckets/alice/photos/uploads", headers=alice, json={"name": "b.bin", "size": 6 * 1024 ** 3})
    assert huge.json()["error"] == "UPLOAD_TOO_BIG"
    bad = api.post("/buckets/alice/photos/uploads", headers=alice, json={"name": "../x", "size": 1})
    assert bad.status_code == 422
    # Deleting from the row = every version.
    assert api.delete("/buckets/alice/photos/objects/2024/a.txt", headers=alice).json()["versions"] == [1, 2]
    # A Reader may look but not write.
    bob = _login(api, platform, "bob")
    assert api.get("/buckets/alice/photos/objects", headers=bob).status_code == 404


def test_platform_buckets_are_never_listed(api, platform):
    alice = _login(api, platform, "alice")
    api.post("/buckets", headers=alice, json={"name": "mine"})
    platform.db["buckets"].update_one({"full_name": "alice/mine"}, {"$set": {"owner.name": "axo"}})
    assert api.get("/buckets", headers=alice).json() == []


def test_downloads_by_link_one_file_or_a_zip(api, platform):
    import io
    import zipfile
    alice = _login(api, platform, "alice")
    api.post("/buckets", headers=alice, json={"name": "data"})
    _upload(api, alice, "alice/data", "2024/march/a.csv", b"a,b\n1,2\n")
    _upload(api, alice, "alice/data", "2024/b.csv", b"c\n3\n")
    one = api.post("/buckets/alice/data/downloads", headers=alice, json={"files": [{"name": "2024/b.csv"}]}).json()
    assert (one["zip"], one["filename"], one["size"]) == (False, "b.csv", 4)
    got = api.get(one["url"])                       # no sign-in: the link is the permission
    assert got.content == b"c\n3\n" and "b.csv" in got.headers["content-disposition"]
    both = api.post("/buckets/alice/data/downloads", headers=alice,
                    json={"folders": ["2024/march/"], "files": [{"name": "2024/b.csv"}], "base": "2024/"}).json()
    assert both["zip"] and both["files"] == 2 and both["filename"] == "2024.zip"
    archive = zipfile.ZipFile(io.BytesIO(api.get(both["url"]).content))
    assert sorted(archive.namelist()) == ["b.csv", "march/a.csv"]
    assert archive.read("march/a.csv") == b"a,b\n1,2\n"
    platform.clock.advance(601)
    assert api.get(both["url"]).status_code == 404
    bob = _login(api, platform, "bob")
    assert api.post("/buckets/alice/data/downloads", headers=bob, json={"files": [{"name": "2024/b.csv"}]}).status_code == 404


def test_a_job_write_says_which_job_and_function(api, platform):
    alice = _login(api, platform, "alice")
    bucket = api.post("/buckets", headers=alice, json={"name": "out"}).json()
    platform.storage.upload("u-alice", bucket["bucket_id"], "r.json", b"{}")
    platform.storage.files[(bucket["bucket_id"], "r.json")][1]["source"] = "job:ve-1:j-9"
    platform.db["job_attempts"].insert_one({"_id": "ve-1:j-9:1", "job_id": "j-9", "attempt": 1, "ve_id": "ve-1",
                                            "function_id": "fn-resize", "function_version": 3})
    by = api.get("/buckets/alice/out/objects", headers=alice).json()["entries"][0]["by"]
    assert (by["kind"], by["ve_id"], by["job_id"], by["function_id"], by["function_version"]) == \
        ("job", "ve-1", "j-9", "fn-resize", 3)


def test_delete_restore_gives_access_back_and_roles_change_in_place(api, platform):
    alice = _login(api, platform, "alice")
    bob = _login(api, platform, "bob")
    api.post("/buckets", headers=alice, json={"name": "shared"})
    invite = api.post("/buckets/alice/shared/invitations", headers=alice, json={"who": "bob", "role": "reader"}).json()
    assert api.post(f"/invitations/{invite['id']}/accept", headers=bob).status_code == 200
    # Everyone with access sees the list, with names; only the Owner the invitations.
    seen = api.get("/buckets/alice/shared/access", headers=bob).json()
    assert {(a["name"], a["role"]) for a in seen["access"]} == {("alice", "owner"), ("bob", "reader")}
    bob_id = platform.people.find("bob")["user_id"]
    assert api.put(f"/buckets/alice/shared/access/{bob_id}", headers=bob, json={"role": "writer"}).status_code == 403
    assert api.put(f"/buckets/alice/shared/access/{bob_id}", headers=alice, json={"role": "writer"}).status_code == 204
    assert api.get("/buckets", headers=bob).json()[0]["my_role"] == "writer"
    # Deleted: gone for everyone, restorable by its owner only.
    assert api.delete("/buckets/alice/shared", headers=alice).status_code == 204
    assert api.get("/buckets", headers=bob).json() == []
    deleted = api.get("/buckets", headers=alice, params={"deleted": True}).json()
    assert [d["name"] for d in deleted] == ["alice/shared"] and deleted[0]["purge_after"] > deleted[0]["deleted_at"]
    assert api.get("/buckets", headers=bob, params={"deleted": True}).json() == []
    bucket_id = deleted[0]["bucket_id"]
    assert api.post(f"/deleted-buckets/{bucket_id}/restore", headers=bob).status_code == 404
    assert api.post(f"/deleted-buckets/{bucket_id}/restore", headers=alice).status_code == 200
    assert api.get("/buckets", headers=bob).json()[0]["my_role"] == "writer"
    # Delete permanently: the name is free again at once.
    api.delete("/buckets/alice/shared", headers=alice)
    assert api.delete(f"/deleted-buckets/{bucket_id}", headers=alice).status_code == 204
    assert bucket_id in platform.storage.purged
    assert api.post("/buckets", headers=alice, json={"name": "shared"}).status_code == 201


def test_a_ve_shows_what_its_results_and_objects_keep(api, platform):
    owner = _login(api, platform, "olga")
    ve_id = api.post("/ves", headers=owner, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 4,
                                                  "quota_memory_mb": 8192}).json()["ve"]["ve_id"]
    platform.clock.advance(7200)
    hour = platform.clock() - 3600
    platform.db["storage_size_hours"].insert_one({"_id": "s:1", "ts": hour, "bucket_id": f"axo/ve-{ve_id}-results",
                                                  "bytes": 5000, "cost": {"total_ucents": 7}})
    platform.db["object_state_hours"].insert_one({"_id": f"{ve_id}:1", "ve_id": ve_id, "ts": hour, "bytes": 300,
                                                  "cost": {"total_ucents": 2}})
    stored = api.get(f"/ves/{ve_id}/stored", headers=owner).json()
    assert stored["results"] == {"bytes": 5000, "at": hour, "period_ucents": 7}
    assert stored["object_state"]["bytes"] == 300 and stored["object_state"]["period_ucents"] == 2
    # The three buckets the platform looks after, with storage's figures now.
    assert [(b["kind"], b["bucket"]) for b in stored["platform_buckets"]] == [
        ("functions", f"axo/ve-{ve_id}-functions"), ("results", f"axo/ve-{ve_id}-results"),
        ("objects", f"axo/ve-{ve_id}-objects")]
    assert all(b["bytes"] == 0 and b["files"] == 0 for b in stored["platform_buckets"])
    assert api.get(f"/ves/{ve_id}/stored", headers=_login(api, platform, "nobody")).status_code == 404


def test_the_ve_buckets_tab_lists_each_ves_platform_buckets(api, platform):
    owner = _login(api, platform, "olga")
    ve_id = api.post("/ves", headers=owner, json={"name": "ml", "plan_id": "basic", "quota_cpu_cores": 4,
                                                  "quota_memory_mb": 8192}).json()["ve"]["ve_id"]
    rows = api.get("/me/ve-buckets", headers=owner).json()
    assert [(r["kind"], r["bucket"], r["ve_name"]) for r in rows] == [
        ("functions", f"axo/ve-{ve_id}-functions", "ml"), ("results", f"axo/ve-{ve_id}-results", "ml"),
        ("objects", f"axo/ve-{ve_id}-objects", "ml")]
    assert all(r["read_only"] and r["files"] == 0 and not r["own_results_only"] for r in rows)
    vic_headers = _login(api, platform, "vic")

    def actor_of(name):
        session = platform.people.login(name, "password123")
        return platform.people.actor_for(platform.people.verify(session["access_token"], session["temporal_secret"]))

    invite = platform.sharing.invite_to_ve(actor_of("olga"), ve_id, "vic", "viewer")
    platform.sharing.answer(actor_of("vic"), invite["id"], accept=True)
    seen = api.get("/me/ve-buckets", headers=vic_headers).json()
    results = next(r for r in seen if r["kind"] == "results")
    assert results["files"] is None and results["bytes"] is None and results["own_results_only"] is True
