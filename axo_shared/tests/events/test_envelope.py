from datetime import datetime, timezone

import pytest

from axo_shared import wire
from axo_shared.events import envelope, models
from axo_shared.metrics import StatSummary


def _summary_dict():
    stats = StatSummary()
    for v in (10, 20, 30):
        stats.add(v)
    return stats.to_dict()


def test_encode_decode_round_trips_endpoint_started():
    event = models.EndpointStarted(
        endpoint_id="axo-endpoint-0",
        router_bind="tcp://0.0.0.0:5555",
        pub_bind="tcp://0.0.0.0:5556",
    )
    command = envelope.encode_event(models.ENDPOINT_STARTED, "axo-endpoint-0", event.model_dump(mode="json"))

    frames = wire.encode_command(command)
    decoded_command_result = wire.decode_command(frames)
    assert decoded_command_result.is_ok
    decoded_command = decoded_command_result.unwrap()

    result = envelope.decode_event(decoded_command)
    assert result.is_ok
    event_type, endpoint_id, data = result.unwrap()
    assert event_type == models.ENDPOINT_STARTED
    assert endpoint_id == "axo-endpoint-0"
    rehydrated = envelope.rehydrate(event_type, data)
    assert rehydrated.event_id == event.event_id
    assert rehydrated.created_at == event.created_at
    assert rehydrated.router_bind == event.router_bind


def test_encode_decode_round_trips_all_event_kinds():
    cases = [
        (models.ENDPOINT_METRICS_REPORTED, models.EndpointMetricsReported(
            endpoint_id="n0", metrics={"queue_depth": 3})),
        (models.ENDPOINT_STOPPED, models.EndpointStopped(endpoint_id="n0", uptime_ms=100.0)),
        (models.FUNCTION_REGISTERED, models.FunctionRegistered(
            endpoint_id="n0", function_id="add", version=1,
            runtime_spec={"type": "process"})),
        (models.FUNCTION_DEPLOYED, models.FunctionDeployed(endpoint_id="n0", function_id="add", version=1)),
        (models.FUNCTION_ACTIVATED, models.FunctionActivated(
            endpoint_id="n0", function_id="add", version=1, job_id="job1")),
        (models.FUNCTION_DEACTIVATED, models.FunctionDeactivated(
            endpoint_id="n0", function_id="add", version=1, state="COMPLETED")),
        (models.FUNCTION_UPDATED, models.FunctionUpdated(
            endpoint_id="n0", function_id="add", version=1, env_vars={"A": "1"})),
        (models.FUNCTION_STOPPED, models.FunctionStopped(
            endpoint_id="n0", function_id="add", version=1, reason="idle_ttl")),
        (models.FUNCTION_CRASHED, models.FunctionCrashed(endpoint_id="n0", function_id="add", version=1)),
        (models.FUNCTION_DELETED, models.FunctionDeleted(endpoint_id="n0", function_id="add", version=1)),
        (models.LEADER_ELECTED, models.LeaderElected(endpoint_id="n0", leader_ids=["n1"], term=2)),
        (models.CONSENSUS_VIEW_CHANGED, models.ConsensusViewChanged(
            endpoint_id="n0", term=2, was_leader=False, is_leader=True, leader_ids=["n0"])),
        (models.CLUSTER_QUORUM_LOST, models.ClusterQuorumLost(
            endpoint_id="n0", term=1, member_count=1, quorum_size=2)),
        (models.CLUSTER_DEGRADED, models.ClusterDegraded(
            endpoint_id="n0", term=1, member_count=2, quorum_size=2, evicted_peer_id="n1")),
        (models.JOB_QUEUED, models.JobQueued(endpoint_id="n0", job_id="job1", function_id="add")),
        (models.JOB_STARTED, models.JobStarted(endpoint_id="n0", job_id="job1", function_id="add")),
        (models.JOB_COMPLETED, models.JobCompleted(endpoint_id="n0", job_id="job1", function_id="add")),
        (models.ENDPOINT_VIRTUAL_ENV_ASSIGNED, models.EndpointVirtualEnvironmentAssigned(
            endpoint_id="n0", virtual_environment_id="ve1")),
        (models.ENDPOINT_VIRTUAL_ENV_DETACHED, models.EndpointVirtualEnvironmentDetached(
            endpoint_id="n0", previous_virtual_environment_id="ve1")),
        (models.JOB_ATTEMPT_RECORDED, models.JobAttemptRecorded(
            endpoint_id="n0", virtual_environment_id="ve1", job_id="job1", attempt=2, trace_id="t1",
            outcome="failed_user", owner_type="team", owner_id="ml-lab", started_by_type="api_key",
            started_by_id="k-123", function_id="add", function_version=3, endpoint_cores=2.0,
            endpoint_ram_mb=4096, received_at=datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 10, 11, 12, 0, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 10, 11, 12, 0, 3, tzinfo=timezone.utc),
            layer_waits_ms={"control_plane": 5.0, "vem": 3.0, "endpoint": 120.0, "runner": 40.0},
            execution_ms=2400.0, bytes_read={"BK1": 50_000}, bytes_written={"BK2": 10_000},
            cache_hits=3, cache_misses=1)),
        (models.VE_HEARTBEAT, models.VirtualEnvironmentHeartbeat(
            virtual_environment_id="ve1", lifecycle="Scaling", health="Degraded",
            target_endpoints=6, min_endpoints=2, running=3, starting=1, failed=2)),
        (models.VE_HEARTBEAT, models.VirtualEnvironmentHeartbeat(
            virtual_environment_id="ve1", lifecycle="Sleeping", target_endpoints=1, min_endpoints=1)),
        (models.METRIC_SUMMARY_REPORTED, models.MetricSummaryReported(
            virtual_environment_id="ve1", component_kind="endpoint", component_id="n0",
            bin_start=1000.0, bin_width_s=60, summaries=[models.MetricSummary(
                metric="job.exec_ms", dims={"function": "add"}, stats=_summary_dict())])),
    ]

    for event_type, event in cases:
        command = envelope.encode_event(event_type, "n0", event.model_dump(mode="json"))
        frames = wire.encode_command(command)
        decoded_command = wire.decode_command(frames).unwrap()
        result = envelope.decode_event(decoded_command)
        assert result.is_ok, f"{event_type} failed to decode"
        got_event_type, endpoint_id, data = result.unwrap()
        assert got_event_type == event_type
        rehydrated = envelope.rehydrate(got_event_type, data)
        assert rehydrated == event, f"{event_type} did not round-trip"


