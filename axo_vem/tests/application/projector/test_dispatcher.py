import asyncio
from datetime import datetime

import mongomock
import pytest

from axo_shared.events import models

from axo_vem.application.projector.dispatcher import apply_event
from axo_vem.application.projector.handlers import ProjectorHandlers
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.consensus_repository import MongoConsensusRepository
from axo_vem.infrastructure.database.mongo.endpoint_repository import MongoEndpointRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository


def _handlers():
    db = mongomock.MongoClient()["test"]
    handlers = ProjectorHandlers(
        activity_recorder=MongoActivityRepository(db["unified_activity"]),
        endpoint_repository=MongoEndpointRepository(db["endpoints"]),
        function_repository=MongoFunctionRepository(db["functions"]),
        consensus_recorder=MongoConsensusRepository(db["consensus"]),
        job_repository=MongoJobRepository(db["jobs"]),
        choreography_repository=MongoChoreographyRepository(db["choreographies"]),
    )
    return handlers, db


def test_endpoint_started_upserts_by_endpoint_id():
    handlers, db = _handlers()
    event = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, event.model_dump(mode="json"))

    doc = db["endpoints"].find_one({"_id": "n0"})
    assert doc["router_bind"] == "tcp://0.0.0.0:5555"
    assert doc["status"] == "running"


def test_endpoint_started_also_records_unified_activity():
    handlers, db = _handlers()
    event = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, event.model_dump(mode="json"))

    doc = db["unified_activity"].find_one({"_id": event.event_id})
    assert doc is not None
    assert doc["event_type"] == models.ENDPOINT_STARTED
    assert doc["endpoint_id"] == "n0"
    assert doc["meta"]["router_bind"] == "tcp://0.0.0.0:5555"
    assert isinstance(doc["created_at"], datetime)


def test_endpoint_started_broadcasts_over_ws():
    handlers, _ = _handlers()
    ws = _FakeWebSocket()

    async def scenario():
        broadcaster = Broadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.register("endpoints", ws)

        event = models.EndpointStarted(
            endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
        )
        apply_event(handlers, models.ENDPOINT_STARTED, event.model_dump(mode="json"), broadcaster)

        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert len(ws.sent) == 1
    assert ws.sent[0]["endpoint_id"] == "n0"
    assert ws.sent[0]["status"] == "running"
    assert ws.sent[0]["router_bind"] == "tcp://0.0.0.0:5555"


def test_endpoint_metrics_reported_does_not_broadcast_over_ws():
    handlers, _ = _handlers()
    ws = _FakeWebSocket()

    async def scenario():
        broadcaster = Broadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.register("endpoints", ws)

        event = models.EndpointMetricsReported(endpoint_id="n0", metrics={"cpu": 0.1})
        apply_event(handlers, models.ENDPOINT_METRICS_REPORTED, event.model_dump(mode="json"), broadcaster)

        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert ws.sent == []


def test_endpoint_metrics_reported_upserts_same_document_as_endpoint_started():
    handlers, db = _handlers()
    started = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, started.model_dump(mode="json"))

    metrics = models.EndpointMetricsReported(endpoint_id="n0", metrics={"queue_depth": 3})
    apply_event(handlers, models.ENDPOINT_METRICS_REPORTED, metrics.model_dump(mode="json"))

    assert db["endpoints"].count_documents({}) == 1
    doc = db["endpoints"].find_one({"_id": "n0"})
    assert doc["router_bind"] == "tcp://0.0.0.0:5555"  # still present from the earlier event
    assert doc["metrics"] == {"queue_depth": 3}
    assert doc["status"] == "running"  # untouched by a metrics-only event


def test_endpoint_stopped_upserts_same_document():
    handlers, db = _handlers()
    started = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, started.model_dump(mode="json"))

    stopped = models.EndpointStopped(endpoint_id="n0", uptime_ms=1234.5)
    apply_event(handlers, models.ENDPOINT_STOPPED, stopped.model_dump(mode="json"))

    doc = db["endpoints"].find_one({"_id": "n0"})
    assert doc["uptime_ms"] == 1234.5
    assert doc["status"] == "stopped"


