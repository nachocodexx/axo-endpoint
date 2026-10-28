from datetime import datetime, timedelta, timezone

import mongomock

from axo_vem.application.virtual_environment.history_cleanup import HistoryCleanup
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=37)   # 30 days of retention + 7 of grace


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_history_past_retention_plus_grace_is_deleted_and_the_rest_kept():
    db = mongomock.MongoClient()["test"]
    activity = MongoActivityRepository(db["unified_activity"])
    jobs = MongoJobRepository(db["jobs"])
    runs = MongoChoreographyRunRepository(db["choreography_runs"])
    for name, days_ago in (("old", 40), ("hidden-not-yet-deleted", 33), ("new", 1)):
        activity.record("JobCompleted", {"event_id": name, "created_at": _iso(days_ago)})
    db["jobs"].insert_many([
        {"_id": "old-done", "status": "COMPLETED", "created_at": _iso(40)},
        {"_id": "old-still-queued", "status": "QUEUED", "created_at": _iso(40)},
        {"_id": "new-done", "status": "FAILED", "created_at": _iso(1)},
    ])
    db["choreography_runs"].insert_many([
        {"_id": "old", "finished_at": _iso(40)},
        {"_id": "running", "finished_at": None},
        {"_id": "new", "finished_at": _iso(1)},
    ])

    deleted = HistoryCleanup(activity, jobs, runs, lambda: CUTOFF).run()

    assert deleted == {"activity": 1, "jobs": 1, "runs": 1, "events": 0}
    assert {d["_id"] for d in db["unified_activity"].find()} == {"hidden-not-yet-deleted", "new"}
    assert {d["_id"] for d in db["jobs"].find()} == {"old-still-queued", "new-done"}
    assert {d["_id"] for d in db["choreography_runs"].find()} == {"running", "new"}


def test_without_retention_nothing_is_deleted():
    db = mongomock.MongoClient()["test"]
    activity = MongoActivityRepository(db["unified_activity"])
    activity.record("JobCompleted", {"event_id": "ancient", "created_at": _iso(4000)})
    cleanup = HistoryCleanup(activity, MongoJobRepository(db["jobs"]),
                             MongoChoreographyRunRepository(db["choreography_runs"]), lambda: None)
    assert cleanup.run() == {"activity": 0, "jobs": 0, "runs": 0, "events": 0}
    assert db["unified_activity"].count_documents({}) == 1


def test_rows_saved_before_areas_existed_get_theirs_and_old_readings_go():
    collection = mongomock.MongoClient()["test"]["unified_activity"]
    collection.insert_many([
        {"_id": "a", "event_type": "JobQueued", "created_at": NOW},
        {"_id": "b", "event_type": "LeaderElected", "created_at": NOW},
        {"_id": "c", "event_type": "VirtualEnvironmentSettingsChanged", "created_at": NOW},
        {"_id": "d", "event_type": "EndpointMetricsReported", "created_at": NOW},
    ])
    MongoActivityRepository(collection)
    assert {d["_id"]: d["area"] for d in collection.find()} == {"a": "job", "b": "endpoint", "c": "ve"}
