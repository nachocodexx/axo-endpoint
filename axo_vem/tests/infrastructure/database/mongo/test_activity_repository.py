from datetime import datetime, timezone

import mongomock

from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository


def _repo():
    collection = mongomock.MongoClient()["test"]["unified_activity"]
    return MongoActivityRepository(collection)


def _insert(
    repo,
    event_id,
    event_type="SomeEvent",
    created_at=None,
    user_id=None,
    virtual_environment_id=None,
    endpoint_id=None,
    meta=None,
):
    repo._collection.insert_one({
        "_id": event_id,
        "event_type": event_type,
        "created_at": created_at or datetime.now(timezone.utc),
        "user_id": user_id,
        "virtual_environment_id": virtual_environment_id,
        "endpoint_id": endpoint_id,
        "runtime_type": None,
        "meta": meta or {},
    })


def test_get_timeline_with_no_filters_returns_everything():
    repo = _repo()
    _insert(repo, "a")
    _insert(repo, "b")

    timeline = repo.get_timeline()
    assert {e["event_id"] for e in timeline} == {"a", "b"}


def test_get_timeline_filters_by_user_id():
    repo = _repo()
    _insert(repo, "a", user_id="user-1")
    _insert(repo, "b", user_id="user-2")

    timeline = repo.get_timeline(user_id="user-1")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_filters_by_virtual_environment_id():
    repo = _repo()
    _insert(repo, "a", virtual_environment_id="ve1")
    _insert(repo, "b", virtual_environment_id="ve2")

    timeline = repo.get_timeline(virtual_environment_id="ve1")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_filters_by_endpoint_id():
    repo = _repo()
    _insert(repo, "a", endpoint_id="n0")
    _insert(repo, "b", endpoint_id="n1")

    timeline = repo.get_timeline(endpoint_id="n0")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_filters_by_function_id_nested_under_meta():
    repo = _repo()
    _insert(repo, "a", meta={"function_id": "add"})
    _insert(repo, "b", meta={"function_id": "sub"})

    timeline = repo.get_timeline(function_id="add")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_filters_by_active_object_id_nested_under_meta():
    repo = _repo()
    _insert(repo, "a", meta={"object_id": "ao1"})
    _insert(repo, "b", meta={"object_id": "ao2"})

    timeline = repo.get_timeline(active_object_id="ao1")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_with_no_matching_active_object_id_returns_empty():
    """Active object events carry object_id (meta.object_id); events of
    other kinds never match."""
    repo = _repo()
    _insert(repo, "a", meta={"function_id": "add"})

    assert repo.get_timeline(active_object_id="anything") == []


def test_get_timeline_combines_multiple_filters_with_and_semantics():
    repo = _repo()
    _insert(repo, "a", endpoint_id="n0", meta={"function_id": "add"})
    _insert(repo, "b", endpoint_id="n0", meta={"function_id": "sub"})
    _insert(repo, "c", endpoint_id="n1", meta={"function_id": "add"})

    timeline = repo.get_timeline(endpoint_id="n0", function_id="add")
    assert [e["event_id"] for e in timeline] == ["a"]


def test_get_timeline_sorts_newest_first():
    repo = _repo()
    _insert(repo, "a", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(repo, "b", created_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
    _insert(repo, "c", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

    timeline = repo.get_timeline()
    assert [e["event_id"] for e in timeline] == ["b", "c", "a"]


def test_get_timeline_respects_limit():
    repo = _repo()
    for i in range(5):
        _insert(repo, str(i), created_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc))

    timeline = repo.get_timeline(limit=2)
    assert [e["event_id"] for e in timeline] == ["4", "3"]


def test_get_timeline_since_excludes_older_events():
    repo = _repo()
    _insert(repo, "old", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _insert(repo, "new", created_at=datetime(2026, 1, 5, tzinfo=timezone.utc))

    timeline = repo.get_timeline(since=datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert [e["event_id"] for e in timeline] == ["new"]


def test_get_timeline_since_is_inclusive_of_the_exact_boundary():
    repo = _repo()
    boundary = datetime(2026, 1, 3, tzinfo=timezone.utc)
    _insert(repo, "at_boundary", created_at=boundary)

    timeline = repo.get_timeline(since=boundary)
    assert [e["event_id"] for e in timeline] == ["at_boundary"]


def test_record_is_idempotent_upsert_by_event_id():
    repo = _repo()
    data = {
        "event_id": "e1", "created_at": "2026-01-01T00:00:00Z", "user_id": "u1",
        "virtual_environment_id": None, "endpoint_id": None, "runtime_type": None,
        "function_id": "add", "version": 1,
    }
    repo.record("FunctionRegistered", data)
    repo.record("FunctionRegistered", data)  # simulate at-least-once redelivery

    assert repo._collection.count_documents({}) == 1
    doc = repo._collection.find_one({"_id": "e1"})
    assert doc["event_type"] == "FunctionRegistered"
    assert doc["meta"]["function_id"] == "add"