def test_endpoint_unreachable_flips_status_to_unreachable():
    handlers, db = _handlers()
    started = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, started.model_dump(mode="json"))

    unreachable = models.EndpointUnreachable(endpoint_id="n0", last_seen_at="2026-01-01T00:00:00+00:00")
    apply_event(handlers, models.ENDPOINT_UNREACHABLE, unreachable.model_dump(mode="json"))

    doc = db["endpoints"].find_one({"_id": "n0"})
    assert doc["status"] == "unreachable"
    assert doc["last_seen_at"] == "2026-01-01T00:00:00+00:00"


def test_endpoint_recovered_flips_status_back_to_running():
    handlers, db = _handlers()
    started = models.EndpointStarted(
        endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    apply_event(handlers, models.ENDPOINT_STARTED, started.model_dump(mode="json"))
    unreachable = models.EndpointUnreachable(endpoint_id="n0", last_seen_at="2026-01-01T00:00:00+00:00")
    apply_event(handlers, models.ENDPOINT_UNREACHABLE, unreachable.model_dump(mode="json"))

    recovered = models.EndpointRecovered(endpoint_id="n0")
    apply_event(handlers, models.ENDPOINT_RECOVERED, recovered.model_dump(mode="json"))

    doc = db["endpoints"].find_one({"_id": "n0"})
    assert doc["status"] == "running"


def test_function_registered_upserts_by_function_id_and_version():
    handlers, db = _handlers()
    event = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1, runtime_spec=None)
    apply_event(handlers, models.FUNCTION_REGISTERED, event.model_dump(mode="json"))

    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["function_id"] == "add"
    assert doc["version"] == 1


def test_function_registered_also_records_unified_activity():
    handlers, db = _handlers()
    event = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, event.model_dump(mode="json"))

    doc = db["unified_activity"].find_one({"_id": event.event_id})
    assert doc is not None
    assert doc["event_type"] == models.FUNCTION_REGISTERED
    assert doc["endpoint_id"] == "n0"
    assert doc["meta"]["function_id"] == "add"


def test_function_activated_upserts_same_document():
    handlers, db = _handlers()
    registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"))

    activated = models.FunctionActivated(endpoint_id="n0", function_id="add", version=1, job_id="job1")
    apply_event(handlers, models.FUNCTION_ACTIVATED, activated.model_dump(mode="json"))

    assert db["functions"].count_documents({}) == 1
    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["job_id"] == "job1"


def test_function_deployed_sets_container_status_running():
    handlers, db = _handlers()
    registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"))

    deployed = models.FunctionDeployed(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_DEPLOYED, deployed.model_dump(mode="json"))

    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["container_status"] == "running"


def test_function_activated_sets_container_status_running():
    handlers, db = _handlers()
    registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"))

    activated = models.FunctionActivated(endpoint_id="n0", function_id="add", version=1, job_id="job1")
    apply_event(handlers, models.FUNCTION_ACTIVATED, activated.model_dump(mode="json"))

    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["container_status"] == "running"


@pytest.mark.parametrize("event_type,event", [
    (models.FUNCTION_DEPLOY_FAILED, models.FunctionDeployFailed(
        endpoint_id="n0", function_id="add", version=1,
        failure=models.FailureDetail(error_class="BuildError", error_code=0, component="build", message="boom"),
    )),
    (models.FUNCTION_DEACTIVATED, models.FunctionDeactivated(endpoint_id="n0", function_id="add", version=1, state="idle")),
    (models.FUNCTION_STOPPED, models.FunctionStopped(endpoint_id="n0", function_id="add", version=1, reason="idle_ttl")),
    (models.FUNCTION_CRASHED, models.FunctionCrashed(endpoint_id="n0", function_id="add", version=1)),
])
def test_function_lifecycle_failure_events_set_container_status_stopped(event_type, event):
    handlers, db = _handlers()
    registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"))
    deployed = models.FunctionDeployed(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_DEPLOYED, deployed.model_dump(mode="json"))

    apply_event(handlers, event_type, event.model_dump(mode="json"))

    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["container_status"] == "stopped"


def test_function_deleted_soft_deletes_functions_document():
    handlers, db = _handlers()
    registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"))

    deleted = models.FunctionDeleted(endpoint_id="n0", function_id="add", version=1)
    apply_event(handlers, models.FUNCTION_DELETED, deleted.model_dump(mode="json"))

    doc = db["functions"].find_one({"_id": "add:1"})
    assert doc["deleted_at"] is not None
    activity_doc = db["unified_activity"].find_one({"_id": deleted.event_id})
    assert activity_doc["event_type"] == models.FUNCTION_DELETED


