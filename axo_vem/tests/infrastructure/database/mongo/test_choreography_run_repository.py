import mongomock

from axo_vem.domain.choreography.run import ChoreographyRun, ItemState, StepState
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository


def _run(run_id, status="completed", queued_at="2026-01-01T00:00:00+00:00", user="u1", duration=10.0):
    return ChoreographyRun(run_id=run_id, choreography_id="c1", choreography_name="etl", version=1, status=status,
                           acting_user_id=user, queued_at=queued_at, duration_ms=duration,
                           steps={"a": StepState(node_id="a", kind="function",
                                                 items=[ItemState(index=0, file={"name": "x"})])})


def test_round_trip_insert_once_and_listing():
    repository = MongoChoreographyRunRepository(mongomock.MongoClient()["t"]["runs"])
    assert repository.insert(_run("r1"))
    assert not repository.insert(_run("r1"))
    repository.save(_run("r2", status="queued", queued_at="2026-01-02T00:00:00+00:00"))
    back = repository.get("r1")
    assert back.steps["a"].items[0].file == {"name": "x"}
    assert [r.run_id for r in repository.list("c1")] == ["r2", "r1"]
    assert [r.run_id for r in repository.list("c1", status="queued")] == ["r2"]
    assert [r.run_id for r in repository.list("c1", since="2026-01-01T12:00:00+00:00")] == ["r2"]
    assert [r.run_id for r in repository.active()] == ["r2"]
    assert repository.count("c1", "queued") == 1


def test_usage_and_delete():
    repository = MongoChoreographyRunRepository(mongomock.MongoClient()["t"]["runs"])
    for i, (status, user) in enumerate([("completed", "u1"), ("failed", "u2"), ("completed", "u1")]):
        repository.insert(_run(f"r{i}", status=status, user=user, duration=10.0 * (i + 1)))
    usage = repository.usage("c1")
    assert usage["runs"] == 3 and usage["by_status"] == {"completed": 2, "failed": 1}
    assert abs(usage["success_rate"] - 2 / 3) < 1e-9 and usage["duration_ms"]["max"] == 30.0
    assert repository.delete_for("c1") == 3 and repository.list("c1") == []


def test_overview_gives_last_run_day_counts_and_active_in_one_pass():
    repository = MongoChoreographyRunRepository(mongomock.MongoClient()["t"]["runs"])
    rows = [("r0", "completed", "2026-01-01T00:00:00+00:00"), ("r1", "failed", "2026-01-02T10:00:00+00:00"),
            ("r2", "completed_with_errors", "2026-01-02T11:00:00+00:00"), ("r3", "running", "2026-01-02T12:00:00+00:00")]
    for run_id, status, at in rows:
        repository.insert(_run(run_id, status=status, queued_at=at))
    other = _run("x", queued_at="2026-01-02T09:00:00+00:00")
    other.choreography_id = "c2"
    repository.insert(other)
    view = repository.overview("2026-01-02T00:00:00+00:00")
    assert view["c1"]["last_run"]["run_id"] == "r3" and view["c1"]["last_run"]["status"] == "running"
    assert (view["c1"]["runs_24h"], view["c1"]["errors_24h"], view["c1"]["active_runs"]) == (3, 2, 1)
    assert view["c2"]["runs_24h"] == 1 and view["c2"]["active_runs"] == 0
