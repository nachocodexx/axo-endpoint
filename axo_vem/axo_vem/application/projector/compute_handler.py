from __future__ import annotations

from typing import Any, Dict, Optional

from axo_vem.domain.compute.consensus_recorder import ConsensusRecorder
from axo_vem.domain.compute.repository import EndpointRepository, FunctionRepository
from axo_vem.domain.events import models
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster

ENDPOINT_EVENT_TYPES = frozenset({
    models.ENDPOINT_STARTED, models.ENDPOINT_METRICS_REPORTED, models.ENDPOINT_STOPPED,
    models.ENDPOINT_UNREACHABLE, models.ENDPOINT_RECOVERED,
})

_WS_TOPIC = "endpoints"


def apply_reading(endpoint_repository: EndpointRepository, data: Dict[str, Any]) -> bool:
    """An endpoint's metric reading (EndpointMetricsReported, every few
    seconds), straight from the ingestion router: only the endpoint's
    current figures change -- it's never appended to the event log nor kept
    as history (the figures over time come from the minute statistics). A
    reading for an endpoint not listed (not started yet, or forgotten) or
    older than the one kept (a resend) changes nothing."""
    endpoint_repository.apply_reading(data["endpoint_id"], data)
    return True
_WS_FUNCTIONS_TOPIC = "functions"

# Function-lifecycle events that carry the full function_id/version/
# runtime_type shape and belong in the functions collection --
# FunctionDeleted/FunctionDeleteFailed get a different treatment (soft-delete
# vs. plain $set) than the rest, handled separately below.
FUNCTION_EVENT_TYPES = frozenset({
    models.FUNCTION_REGISTERED, models.FUNCTION_REGISTER_FAILED,
    models.FUNCTION_DEPLOYED, models.FUNCTION_DEPLOY_FAILED,
    models.FUNCTION_ACTIVATED, models.FUNCTION_DEACTIVATED,
    models.FUNCTION_UPDATED,
    models.FUNCTION_STOPPED, models.FUNCTION_CRASHED,
})

CONSENSUS_EVENT_TYPES = frozenset({
    models.LEADER_ELECTED, models.CONSENSUS_VIEW_CHANGED,
    models.CLUSTER_QUORUM_LOST, models.CLUSTER_QUORUM_RESTORED, models.CLUSTER_DEGRADED,
})

# Neither event carries a status field itself (EventEnvelope's shared shape
# has none) -- derived here from which event fired, rather than changing the
# wire taxonomy, since event_type already unambiguously implies it.
_ENDPOINT_STATUS_BY_EVENT_TYPE = {
    models.ENDPOINT_STARTED: "running",
    models.ENDPOINT_STOPPED: "stopped",
    models.ENDPOINT_UNREACHABLE: "unreachable",
    models.ENDPOINT_RECOVERED: "running",
}

# Same "no event carries a status field, derive it from which one fired"
# rationale as _ENDPOINT_STATUS_BY_EVENT_TYPE above. FunctionRegistered/
# RegisterFailed/Updated aren't container-lifecycle events, so they're
# absent here and leave container_status untouched.
_FUNCTION_CONTAINER_STATUS_BY_EVENT_TYPE = {
    models.FUNCTION_DEPLOYED: "running",
    models.FUNCTION_ACTIVATED: "running",
    models.FUNCTION_DEPLOY_FAILED: "stopped",
    models.FUNCTION_DEACTIVATED: "stopped",
    models.FUNCTION_STOPPED: "stopped",
    models.FUNCTION_CRASHED: "stopped",
}


def apply(
    endpoint_repository: EndpointRepository,
    function_repository: FunctionRepository,
    consensus_recorder: ConsensusRecorder,
    event_type: str,
    data: Dict[str, Any],
    broadcaster: Optional[Broadcaster] = None,
) -> None:
    """Endpoint/Function events are written via apply_event_data() (full
    event field set, preserving fidelity for the dict-based GET read model
    -- see domain/compute/repository.py's docstring), not the narrow
    save(aggregate) methods. Replaces projector/upserts.py's former
    upsert_endpoint/upsert_function/mark_function_deleted/upsert_consensus.

    Status-bearing endpoint events (STARTED/STOPPED/UNREACHABLE/RECOVERED --
    see _ENDPOINT_STATUS_BY_EVENT_TYPE) also broadcast over the shared WS
    broadcaster's "endpoints" topic, the same full payload just written via
    apply_event_data() (which has no return value), so a freshly-connected
    client -- or one that's never heard of this endpoint_id before -- has
    enough to render a brand new row, not just patch an already-rendered
    one. METRICS_REPORTED deliberately doesn't
    broadcast here: they carry no "status" field, and axo-ui's WS handler
    uses that field's presence to distinguish "this can seed a new row"
    from EndpointStatsPoller's separate, differently-shaped container_stats
    ticks on the same topic.

    Every FUNCTION_EVENT_TYPES member (registration through crash) and
    FUNCTION_DELETED also broadcast over the "functions" topic -- unlike
    endpoints, this topic carries no second, differently-shaped message
    type sharing it, so there's no "status field present" gate needed:
    every message here is always a full function payload, letting
    axo-ui's functions list page pick up a freshly registered function (or
    any later status change) without a manual reload. FUNCTION_ENDPOINT_
    DETACHED deliberately doesn't broadcast: it only prunes one entry from
    endpoint_id, an internal accounting detail no UI surfaces live today."""
    if event_type in ENDPOINT_EVENT_TYPES:
        status = _ENDPOINT_STATUS_BY_EVENT_TYPE.get(event_type)
        payload = {**data, "status": status} if status is not None else data
        endpoint_repository.apply_event_data(data["endpoint_id"], payload)
        if broadcaster is not None and status is not None:
            broadcaster.broadcast(_WS_TOPIC, payload)
    elif event_type in FUNCTION_EVENT_TYPES:
        container_status = _FUNCTION_CONTAINER_STATUS_BY_EVENT_TYPE.get(event_type)
        payload = {**data, "container_status": container_status} if container_status is not None else data
        function_repository.apply_event_data(payload)
        if broadcaster is not None:
            broadcaster.broadcast(_WS_FUNCTIONS_TOPIC, payload)
    elif event_type == models.FUNCTION_DELETED:
        function_repository.mark_deleted_from_event(data)
        if broadcaster is not None:
            broadcaster.broadcast(_WS_FUNCTIONS_TOPIC, {**data, "deleted_at": data["created_at"]})
    elif event_type == models.FUNCTION_ENDPOINT_DETACHED:
        function_repository.detach_endpoint_from_event(data)
    elif event_type in CONSENSUS_EVENT_TYPES:
        consensus_recorder.record(data["endpoint_id"], data)
