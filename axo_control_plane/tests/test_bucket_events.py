"""axo_storage "file written" reports -> the VEs whose choreography triggers watch that bucket."""

from axo_control_plane.application.bucket_events import (
    BUCKET_EVENTS_PATH,
    STORAGE_OBJECT_WRITTEN,
    BucketEvents,
    save_triggers,
)
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.domain import virtual_environment as V
from tests.test_sleeping import _free_running_ve, _manager


def _setup(platform):
    manager = _manager(platform)
    alice, ve_id = _free_running_ve(platform)
    bob = platform.user("bob")
    bucket = platform.buckets.create(alice, "sales")
    platform.buckets.add_alias(alice, "alice/sales", "alice/latest")
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"triggers": [
        {"bucket": "alice/latest", "created_by": alice.user_id},     # an alias of the bucket written
        {"bucket": "alice/sales", "created_by": bob.user_id},        # bob may not read it
        {"bucket": "alice/elsewhere", "created_by": alice.user_id},  # no such bucket
    ]}})
    events = BucketEvents(platform.db, platform.storage_access, manager)
    return manager, events, alice, ve_id, bucket["bucket_id"]


def _written(key, name="in/a.csv", version=2, source=None):
    return {"bucket_key": key, "name": name, "version": version, "format": "raw", "size": 10, "source": source}


def test_a_new_file_reaches_the_ves_watching_it_once_and_only_for_readers(platform):
    manager, events, alice, ve_id, key = _setup(platform)
    assert events.written("storage:s1", _written(key, source="choreography:ch-1"))
    assert events.written("storage:s1", _written(key, source="choreography:ch-1"))     # resent: kept once

    [saved] = platform.db["pending_requests"].find({"kind": "bucket_event"})
    assert saved["ve_id"] == ve_id and saved["path"] == BUCKET_EVENTS_PATH
    assert saved["body"] == {"bucket": "alice/latest", "name": "in/a.csv", "version": 2, "format": "raw",
                             "source": "choreography:ch-1", "allowed_users": [alice.user_id]}
    assert not manager.is_asleep(platform.ves.record(ve_id))   # people's jobs aren't held behind it
    assert manager.deliver() == 1
    (delivered_ve, request), = platform.vem.jobs
    assert delivered_ve == ve_id and request["path"] == BUCKET_EVENTS_PATH
    assert request["acting_user"] == alice.user_id


def test_a_stopped_ve_gets_nothing_and_a_sleeping_one_is_woken(platform):
    manager, events, alice, ve_id, key = _setup(platform)
    ves = platform.db["virtual_environments"]
    ves.update_one({"_id": ve_id}, {"$set": {"desired": V.DESIRED_STOPPED}})
    events.written("storage:s1", _written(key, version=3))
    assert platform.db["pending_requests"].count_documents({}) == 0

    ves.update_one({"_id": ve_id}, {"$set": {"desired": V.DESIRED_SLEEPING, "lifecycle": V.SLEEPING}})
    events.written("storage:s1", _written(key, version=4))
    assert platform.db["pending_requests"].count_documents({"kind": "bucket_event"}) == 1
    record = platform.ves.record(ve_id)
    assert record.desired == V.DESIRED_RUNNING and record.lifecycle == V.WAKING


def test_the_intake_hands_storage_reports_to_the_bucket_events(platform):
    manager, events, alice, ve_id, key = _setup(platform)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog)
    intake.storage_handlers[STORAGE_OBJECT_WRITTEN] = events.written
    assert intake.handle(STORAGE_OBJECT_WRITTEN, "storage:s1", _written(key))
    assert intake.handle(STORAGE_OBJECT_WRITTEN, "vem:other", _written(key, version=9))   # not from storage
    assert platform.db["pending_requests"].count_documents({"kind": "bucket_event"}) == 1


def test_an_older_heartbeat_never_puts_back_an_older_trigger_list(platform):
    """The front door reads the VEM's triggers right after a save; a heartbeat
    built just before that save, arriving after, must not undo it."""
    _manager_, events, alice, ve_id, key = _setup(platform)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog)
    watching = [{"bucket": "alice/sales", "created_by": alice.user_id}]
    beat = {"virtual_environment_id": ve_id, "lifecycle": "Running", "health": "Healthy", "target_endpoints": 1,
            "min_endpoints": 1, "running": 1}

    assert save_triggers(platform.db["virtual_environments"], ve_id, watching, at=1000.0)
    intake.heartbeat(ve_id, {**beat, "triggers": [], "created_at": "1970-01-01T00:16:39+00:00"})   # 999 s
    assert platform.db["virtual_environments"].find_one({"_id": ve_id})["triggers"] == watching
    intake.heartbeat(ve_id, {**beat, "triggers": [], "created_at": "1970-01-01T00:16:41+00:00"})   # 1001 s
    assert platform.db["virtual_environments"].find_one({"_id": ve_id})["triggers"] == []
    assert not save_triggers(platform.db["virtual_environments"], ve_id, watching, at=1000.5)