def test_job_failed_round_trips_with_nested_failure_detail():
    failure = models.FailureDetail(
        error_class="FUNCTION_RUNTIME_ERROR", error_code=0, component="runtime", message="boom",
    )
    event = models.JobFailed(endpoint_id="n0", job_id="job1", function_id="add", failure=failure)
    command = envelope.encode_event(models.JOB_FAILED, "n0", event.model_dump(mode="json"))
    frames = wire.encode_command(command)
    decoded_command = wire.decode_command(frames).unwrap()
    event_type, endpoint_id, data = envelope.decode_event(decoded_command).unwrap()
    rehydrated = envelope.rehydrate(event_type, data)
    assert rehydrated == event
    assert rehydrated.failure.message == "boom"


def test_decode_event_with_missing_keys_returns_err():
    from axo_shared.protocol import Command

    command = Command(
        operation=wire.EVENT_PUBLISH,
        content_type="application/json",
        envelope={"schema_version": 1, "event_type": models.ENDPOINT_STARTED},
        payload=b"",
    )
    result = envelope.decode_event(command)
    assert result.is_err


def test_event_publish_is_a_distinct_operation_constant():
    assert wire.EVENT_PUBLISH not in {
        wire.PING, wire.FUNCTION_REGISTER, wire.FUNCTION_DELETE, wire.JOB_SUBMIT, wire.JOB_RESULT, wire.METRICS,
    }


def test_rehydrate_choreography_created_raises_key_error_pending_envelope_registration():
    """Pins current behavior, not an endorsement of it: Choreography events
    are appended straight to Kurrent by axo_vem's EventPublisher and never
    traverse encode_event/decode_event/rehydrate in practice, so EVENT_TYPES
    doesn't register them. If Choreography events are ever meant to also
    cross the ZMQ envelope (e.g. endpoint -> axo_vem forwarding), EVENT_TYPES
    needs an entry added -- this test exists to make that omission visible
    and intentional-looking, not to silently assume it's fine forever."""
    with pytest.raises(KeyError):
        envelope.rehydrate(models.CHOREOGRAPHY_CREATED, {"choreography_id": "c1"})


def test_message_id_is_only_added_when_given():
    plain = envelope.encode_event(models.JOB_QUEUED, "n0", {})
    assert "message_id" not in plain.envelope
    tagged = envelope.encode_event(models.JOB_QUEUED, "n0", {}, message_id="m-1")
    assert tagged.envelope["message_id"] == "m-1"
    assert envelope.decode_event(tagged).unwrap() == (models.JOB_QUEUED, "n0", {})


@pytest.mark.parametrize("outcome,billable", [
    ("completed", True),
    ("failed_user", True),
    ("cancelled", True),
    ("failed_platform", False),
    ("timed_out_in_queue", False),
])
def test_is_billable(outcome, billable):
    assert models.is_billable(outcome) is billable


def test_metric_summary_stats_rebuild_into_a_stat_summary():
    event = models.MetricSummaryReported(
        component_kind="vem", component_id="vem-1", bin_start=0.0, bin_width_s=60,
        summaries=[models.MetricSummary(metric="queue.length", stats=_summary_dict())],
    )
    data = envelope.decode_event(envelope.encode_event(models.METRIC_SUMMARY_REPORTED, "vem-1",
                                                      event.model_dump(mode="json"))).unwrap()[2]
    rebuilt = StatSummary.from_dict(envelope.rehydrate(models.METRIC_SUMMARY_REPORTED, data).summaries[0].stats)
    assert rebuilt.mean() == 20


def test_an_object_call_travels_the_envelope_and_rehydrates():
    payload = models.ActiveObjectCalled(
        object_id="o1", job_id="j1", trace_id="t1", method="run", class_version=2, outcome="completed",
        revision=4, changed=["count"], size_bytes=10).model_dump(mode="json")
    command = envelope.encode_event(models.ACTIVE_OBJECT_CALLED, "ep-1", payload)
    event_type, _, decoded = envelope.decode_event(command).unwrap()
    event = envelope.rehydrate(event_type, decoded)
    assert isinstance(event, models.ActiveObjectCalled) and event.changed == ["count"] and event.revision == 4
