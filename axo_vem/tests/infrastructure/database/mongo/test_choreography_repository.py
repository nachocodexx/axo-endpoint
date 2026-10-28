import mongomock

from axo_vem.domain.choreography.choreography import Choreography
from axo_vem.domain.events.models import ChoreographyGraph, ChoreographyNode
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository


def _repository():
    db = mongomock.MongoClient()["test"]
    return MongoChoreographyRepository(db["choreographies"], db["choreography_versions"], db["choreography_names"])


def _choreography(version=1, name="pipeline", nodes=1):
    graph = ChoreographyGraph(nodes=[ChoreographyNode(node_id=f"n{i}", kind="function", function_id="f")
                                     for i in range(nodes)])
    return Choreography(choreography_id="c1", name=name, owner_user_id="u1", version=version, graph=graph,
                        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00")


def test_save_keeps_the_newest_and_every_version():
    repository = _repository()
    repository.save(_choreography(1))
    repository.save(_choreography(2, nodes=2))
    repository.save(_choreography(1))                      # replayed late: only its version row
    assert repository.get("c1").version == 2
    assert repository.get_by_name("pipeline").choreography_id == "c1"
    assert [v["version"] for v in repository.versions("c1")] == [2, 1]
    assert len(repository.get_version("c1", 1).graph.nodes) == 1


def test_names_and_soft_delete():
    repository = _repository()
    assert repository.reserve_name("pipeline", "c1") and repository.reserve_name("pipeline", "c1")
    assert not repository.reserve_name("pipeline", "c2")
    repository.release_name("pipeline", "c2")              # not the holder: nothing happens
    assert not repository.reserve_name("pipeline", "c2")
    repository.save(_choreography())
    repository.soft_delete("c1", "2026-01-02T00:00:00+00:00")
    assert repository.get("c1") is None and repository.list() == []
    assert repository.raw("c1")["deleted_at"]


def test_triggers_survive_new_versions_and_are_found_by_bucket():
    repository = _repository()
    repository.save(_choreography())
    repository.set_triggers("c1", [{"trigger_id": "t1", "bucket": "alice/raw"}])
    repository.save(_choreography(2))
    assert repository.get("c1").triggers == [{"trigger_id": "t1", "bucket": "alice/raw"}]
    assert [c.choreography_id for c in repository.triggers_for_bucket("alice/raw")] == ["c1"]
    assert repository.triggers_for_bucket("bob/raw") == []
