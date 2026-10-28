import mongomock
import pytest

from axo_vem.application.compute.default_version import FunctionDefaults
from axo_vem.domain.errors import ConflictError
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository


@pytest.fixture
def world():
    db = mongomock.MongoClient()["axo_vem"]
    for v in (1, 2, 3):
        db["functions"].insert_one({"_id": f"f:{v}", "function_id": "f", "version": v, "name": "resize", "deleted_at": None})
    activity = MongoActivityRepository(db["unified_activity"])
    return db, FunctionDefaults(db["functions"], db["function_defaults"], activity, "ve1", now_fn=lambda: 1_800_000_000.0)


def test_the_newest_is_the_default_until_someone_picks_another(world):
    db, defaults = world
    assert defaults.version_of("f") == 3
    defaults.set("f", 2, by="ana", by_name="Ana")
    assert defaults.version_of("f") == 2
    row = db["unified_activity"].find_one({"event_type": "FunctionDefaultVersionChanged"})
    assert row["meta"]["version"] == 2 and row["meta"]["previous_version"] == 3 and row["area"] == "function"


def test_a_new_version_becomes_the_default(world):
    db, defaults = world
    defaults.set("f", 1)
    db["functions"].insert_one({"_id": "f:4", "function_id": "f", "version": 4, "deleted_at": None})
    defaults.follow_newest("f")
    assert defaults.version_of("f") == 4


def test_deleting_the_chosen_version_gives_way_to_the_newest_left(world):
    db, defaults = world
    defaults.set("f", 2)
    db["functions"].update_one({"_id": "f:2"}, {"$set": {"deleted_at": "2026-10-22T00:00:00Z"}})
    assert defaults.version_of("f") == 3
    with pytest.raises(ConflictError):
        defaults.set("f", 2)


def test_picking_the_newest_follows_the_newest_from_then_on(world):
    db, defaults = world
    defaults.set("f", 3)
    assert db["function_defaults"].count_documents({}) == 0
