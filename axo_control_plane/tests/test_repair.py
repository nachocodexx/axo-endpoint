"""Repair: every piece of a running VE checked and fixed, step by step."""
import pytest

from axo_control_plane.application.repair import VeRepair
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, LimitReachedError

from .test_ves import _heartbeat


def _running_ve(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    platform.provisioner.tick()
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    return alice, ve_id


@pytest.fixture
def repair(platform):
    r = VeRepair(platform.db, platform.driver, platform.vem, platform.provisioner, platform.notifications,
                 platform.history, "axo-storage", vem_wait_s=4, poll_s=1, now_fn=platform.clock,
                 sleep_fn=platform.clock.advance)
    platform.ves.repairs = r
    platform.provisioner.after_running = r.check_unavailable
    return r


def _by_check(answer):
    return {s["check"]: s for s in answer["steps"]}


def test_a_healthy_ve_is_checked_and_nothing_is_changed(platform, repair):
    alice, ve_id = _running_ve(platform)
    answer = platform.ves.repair(alice, ve_id)
    assert answer["outcome"] == "ok"
    assert {s["status"] for s in answer["steps"]} == {"ok"}
    assert [c for c in platform.vem.calls if c[0] == "repair"] == [("repair", ve_id)]
    assert any("everything was working" in n["text"] for n in platform.notifications.list(alice.user_id))
    assert platform.history.of_type("VirtualEnvironmentRepaired")[0]["outcome"] == "ok"


def test_stopped_containers_and_a_silent_vem_are_fixed(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.driver.stop(f"ve-{ve_id}-mongo")
    platform.driver.remove(f"ve-{ve_id}-redis")
    platform.driver.networks[f"ve-{ve_id}-net"].discard("axo-storage")
    platform.vem.unreachable.add(ve_id)
    stops = []
    original_stop = platform.driver.stop

    def stop_and_heal(name, timeout_s=10):        # the restart brings the VEM back
        stops.append(name)
        original_stop(name, timeout_s)
        platform.vem.unreachable.discard(ve_id)
    platform.driver.stop = stop_and_heal
    platform.vem.endpoints[ve_id] = {"ep-1": "failed", "ep-2": "running"}

    steps = _by_check(platform.ves.repair(alice, ve_id))

    assert steps["network"]["status"] == "fixed"
    assert steps["mongo"]["status"] == "fixed" and "started" in steps["mongo"]["text"]
    assert steps["redis"]["status"] == "fixed" and "created" in steps["redis"]["text"]
    assert steps["vem_answers"]["status"] == "fixed" and "restarted" in steps["vem_answers"]["text"]
    assert steps["endpoints"]["status"] == "fixed" and "ep-1" in steps["endpoints"]["text"]
    assert stops == [f"ve-{ve_id}-vem"]
    assert platform.vem.repairs[-1]["steps"][0]["check"] == "network"     # the VE's history gets the checks


def test_a_vem_that_never_answers_is_recreated_and_its_report_waits(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.vem.unreachable.add(ve_id)
    steps = _by_check(platform.ves.repair(alice, ve_id))
    assert steps["vem_answers"]["status"] == "later" and "recreated" in steps["vem_answers"]["text"]
    assert steps["endpoints"]["status"] == "later"
    assert f"ve-{ve_id}-vem" in platform.driver.removed

    platform.vem.unreachable.discard(ve_id)                  # it answers later: the report goes then
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    assert platform.vem.repairs and platform.vem.repairs[-1]["ve_id"] == ve_id
    assert "repair_unreported" not in platform.db["virtual_environments"].find_one({"_id": ve_id})


def test_once_every_five_minutes_and_only_for_admins(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.ves.repair(alice, ve_id)
    platform.clock.advance(120)
    with pytest.raises(LimitReachedError) as too_soon:
        platform.ves.repair(alice, ve_id)
    assert too_soon.value.code == "REPAIR_TOO_SOON" and too_soon.value.details["available_in_s"] == 180
    platform.clock.advance(181)
    assert platform.ves.repair(alice, ve_id)["outcome"] == "ok"

    bob = platform.user("bob")
    with pytest.raises((ForbiddenError, Exception)):
        platform.ves.repair(bob, ve_id)


def test_a_stopped_ve_has_nothing_to_repair(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.ves.stop(alice, ve_id)
    with pytest.raises(ConflictError) as refused:
        platform.ves.repair(alice, ve_id)
    assert refused.value.code == "REPAIR_NOT_RUNNING"


def test_the_platform_repairs_a_ve_unavailable_for_ten_minutes_once_an_hour(platform, repair):
    alice, ve_id = _running_ve(platform)
    _heartbeat(platform, ve_id, health="Unavailable")
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"health": "Unavailable"}})
    platform.provisioner.tick()                              # first seen Unavailable now
    for _ in range(4):                                       # 9 minutes: not yet
        platform.clock.advance(135)
        _heartbeat(platform, ve_id, health="Unavailable")
        platform.provisioner.tick()
    assert platform.history.of_type("VirtualEnvironmentRepaired") == []
    platform.clock.advance(70)
    _heartbeat(platform, ve_id, health="Unavailable")
    platform.provisioner.tick()
    repaired = platform.history.of_type("VirtualEnvironmentRepaired")
    assert len(repaired) == 1 and repaired[0]["automatic"] is True
    assert any("The platform" in n["text"] for n in platform.notifications.list(alice.user_id))

    platform.clock.advance(1800)                             # still unavailable: not again within the hour
    _heartbeat(platform, ve_id, health="Unavailable")
    platform.provisioner.tick()
    assert len(platform.history.of_type("VirtualEnvironmentRepaired")) == 1


def test_a_vem_on_an_older_build_is_updated_and_its_data_kept(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.driver.image_ids["axo-vem:test"] = "sha-new"            # deploy.sh build made a new VEM image
    steps = _by_check(platform.ves.repair(alice, ve_id))
    assert steps["vem"]["status"] == "fixed" and "older version" in steps["vem"]["text"]
    assert f"ve-{ve_id}-vem" in platform.driver.removed
    vem = platform.driver.containers[f"ve-{ve_id}-vem"]
    assert vem["image_id"] == "sha-new"
    assert f"ve-{ve_id}-vem-outbox" in vem["spec"].volumes                # same outbox volume: nothing lost
    assert steps["kurrent"]["status"] == "ok"                          # the others were current


def test_a_vem_without_repair_says_to_rebuild(platform, repair):
    from axo_control_plane.domain.errors import NotFoundError

    alice, ve_id = _running_ve(platform)

    def old_vem(*_a, **_k):
        raise NotFoundError("Not Found")
    platform.vem.repair = old_vem
    steps = _by_check(platform.ves.repair(alice, ve_id))
    assert steps["endpoints"]["status"] == "failed" and "deploy.sh build" in steps["endpoints"]["text"]


def test_the_endpoints_step_counts_them(platform, repair):
    alice, ve_id = _running_ve(platform)
    platform.vem.endpoints[ve_id] = {"ep-1": "running", "ep-2": "sleeping"}
    assert _by_check(platform.ves.repair(alice, ve_id))["endpoints"]["text"] == "1 of 2 running, 1 sleeping"
