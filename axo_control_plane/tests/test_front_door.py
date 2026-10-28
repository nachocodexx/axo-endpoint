import re
from pathlib import Path

import pytest

from axo_control_plane.application.front_door import CONTROL_PLANE_ONLY, WS_RULES, FrontDoor, match
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, LimitReachedError, NotFoundError


def _door(platform):
    sleep = SleepManager(platform.db, platform.catalog, platform.vem, platform.leases, platform.notifications,
                         platform.history, instance_id="cp-1", now_fn=platform.clock)
    return FrontDoor(platform.db, platform.authz, sleep, platform.catalog, platform.notifications,
                     now_fn=platform.clock), sleep


def _running(platform, actor, plan_id="basic"):
    ve_id = platform.buy(actor, plan_id=plan_id, name=f"v{plan_id}")["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "lifecycle": V.RUNNING, "heartbeat_at": platform.clock(), "health": "Healthy"}})
    return ve_id


def test_the_permission_table():
    assert match("GET", "/functions") == (R.VIEWER, None)
    assert match("POST", "/functions/f1/3/jobs") == (R.DEVELOPER, "job")
    assert match("POST", "/choreographies/c1/runs") == (R.DEVELOPER, "choreography_run")
    assert match("PATCH", "/ve/settings") == (R.ADMIN, None)
    for method, path in (("POST", "/ve/stop"), ("POST", "/ve/prepare-delete"), ("POST", "/ve/limits"),
                         ("DELETE", "/functions"), ("GET", "/../admin")):
        assert match(method, path) is None           # not in the table = refused


VEM_CONTROLLERS = Path(__file__).resolve().parents[2] / "axo_vem" / "axo_vem" / "infrastructure" / "transport" / "api" \
    / "controllers"
_ROUTE = re.compile(r'@router\.(get|post|put|patch|delete|websocket)\("([^"]+)"')


def _vem_routes():
    for source in sorted(VEM_CONTROLLERS.glob("*.py")):
        for method, path in _ROUTE.findall(source.read_text()):
            yield method.upper(), re.sub(r"{[^}]+}", "x1", path)


def test_every_vem_route_has_a_rule():
    """A new VEM route needs a line in the table (or CONTROL_PLANE_ONLY)."""
    routes = list(_vem_routes())
    assert len(routes) > 60
    cp_only = {(m, p) for m, p in CONTROL_PLANE_ONLY}
    missing = [(m, p) for m, p in routes
               if (m, p) not in cp_only and not (any(r.match(p) for r in WS_RULES) if m == "WEBSOCKET"
                                                 else match(m, p))]
    assert missing == []
    assert not [(m, p) for m, p in cp_only if match(m, p)]


def test_object_and_choreography_roles():
    assert match("GET", "/choreographies/designer") == (R.VIEWER, None)
    assert match("POST", "/objects") == (R.DEVELOPER, "job")
    assert match("POST", "/objects/acme/calls") == (R.DEVELOPER, "job")
    assert match("POST", "/choreographies/c/runs/r/resume") == (R.DEVELOPER, None)
    assert match("DELETE", "/objects/acme") == (R.ADMIN, None)
    assert match("DELETE", "/choreographies/c") == (R.ADMIN, None)
    assert match("POST", "/bucket-events") is None


def test_roles_are_checked_before_forwarding(platform):
    door, _ = _door(platform)
    owner, viewer = platform.user("olga"), platform.user("vic")
    ve_id = _running(platform, owner)
    inv = platform.sharing.invite_to_ve(owner, ve_id, "vic", R.VIEWER)
    platform.sharing.answer(viewer, inv["id"], accept=True)
    assert door.decide(viewer, ve_id, "GET", "/functions").action == "forward"
    with pytest.raises(ForbiddenError):
        door.decide(viewer, ve_id, "POST", "/functions/f/1/jobs", {})
    with pytest.raises(NotFoundError):
        door.decide(viewer, ve_id, "POST", "/ve/stop")
    stranger = platform.user("sam")
    with pytest.raises(NotFoundError):
        door.decide(stranger, ve_id, "GET", "/functions")
    assert door.decide(owner, ve_id, "PATCH", "/ve/settings").action == "forward"


def test_sleeping_ve_keeps_submissions_and_wakes(platform):
    door, sleep = _door(platform)
    alice = platform.user("alice")
    ve_id = _running(platform, alice, plan_id="free")
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "desired": V.DESIRED_SLEEPING, "lifecycle": V.SLEEPING}})
    decision = door.decide(alice, ve_id, "POST", "/functions/f/1/jobs", {"params": {}})
    assert decision.action == "answer" and decision.status_code == 202
    job_id = decision.body["job_id"]
    assert platform.ves.record(ve_id).lifecycle == V.WAKING
    status = door.decide(alice, ve_id, "GET", f"/jobs/{job_id}")
    assert status.action == "answer" and status.body["status"] == "waiting"
    other = door.decide(alice, ve_id, "GET", "/functions")
    assert other.status_code == 503 and other.headers["Retry-After"]