def test_function_registered_broadcasts_over_ws():
    handlers, _ = _handlers()
    ws = _FakeWebSocket()

    async def scenario():
        broadcaster = Broadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.register("functions", ws)

        event = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1, name="add")
        apply_event(handlers, models.FUNCTION_REGISTERED, event.model_dump(mode="json"), broadcaster)

        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert len(ws.sent) == 1
    assert ws.sent[0]["function_id"] == "add"
    assert ws.sent[0]["version"] == 1
    assert ws.sent[0]["name"] == "add"


def test_function_deleted_broadcasts_over_ws():
    handlers, _ = _handlers()
    ws = _FakeWebSocket()

    async def scenario():
        broadcaster = Broadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.register("functions", ws)

        registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
        apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"), broadcaster)

        deleted = models.FunctionDeleted(endpoint_id="n0", function_id="add", version=1)
        apply_event(handlers, models.FUNCTION_DELETED, deleted.model_dump(mode="json"), broadcaster)

        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert len(ws.sent) == 2
    assert ws.sent[1]["function_id"] == "add"
    assert ws.sent[1]["deleted_at"] is not None


def test_function_endpoint_detached_does_not_broadcast_over_ws():
    handlers, _ = _handlers()
    ws = _FakeWebSocket()

    async def scenario():
        broadcaster = Broadcaster()
        broadcaster.bind_loop(asyncio.get_running_loop())
        broadcaster.register("functions", ws)

        registered = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1)
        apply_event(handlers, models.FUNCTION_REGISTERED, registered.model_dump(mode="json"), broadcaster)
        await asyncio.sleep(0.01)
        ws.sent.clear()

        detached = models.FunctionEndpointDetached(endpoint_id="n0", function_id="add", version=1)
        apply_event(handlers, models.FUNCTION_ENDPOINT_DETACHED, detached.model_dump(mode="json"), broadcaster)

        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert ws.sent == []


def test_job_queued_records_unified_activity_and_jobs_collection():
    handlers, db = _handlers()
    queued = models.JobQueued(
        endpoint_id="n0", job_id="job1", function_id="add", version=1, params={"a": 1},
    )
    apply_event(handlers, models.JOB_QUEUED, queued.model_dump(mode="json"))

    assert db["functions"].count_documents({}) == 0
    activity_doc = db["unified_activity"].find_one({"_id": queued.event_id})
    assert activity_doc["meta"]["job_id"] == "job1"
    assert activity_doc["meta"]["function_id"] == "add"
    assert activity_doc["event_type"] == models.JOB_QUEUED

    job_doc = db["jobs"].find_one({"_id": "job1"})
    assert job_doc["status"] == "QUEUED"
    assert job_doc["function_version"] == 1
    assert job_doc["params"] == {"a": 1}


def test_job_lifecycle_updates_the_jobs_collection_end_to_end():
    handlers, db = _handlers()
    queued = models.JobQueued(endpoint_id="n0", job_id="job1", function_id="add", version=1, params={"a": 1})
    apply_event(handlers, models.JOB_QUEUED, queued.model_dump(mode="json"))

    started = models.JobStarted(endpoint_id="n0", job_id="job1", function_id="add", version=1)
    apply_event(handlers, models.JOB_STARTED, started.model_dump(mode="json"))
    doc = db["jobs"].find_one({"_id": "job1"})
    assert doc["status"] == "STARTED"
    assert doc["params"] == {"a": 1}  # preserved from JobQueued, not carried by JobStarted

    completed = models.JobCompleted(endpoint_id="n0", job_id="job1", function_id="add", duration_ms=42.0)
    apply_event(handlers, models.JOB_COMPLETED, completed.model_dump(mode="json"))
    doc = db["jobs"].find_one({"_id": "job1"})
    assert doc["status"] == "COMPLETED"
    assert doc["duration_ms"] == 42.0


