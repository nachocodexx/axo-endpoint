from __future__ import annotations

from typing import Callable, Optional, Union

from axo_shared.activity.models import (
    CONTAINER_CRASHED_EVENT,
    CONTAINER_READY_EVENT,
    CONTAINER_SPAWNED_EVENT,
    FUNCTION_BUILD_COMPLETED_EVENT,
    FUNCTION_BUILD_STARTED_EVENT,
)
from axo_shared.events import models as event_models
from axo_shared.functions.models import FunctionRecord
from axo_shared.functions.params_schema import params_schema_to_list
from axo_endpoint.core.events.bus import Event
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.objects import (
    ACTIVE_OBJECT_CALLED_EVENT,
    ACTIVE_OBJECT_CREATED_EVENT,
    ACTIVE_OBJECT_DELETED_EVENT,
    ACTIVE_OBJECT_SIZES_EVENT,
    ACTIVE_OBJECT_UPGRADED_EVENT,
)
from axo_endpoint.core.storage.backend import StorageBackend, StorageKey
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event as LogEvent
from axo_endpoint.service.consensus_loop import (
    CLUSTER_DEGRADED_EVENT,
    CLUSTER_QUORUM_LOST_EVENT,
    CLUSTER_QUORUM_RESTORED_EVENT,
)
from axo_endpoint.service.transport.reliable_event_publisher import ReliableEventPublisher

_Logger = Union[Log, DumbLogger]