def test_stopped_ve_refuses(platform):
    door, _ = _door(platform)
    alice = platform.user("alice")
    ve_id = _running(platform, alice)
    platform.ves.stop(alice, ve_id)
    with pytest.raises(ConflictError):
        door.decide(alice, ve_id, "GET", "/functions")


def test_free_plan_monthly_limit_with_notices(platform):
    door, _ = _door(platform)
    alice = platform.user("alice")
    ve_id = _running(platform, alice, plan_id="free")
    platform.catalog.save_plan(platform.catalog.get("free").with_changes(request_limit=10), by="admin")
    for _ in range(10):
        assert door.decide(alice, ve_id, "POST", "/choreographies/c/runs", {}).action == "forward"
    with pytest.raises(LimitReachedError):
        door.decide(alice, ve_id, "POST", "/functions/f/1/jobs", {})
    texts = [n["text"] for n in platform.notifications.list(alice.user_id)]
    assert sum("80%" in t for t in texts) == 1 and sum("90%" in t for t in texts) == 1
    assert sum("100%" in t for t in texts) == 1
    assert door.decide(alice, ve_id, "GET", "/functions").action == "forward"   # reading isn't counted


def test_the_callers_teams_go_to_the_vem_with_the_call(platform):
    """The VEM checks "who sees runs and results" shared with teams itself."""
    from axo_control_plane.application.front_door import ACTING_TEAMS_HEADER, acting_headers

    door, _ = _door(platform)
    olga = platform.user("olga")
    ve_id = _running(platform, olga)
    team = platform.teams.create(olga, "blue-team")
    decision = door.decide(olga, ve_id, "GET", "/jobs")
    assert decision.teams == (team["team_id"],)
    assert acting_headers(olga, decision.role, decision.teams)[ACTING_TEAMS_HEADER] == team["team_id"]
    assert match("PUT", "/visibility/function/f1") == (R.DEVELOPER, None)


def test_an_event_sent_to_a_sleeping_ve_is_kept_once_and_wakes_it(platform):
    door, sleep = _door(platform)
    alice = platform.user("alice")
    ve_id = _running(platform, alice, plan_id="free")
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"desired": V.DESIRED_SLEEPING,
                                                                             "lifecycle": V.SLEEPING}})
    body = {"type": "order.paid", "data": {"total": 5}, "event_id": "pay-1"}
    first = door.decide(alice, ve_id, "POST", "/events", body)
    again = door.decide(alice, ve_id, "POST", "/events", body)
    assert first.status_code == 202 and first.body["event_id"] == "pay-1" and again.body["event_id"] == "pay-1"
    saved = list(platform.db["pending_requests"].find({"kind": "event"}))
    assert len(saved) == 1 and saved[0]["body"]["event_id"] == "pay-1"
    assert match("DELETE", "/event-types/order.paid") == (R.ADMIN, None)