def test_job_failed_updates_the_jobs_collection():
    handlers, db = _handlers()
    queued = models.JobQueued(endpoint_id="n0", job_id="job2", function_id="add")
    apply_event(handlers, models.JOB_QUEUED, queued.model_dump(mode="json"))

    failure = models.FailureDetail(
        error_class="FUNCTION_RUNTIME_ERROR", error_code=0, component="runtime", message="boom",
    )
    failed = models.JobFailed(endpoint_id="n0", job_id="job2", function_id="add", failure=failure, duration_ms=5.0)
    apply_event(handlers, models.JOB_FAILED, failed.model_dump(mode="json"))

    doc = db["jobs"].find_one({"_id": "job2"})
    assert doc["status"] == "FAILED"
    assert doc["duration_ms"] == 5.0


def test_consensus_events_key_by_term_not_a_singleton():
    handlers, db = _handlers()
    event = models.LeaderElected(endpoint_id="n0", leader_ids=["n0"], term=1)
    apply_event(handlers, models.LEADER_ELECTED, event.model_dump(mode="json"))

    other_term = models.LeaderElected(endpoint_id="n1", leader_ids=["n1"], term=2)
    apply_event(handlers, models.LEADER_ELECTED, other_term.model_dump(mode="json"))

    assert db["consensus"].count_documents({}) == 2


def test_consensus_events_dedup_reported_by_across_endpoints_for_the_same_term():
    handlers, db = _handlers()
    from_n0 = models.ConsensusViewChanged(
        endpoint_id="n0", term=3, was_leader=False, is_leader=True, leader_ids=["n0"],
    )
    from_n1 = models.ConsensusViewChanged(
        endpoint_id="n1", term=3, was_leader=True, is_leader=False, leader_ids=["n0"],
    )
    apply_event(handlers, models.CONSENSUS_VIEW_CHANGED, from_n0.model_dump(mode="json"))
    apply_event(handlers, models.CONSENSUS_VIEW_CHANGED, from_n1.model_dump(mode="json"))
    # Replaying the same event again must not duplicate the reported_by entry.
    apply_event(handlers, models.CONSENSUS_VIEW_CHANGED, from_n0.model_dump(mode="json"))

    doc = db["consensus"].find_one({"_id": 3})
    assert sorted(doc["reported_by"]) == ["n0", "n1"]


def test_cluster_quorum_lost_upserts_consensus_collection():
    handlers, db = _handlers()
    event = models.ClusterQuorumLost(endpoint_id="n0", term=5, member_count=1, quorum_size=2)
    apply_event(handlers, models.CLUSTER_QUORUM_LOST, event.model_dump(mode="json"))

    doc = db["consensus"].find_one({"_id": 5})
    assert doc["member_count"] == 1


def test_leader_elected_also_records_unified_activity():
    handlers, db = _handlers()
    event = models.LeaderElected(endpoint_id="n0", leader_ids=["n0"], term=1)
    apply_event(handlers, models.LEADER_ELECTED, event.model_dump(mode="json"))

    doc = db["unified_activity"].find_one({"_id": event.event_id})
    assert doc is not None
    assert doc["event_type"] == models.LEADER_ELECTED
    assert doc["endpoint_id"] == "n0"
    assert doc["meta"]["term"] == 1


class _FakeEventPublisher:
    def __init__(self):
        self.appended = []

    def append_to_stream(self, stream_name, event_type, data):
        self.appended.append((stream_name, event_type, data))


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def test_unknown_event_type_raises():
    handlers, db = _handlers()
    with pytest.raises(ValueError):
        apply_event(handlers, "NotARealEventType", {})

    assert db["unified_activity"].count_documents({}) == 0


@pytest.mark.parametrize("event_type", [
    models.USER_PROFILE_CREATED, models.VIRTUAL_ENV_CREATED, models.VIRTUAL_ENV_LEADER_CHANGED,
    models.ENDPOINT_VIRTUAL_ENV_ASSIGNED, models.DATA_BUCKET_CREATED, models.DATA_REGISTERED,
])
def test_legacy_event_types_are_skipped_quietly(event_type):
    """Profiles/VEs belong to the Control Plane and buckets to axo_storage
    now -- an older event log still replays without errors or side effects."""
    handlers, db = _handlers()
    apply_event(handlers, event_type, {"event_id": "e1", "created_at": "2026-01-01T00:00:00+00:00"})
    assert db["unified_activity"].count_documents({}) == 0

