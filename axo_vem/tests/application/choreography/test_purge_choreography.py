import pytest

from axo_vem.application.choreography.purge_choreography import PurgeChoreographyUseCase
from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository

from tests.application.choreography.harness import Harness, fn, graph


@pytest.fixture
def h():
    harness = Harness()
    harness.function("a", lambda p, e: {"blob": "x" * 70000})
    yield harness
    harness.close()


def _purge(h):
    return PurgeChoreographyUseCase(h.repository, h.runs, MongoActivityRepository(h.db["unified_activity"]))


def test_purge_needs_a_deleted_choreography(h):
    with pytest.raises(NotFoundError):
        _purge(h).execute(choreography_id="nope")
    cid = h.service.create(user_id="u1", name="p", graph=graph([fn("a")]))["choreography_id"]
    with pytest.raises(ConflictError):
        _purge(h).execute(choreography_id=cid)


def test_purge_removes_versions_and_runs(h):
    cid = h.service.create(user_id="u1", name="p", graph=graph([fn("a")]))["choreography_id"]
    h.wait(h.engine.start("p", user_id="u1")["run_id"])
    h.service.delete(user_id="u1", ref="p")
    _purge(h).execute(choreography_id=cid)
    assert h.repository.raw(cid) is None and h.runs.list(cid) == []
    assert h.db["choreography_versions"].count_documents({}) == 0
