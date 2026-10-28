from axo_control_plane.application.sleeping import DELIVERED, FAILED, KIND_JOB, SleepManager
from axo_control_plane.domain import virtual_environment as V


def _manager(platform, **kw):
    return SleepManager(platform.db, platform.catalog, platform.vem, platform.leases, platform.notifications,
                        platform.history, instance_id="cp-1", now_fn=platform.clock, **kw)


def _free_running_ve(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice, name="play", plan_id="free")["ve"]["ve_id"]
    platform.provisioner.tick()
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "heartbeat": {"lifecycle": "Running", "health": "Healthy", "outbox_pending": 0},
        "heartbeat_at": platform.clock()}})
    platform.provisioner.tick()
    assert platform.ves.record(ve_id).lifecycle == V.RUNNING
    return alice, ve_id


def _beat(platform, ve_id, lifecycle="Running"):
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "heartbeat": {"lifecycle": lifecycle, "health": "Healthy", "outbox_pending": 0},
        "heartbeat_at": platform.clock()}})


def test_free_ve_sleeps_after_30_idle_minutes_and_paid_ones_never(platform):
    manager = _manager(platform)
    alice, ve_id = _free_running_ve(platform)
    paid = platform.buy(alice, name="paid")["ve"]["ve_id"]
    platform.db["virtual_environments"].update_many({}, {"$set": {"idle_since": platform.clock(),
                                                                   "lifecycle": V.RUNNING}})
    platform.clock.advance(29 * 60)
    assert manager.put_idle_to_sleep() == []
    platform.clock.advance(2 * 60)
    assert manager.put_idle_to_sleep() == [ve_id]
    platform.provisioner.tick()          # VEM asked to stop
    platform.provisioner.tick()          # containers stopped, kept
    assert platform.ves.record(ve_id).lifecycle == V.SLEEPING
    assert platform.driver.state(f"ve-{ve_id}-vem") == "exited"
    assert platform.ves.record(paid).desired == V.DESIRED_RUNNING


def test_a_job_for_a_sleeping_ve_is_kept_answered_and_delivered_after_the_wake(platform):
    manager = _manager(platform)
    alice, ve_id = _free_running_ve(platform)
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"idle_since": platform.clock() - 4000}})
    manager.put_idle_to_sleep()
    platform.provisioner.tick(); platform.provisioner.tick()
    answer = manager.accept(ve_id, KIND_JOB, "/functions/f/1/jobs", {"params": {"x": 1}}, alice.user_id, max_wait=0)
    assert answer["status"] == "waiting" and answer["job_id"].startswith("job-")
    assert manager.status(answer["job_id"])["status"] == "waiting"
    record = platform.ves.record(ve_id)
    assert record.desired == V.DESIRED_RUNNING and record.lifecycle == V.WAKING
    assert manager.deliver() == 0                         # still waking
    platform.provisioner.tick()                           # containers started
    platform.clock.advance(20)
    _beat(platform, ve_id, lifecycle="Stopped")
    platform.provisioner.tick()                           # VEM asked to start
    _beat(platform, ve_id)
    platform.provisioner.tick()                           # Running again
    assert platform.ves.record(ve_id).lifecycle == V.RUNNING
    assert any("woke up" in n["text"] for n in platform.notifications.list(alice.user_id))
    assert manager.deliver() == 1                         # max_wait=0 still runs after a wake
    (delivered_ve, request), = platform.vem.jobs
    assert delivered_ve == ve_id and request["body"]["job_id"] == answer["job_id"]
    assert request["wait_ms"] == 20_000                  # held here while the VE woke: the CP wait
    doc = platform.db["pending_requests"].find_one({"_id": answer["job_id"]})
    assert doc["status"] == DELIVERED and doc["wake_ms"] == 20_000
    assert manager.status(answer["job_id"]) is None       # now the VEM knows it


def test_waiting_longer_than_max_wait_fails_and_a_failed_wake_fails_all(platform):
    manager = _manager(platform, wake_timeout_s=300)
    alice, ve_id = _free_running_ve(platform)
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "desired": V.DESIRED_SLEEPING, "lifecycle": V.SLEEPING}})
    short = manager.accept(ve_id, KIND_JOB, "/functions/f/1/jobs", {}, alice.user_id, max_wait=10)
    longer = manager.accept(ve_id, KIND_JOB, "/functions/f/1/jobs", {}, alice.user_id)
    platform.clock.advance(301)
    manager.deliver()                                     # never woke
    for answer in (short, longer):
        assert manager.status(answer["job_id"])["status"] == FAILED
    assert any("could not wake" in n["text"] for n in platform.notifications.list(alice.user_id))


def test_a_saved_job_cancelled_before_the_wake_is_dropped_and_never_delivered(platform):
    manager = _manager(platform)
    alice, ve_id = _free_running_ve(platform)
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"idle_since": platform.clock() - 4000}})
    manager.put_idle_to_sleep()
    platform.provisioner.tick(); platform.provisioner.tick()
    job_id = manager.accept(ve_id, KIND_JOB, "/functions/f/1/jobs", {"params": {}}, alice.user_id)["job_id"]
    assert manager.cancel(job_id, alice.user_id) == {"job_id": job_id, "cancelled": True, "status": "cancelled"}
    assert manager.cancel(job_id, alice.user_id) is None          # only once
    platform.provisioner.tick()
    platform.clock.advance(20)
    _beat(platform, ve_id, lifecycle="Stopped")
    platform.provisioner.tick()
    _beat(platform, ve_id)
    platform.provisioner.tick()
    assert manager.deliver() == 0 and platform.vem.jobs == []
