from axo_control_plane.application.cleanup import CLEANUP_LOGS_BUCKET, Cleanup
from axo_control_plane.domain import virtual_environment as V


def _cleanup(platform):
    return Cleanup(platform.db, platform.driver, platform.vem, platform.storage, platform.buckets,
                   platform.leases, platform.history, instance_id="cp-1", now_fn=platform.clock)


def _running(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    platform.provisioner.tick()
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "lifecycle": V.RUNNING, "heartbeat_at": platform.clock(), "heartbeat": {"lifecycle": "Running"}}})
    return alice, ve_id


def test_leftovers_are_removed_only_after_two_checks(platform):
    cleanup = _cleanup(platform)
    _, ve_id = _running(platform)
    d = platform.driver
    d.add_labelled("ep-live", {"axo.ve": ve_id, "axo.role": "endpoint", "axo.endpoint": "ep-1"})
    d.add_labelled("ep-gone", {"axo.ve": ve_id, "axo.role": "endpoint", "axo.endpoint": "ep-2"})
    d.add_labelled("fn-orphan", {"axo.ve": ve_id, "axo.role": "runner", "axo.endpoint": "ep-9"})
    d.add_labelled("fn-ok", {"axo.ve": ve_id, "axo.role": "runner", "axo.endpoint": "ep-1"})
    d.add_labelled("vem-of-nothing", {"axo.ve": "deleted-ve", "axo.role": "vem"})
    d.add_labelled("someone-elses", {"app": "x"})
    platform.vem.endpoints[ve_id] = {"ep-1": "running", "ep-2": "removed"}
    assert cleanup.tick() == []                               # first sighting: suspects only
    platform.clock.advance(60)
    removed = cleanup.tick()
    assert sorted(removed) == ["ep-gone", "fn-orphan", "vem-of-nothing"]
    assert {"ep-live", "fn-ok", "someone-elses", f"ve-{ve_id}-vem"} <= set(d.containers)
    logged = cleanup.recent()
    assert {e["container"] for e in logged} == set(removed)
    assert all(e["log_object"].startswith(CLEANUP_LOGS_BUCKET) for e in logged)
    assert len(platform.storage.objects) == 3


def test_a_vem_that_does_not_answer_means_skip_and_a_recovered_suspect_is_forgotten(platform):
    cleanup = _cleanup(platform)
    _, ve_id = _running(platform)
    platform.driver.add_labelled("ep-new", {"axo.ve": ve_id, "axo.role": "endpoint", "axo.endpoint": "ep-3"})
    platform.vem.endpoints[ve_id] = {}
    cleanup.tick()                                             # unknown to the VEM: suspect
    platform.vem.endpoints[ve_id] = {"ep-3": "creating"}      # ...the VEM recorded it meanwhile
    platform.clock.advance(60)
    assert cleanup.tick() == []
    platform.vem.unreachable.add(ve_id)
    platform.vem.endpoints[ve_id] = {}
    for _ in range(3):
        platform.clock.advance(60)
        assert cleanup.tick() == []


def test_deleted_ve_volumes_go_after_the_grace_period(platform):
    cleanup = _cleanup(platform)
    alice, ve_id = _running(platform)
    ve = platform.ves.record(ve_id)
    platform.ves.delete(alice, ve_id, confirm_name=ve.name)
    for _ in range(3):
        platform.provisioner.tick()
    assert platform.ves.record(ve_id).lifecycle == V.DELETED
    assert cleanup.remove_expired_volumes() == []
    platform.clock.advance(31 * 86400)
    assert cleanup.remove_expired_volumes() == [ve_id]
    assert not [v for v in platform.driver.volumes if ve_id in v]