class ExternalEventForwardingBridge:
    """Bridges the internal event bus <-> ReliableEventPublisher, translating
    already-emitted internal lifecycle events into the axo_shared.events
    taxonomy and forwarding them to axo_vem. Same one-method-
    per-event-group shape as ActivityTrackingBridge -- a separate bridge
    subscribing to the same bus events, not a change to any existing one.

    Every event is constructed as a typed Pydantic model first, then
    forwarded via ``publisher.publish(event_type, event.model_dump(mode="json"))``
    -- constructing the model gets field validation for free at this point,
    catching a malformed payload immediately instead of silently forwarding
    a bad dict.
    """

    def __init__(
        self,
        publisher: ReliableEventPublisher,
        endpoint_id: str,
        results: StorageBackend,
        function_registry: FunctionRegistry,
        get_virtual_environment_id: Optional[Callable[[], Optional[str]]] = None,
        logger: _Logger = None,
        attempt_of: Optional[Callable[[str], Optional[int]]] = None,
    ) -> None:
        self._publisher = publisher
        self._attempt_of_fn = attempt_of
        self._endpoint_id = endpoint_id
        self._results = results
        self._function_registry = function_registry
        # Bound-method closure, mirrors the same pattern App already uses
        # for VirtualEnvAssignHandler -- resolved at call time, so it's safe
        # to pass even before App has assigned self._virtual_environment_id
        # in its own __init__. Optional/defaulted so callers that don't care
        # about virtual-environment stamping (e.g. tests) don't need to wire
        # up a callback that always returns None.
        self._get_virtual_environment_id = get_virtual_environment_id or (lambda: None)
        self._logger: _Logger = logger or DumbLogger()

    def _attempt_of(self, job_id: str) -> int:
        return (self._attempt_of_fn(job_id) if self._attempt_of_fn is not None else None) or 1

    def _publish(self, event_type: str, event: event_models.EventEnvelope) -> None:
        event = event.model_copy(update={"virtual_environment_id": self._get_virtual_environment_id()})
        self._publisher.publish(event_type, event.model_dump(mode="json"))

    def _get_function_record(self, function_id: str, version: Optional[int] = None) -> Optional[FunctionRecord]:
        """Looks up a function's record for its runtime_type/version -- the
        bus payload for most function/job events only ever carries
        function_id (+ sometimes version), never runtime_spec directly."""
        key = StorageKey(id=function_id, version=version) if version is not None else StorageKey(id=function_id)
        result = self._function_registry.get(key)
        if result.is_err:
            return None
        return result.unwrap()

    def _runtime_type_for(self, function_id: str, version: Optional[int] = None) -> Optional[str]:
        record = self._get_function_record(function_id, version)
        if record is None or record.runtime_spec is None:
            return None
        return record.runtime_spec.type

    def _get_job_error(self, job_id: str) -> str:
        get_result = self._results.get(StorageKey(id=job_id))
        result = get_result.unwrap() if get_result.is_ok else None
        return result.error if result is not None else "unknown error"

    def _log_version_unknown(self, event: Event, function_id: str) -> None:
        """Called whenever a bus event that's supposed to carry a function's
        version doesn't have one -- e.g. a handle built before a job was
        ever actually dispatched (see InvocationHandle's docstring). Never
        invent a version (a past bug here coerced a missing version to the
        literal int 0 with `or 0`, which axo_vem then upserted as a brand
        new, entirely fictitious "version 0" function record with no
        runtime_spec) -- callers skip publishing the version-requiring event
        instead."""
        self._logger.warning_event(
            LogEvent.External.VERSION_UNKNOWN_SKIPPED,
            component=Component.EXTERNAL_FORWARDING,
            function_id=function_id,
            bus_event_type=event.event_type,
        )

    # ── function lifecycle ──────────────────────────────────────────────────

    def on_function_event(self, event: Event) -> None:
        """Subscribed to FunctionState.REGISTERED.value only -- the other
        FunctionState values (COLD_START/RUNNING/COMPLETED/FAILED/IDLE/
        EVICTED) are only ever reached via FunctionRegistry.transition(),
        which nothing in this codebase currently calls, so subscribing to
        them would be dead code. Real deploy/activate/deactivate signals
        come from the process/container runtimes' own events instead (see
        on_function_deployed/on_job_started/on_job_finished below)."""
        function_id = event.payload["function_id"]
        version = event.payload["version"]
        record = self._get_function_record(function_id, version)
        # secret env values never leave the endpoints
        runtime_spec = record.runtime_spec.public_dict() if record and record.runtime_spec else None
        runtime_type = record.runtime_spec.type if record and record.runtime_spec else None
        params_schema = params_schema_to_list(record.params_schema) if record else None
        self._publish(event_models.FUNCTION_REGISTERED, event_models.FunctionRegistered(
            endpoint_id=self._endpoint_id,
            function_id=function_id,
            version=version,
            name=record.name if record else None,
            runtime_spec=runtime_spec,
            runtime_type=runtime_type,
            params_schema=params_schema,
            code_format=record.code_format if record else None,
            code_hash=record.code_hash if record else None,
        ))

    def on_function_updated(self, event: Event) -> None:
        """Subscribed to FUNCTION_UPDATED_EVENT (FunctionRegistry.update()) --
        forwards the record's current, post-merge params_schema/env_vars
        (not just whichever fields this particular update call touched), so
        a listener always sees the function's full current config."""
        function_id = event.payload["function_id"]
        version = event.payload["version"]
        record = self._get_function_record(function_id, version)
        params_schema = params_schema_to_list(record.params_schema) if record else None
        runtime_spec = record.runtime_spec.public_dict() if record and record.runtime_spec else None
        env_vars = runtime_spec["env_vars"] if runtime_spec else None
        self._publish(event_models.FUNCTION_UPDATED, event_models.FunctionUpdated(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version,
            params_schema=params_schema, env_vars=env_vars, runtime_spec=runtime_spec,
        ))

    def on_function_register_failed(self, event: Event) -> None:
        """Subscribed to FUNCTION_REGISTER_FAILED_EVENT (FunctionRegisterHandler's
        storage-failure branch). No version was ever assigned when
        registration itself failed -- same "never invent one" rule as every
        other handler in this file (see _log_version_unknown)."""
        function_id = event.payload["function_id"]
        version = event.payload.get("version")
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        failure = event.payload["failure"]
        self._publish(event_models.FUNCTION_REGISTER_FAILED, event_models.FunctionRegisterFailed(
            endpoint_id=self._endpoint_id,
            function_id=function_id,
            version=version,
            failure=event_models.FailureDetail(**failure),
        ))

    def on_function_deployed(self, event: Event) -> None:
        """Subscribed to FUNCTION_DEPLOYED_EVENT (process worker spawn
        success) and CONTAINER_SPAWNED_EVENT/CONTAINER_READY_EVENT (container
        start attempt / confirmed healthy -- both map here, since they're two
        distinct real moments within the same "being deployed" phase)."""
        function_id = event.payload["function_id"]
        version = event.payload.get("version")
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        runtime_type = "container" if event.event_type in (CONTAINER_SPAWNED_EVENT, CONTAINER_READY_EVENT) else "process"
        self._publish(event_models.FUNCTION_DEPLOYED, event_models.FunctionDeployed(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version, runtime_type=runtime_type,
        ))

    def on_function_deploy_failed(self, event: Event) -> None:
        """Subscribed to FUNCTION_DEPLOY_FAILED_EVENT (process spawn failure)
        and CONTAINER_CRASHED_EVENT (container readiness timeout -- the only
        place that bus event is emitted, so this is always a deploy-phase
        failure, never a later mid-execution crash)."""
        function_id = event.payload["function_id"]
        version = event.payload.get("version")
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        is_container = event.event_type == CONTAINER_CRASHED_EVENT
        runtime_type = "container" if is_container else "process"
        failure = event_models.FailureDetail(
            error_class="CONTAINER_ERROR" if is_container else "FUNCTION_RUNTIME_ERROR",
            error_code=0,
            component="container_spawner" if is_container else "process_runtime",
            message=event.payload.get("error_message", "deploy failed"),
        )
        self._publish(event_models.FUNCTION_DEPLOY_FAILED, event_models.FunctionDeployFailed(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version,
            failure=failure, runtime_type=runtime_type,
        ))

    def on_function_stopped(self, event: Event) -> None:
        """Subscribed to FUNCTION_STOPPED_EVENT -- emitted by both runtimes'
        idle-TTL/max-invocations sweeps and an explicit container dismissal.
        Graceful teardown only -- a process crash is FunctionCrashed instead
        (on_function_crashed below), a distinct event, not a reason string
        on this one."""
        function_id = event.payload["function_id"]
        version = event.payload.get("version")
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        runtime_type = self._runtime_type_for(function_id, version)
        self._publish(event_models.FUNCTION_STOPPED, event_models.FunctionStopped(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version,
            reason=event.payload["reason"], runtime_type=runtime_type,
        ))

    def on_function_crashed(self, event: Event) -> None:
        """Subscribed to FUNCTION_CRASHED_EVENT -- a process worker's
        ungraceful exit. Container mid-run crash/OOM detection has no
        monitoring mechanism yet, so this only ever fires with
        runtime_type="process" today."""
        function_id = event.payload["function_id"]
        version = event.payload.get("version")
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        runtime_type = self._runtime_type_for(function_id, version)
        self._publish(event_models.FUNCTION_CRASHED, event_models.FunctionCrashed(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version, runtime_type=runtime_type,
        ))

    def on_function_build_event(self, event: Event) -> None:
        """Subscribed to FUNCTION_BUILD_STARTED/COMPLETED/FAILED_EVENT
        (ContainerSummoner.build_runner_image()) -- always runtime_type
        "container", since only container functions ever need a base image
        built."""
        function_id = event.payload.get("function_id")
        python_version = event.payload["python_version"]
        image_tag = event.payload["image_tag"]
        if event.event_type == FUNCTION_BUILD_STARTED_EVENT:
            self._publish(event_models.FUNCTION_BUILD_STARTED, event_models.FunctionBuildStarted(
                endpoint_id=self._endpoint_id, function_id=function_id,
                python_version=python_version, image_tag=image_tag, runtime_type="container",
            ))
        elif event.event_type == FUNCTION_BUILD_COMPLETED_EVENT:
            self._publish(event_models.FUNCTION_BUILD_COMPLETED, event_models.FunctionBuildCompleted(
                endpoint_id=self._endpoint_id, function_id=function_id,
                python_version=python_version, image_tag=image_tag,
                duration_ms=event.payload["duration_ms"], runtime_type="container",
            ))
        else:
            failure = event_models.FailureDetail(
                error_class="CONTAINER_ERROR", error_code=0, component="container_spawner",
                message=event.payload.get("error_message", "build failed"),
            )
            self._publish(event_models.FUNCTION_BUILD_FAILED, event_models.FunctionBuildFailed(
                endpoint_id=self._endpoint_id, function_id=function_id,
                python_version=python_version, image_tag=image_tag,
                failure=failure, runtime_type="container",
            ))

    def on_function_deleted(self, event: Event) -> None:
        """Subscribed to FUNCTION_DELETED_EVENT (FunctionRegistry.delete())."""
        self._publish(event_models.FUNCTION_DELETED, event_models.FunctionDeleted(
            endpoint_id=self._endpoint_id,
            function_id=event.payload["function_id"],
            version=event.payload["version"],
        ))

    def on_function_delete_failed(self, event: Event) -> None:
        """Subscribed to FUNCTION_DELETE_FAILED_EVENT (FunctionDeleteHandler's
        storage-failure branch)."""
        failure = event.payload["failure"]
        self._publish(event_models.FUNCTION_DELETE_FAILED, event_models.FunctionDeleteFailed(
            endpoint_id=self._endpoint_id,
            function_id=event.payload["function_id"],
            version=event.payload["version"],
            failure=event_models.FailureDetail(**failure),
        ))

    # ── job pipeline ─────────────────────────────────────────────────────────

    def on_job_queued(self, event: Event) -> None:
        """Subscribed to JOB_SUBMITTED -- fires when a job is accepted,
        before dispatch (a rename for past-tense consistency, not a new
        concept)."""
        function_id = event.payload["function_id"]
        version = event.payload.get("function_version")
        self._publish(event_models.JOB_QUEUED, event_models.JobQueued(
            endpoint_id=self._endpoint_id,
            job_id=event.payload["job_id"],
            function_id=function_id,
            version=version,
            params=event.payload.get("params"),
            runtime_type=self._runtime_type_for(function_id, version),
        ))

    def on_job_started(self, event: Event) -> None:
        """Subscribed to JOB_STARTED_EVENT (a worker/container actually began
        executing) -- also publishes FunctionActivated, since a job starting
        on a function's worker IS that function becoming active; no separate
        bus event carries a job_id for the Function domain to key off.

        Trusts event.payload["function_version"] -- the version the
        runtime actually dispatched -- rather than re-deriving "whatever
        the registry currently considers latest" via
        _get_function_record(function_id) with no version. That guess was
        actively wrong whenever a function was re-registered with a new
        version while an older-version worker was still mid-job.

        JobStarted tolerates an unknown version (it's Optional there, same
        as on JobQueued) and always gets published -- but FunctionActivated
        requires one, so that second publish is skipped (never faked with a
        made-up 0) whenever the version genuinely isn't known yet, e.g. a
        handle built before dispatch ever actually happened (see
        InvocationHandle's docstring)."""
        job_id = event.payload["job_id"]
        function_id = event.payload["function_id"]
        version = event.payload.get("function_version")
        record = self._get_function_record(function_id, version) if version is not None else None
        runtime_type = record.runtime_spec.type if record and record.runtime_spec else None

        self._publish(event_models.JOB_STARTED, event_models.JobStarted(
            endpoint_id=self._endpoint_id, job_id=job_id, function_id=function_id,
            version=version, params=event.payload.get("params"),
            runtime_type=runtime_type, attempt=self._attempt_of(job_id),
        ))
        if version is None:
            self._log_version_unknown(event, function_id)
            return
        self._publish(event_models.FUNCTION_ACTIVATED, event_models.FunctionActivated(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version,
            job_id=job_id, runtime_type=runtime_type,
        ))

    def on_job_finished(self, event: Event) -> None:
        """Subscribed to JOB_COMPLETED/JOB_FAILED -- also publishes
        FunctionDeactivated, mirroring on_job_started's FunctionActivated
        pairing (a job finishing is exactly when its worker goes back to
        idle/available). Trusts event.payload["function_version"], same
        rationale as on_job_started above.

        JobCompleted/JobFailed never carried a version field at all, so
        they always get published regardless -- only FunctionDeactivated
        (which requires one) is skipped when the version is unknown."""
        job_id = event.payload["job_id"]
        function_id = event.payload["function_id"]
        version = event.payload.get("function_version")
        duration_ms = event.payload.get("duration_ms")
        record = self._get_function_record(function_id, version) if version is not None else None
        runtime_type = record.runtime_spec.type if record and record.runtime_spec else None

        if event.event_type == "JOB_COMPLETED":
            self._publish(event_models.JOB_COMPLETED, event_models.JobCompleted(
                endpoint_id=self._endpoint_id, job_id=job_id, function_id=function_id,
                duration_ms=duration_ms, runtime_type=runtime_type, attempt=event.payload.get("attempt") or 1,
            ))
            state = "COMPLETED"
        else:
            # The traceback goes only on the attempt record (JobAttemptRecorded),
            # which the VEM keeps for Developers: never into the event log.
            given = {**event.payload["failure"], "traceback": None} if event.payload.get("failure") else None
            failure = event_models.FailureDetail(**given) if given else event_models.FailureDetail(
                error_class="FUNCTION_RUNTIME_ERROR", error_code=0, component="runtime",
                message=self._get_job_error(job_id),
            )
            self._publish(event_models.JOB_FAILED, event_models.JobFailed(
                endpoint_id=self._endpoint_id, job_id=job_id, function_id=function_id,
                failure=failure, duration_ms=duration_ms, runtime_type=runtime_type,
                attempt=event.payload.get("attempt") or 1,
            ))
            state = "FAILED"

        if version is None:
            self._log_version_unknown(event, function_id)
            return
        self._publish(event_models.FUNCTION_DEACTIVATED, event_models.FunctionDeactivated(
            endpoint_id=self._endpoint_id, function_id=function_id, version=version,
            state=state, runtime_type=runtime_type,
        ))

    # ── consensus ────────────────────────────────────────────────────────────

    def on_leader_elected(self, event: Event) -> None:
        """Subscribed to the LEADER_ELECTED bus event (service/consensus_loop.py)."""
        self._publish(event_models.LEADER_ELECTED, event_models.LeaderElected(
            endpoint_id=self._endpoint_id,
            leader_ids=list(event.payload["leader_ids"]),
            term=event.payload["term"],
        ))

    def on_consensus_view_changed(self, event: Event) -> None:
        """Subscribed to the CONSENSUS_VIEW_CHANGED bus event (service/consensus_loop.py)."""
        self._publish(event_models.CONSENSUS_VIEW_CHANGED, event_models.ConsensusViewChanged(
            endpoint_id=self._endpoint_id,
            term=event.payload["term"],
            was_leader=event.payload["was_leader"],
            is_leader=event.payload["is_leader"],
            leader_ids=list(event.payload["leader_ids"]),
        ))

    def on_active_object_event(self, event: Event) -> None:
        """Subscribed to the ObjectCoordinator's ACTIVE_OBJECT_*_EVENTs (the
        leader's): their payloads already hold each typed event's fields."""
        p = event.payload
        if event.event_type == ACTIVE_OBJECT_CREATED_EVENT:
            self._publish(event_models.ACTIVE_OBJECT_CREATED, event_models.ActiveObjectCreated(
                endpoint_id=self._endpoint_id, user_id=p.get("created_by"), **p))
        elif event.event_type == ACTIVE_OBJECT_CALLED_EVENT:
            failure = p.get("failure")
            self._publish(event_models.ACTIVE_OBJECT_CALLED, event_models.ActiveObjectCalled(
                endpoint_id=self._endpoint_id, **{**p, "failure": event_models.FailureDetail(**failure) if failure else None}))
        elif event.event_type == ACTIVE_OBJECT_UPGRADED_EVENT:
            self._publish(event_models.ACTIVE_OBJECT_UPGRADED, event_models.ActiveObjectUpgraded(
                endpoint_id=self._endpoint_id, **p))
        elif event.event_type == ACTIVE_OBJECT_DELETED_EVENT:
            self._publish(event_models.ACTIVE_OBJECT_DELETED, event_models.ActiveObjectDeleted(
                endpoint_id=self._endpoint_id, **p))
        elif event.event_type == ACTIVE_OBJECT_SIZES_EVENT:
            self._publish(event_models.ACTIVE_OBJECT_STATE_SIZES_REPORTED, event_models.ActiveObjectStateSizesReported(
                endpoint_id=self._endpoint_id, **p))

    def on_cluster_quorum_event(self, event: Event) -> None:
        """Subscribed to CLUSTER_QUORUM_LOST/RESTORED_EVENT and
        CLUSTER_DEGRADED_EVENT (service/consensus_loop.py's derived quorum
        tracking)."""
        if event.event_type == CLUSTER_QUORUM_LOST_EVENT:
            self._publish(event_models.CLUSTER_QUORUM_LOST, event_models.ClusterQuorumLost(
                endpoint_id=self._endpoint_id,
                term=event.payload["term"],
                member_count=event.payload["member_count"],
                quorum_size=event.payload["quorum_size"],
            ))
        elif event.event_type == CLUSTER_QUORUM_RESTORED_EVENT:
            self._publish(event_models.CLUSTER_QUORUM_RESTORED, event_models.ClusterQuorumRestored(
                endpoint_id=self._endpoint_id,
                term=event.payload["term"],
                member_count=event.payload["member_count"],
                quorum_size=event.payload["quorum_size"],
            ))
        elif event.event_type == CLUSTER_DEGRADED_EVENT:
            self._publish(event_models.CLUSTER_DEGRADED, event_models.ClusterDegraded(
                endpoint_id=self._endpoint_id,
                term=event.payload["term"],
                member_count=event.payload["member_count"],
                quorum_size=event.payload["quorum_size"],
                evicted_peer_id=event.payload["evicted_peer_id"],
            ))
