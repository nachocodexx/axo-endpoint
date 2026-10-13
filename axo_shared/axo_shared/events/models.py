from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from axo_shared.virtual_environment.states import VeHealth, VeLifecycle


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventEnvelope(BaseModel):
    """Base fields every taxonomy event carries. Concrete events subclass this
    directly -- no per-domain intermediate base, since every field here is
    genuinely universal (even UserProfile/VirtualEnvironment events, which
    never travel the ZMQ envelope, still want event_id/created_at for
    Kurrent's own event metadata). A field being irrelevant for a given
    domain (e.g. runtime_type=None on a LeaderElected) is normal."""

    model_config = {"frozen": True}

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=_utcnow)
    user_id: Optional[str] = None
    virtual_environment_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    runtime_type: Optional[Literal["process", "container"]] = None


class FailureDetail(BaseModel):
    """Embedded on every *Failed event. Field names deliberately mirror
    axo_shared.errors.AxoError.to_dict()'s keys so a FailureDetail can be
    built directly off any caught AxoError via from_axo_error, not
    reinvented per call site."""

    model_config = {"frozen": True}

    error_class: str
    error_code: int
    component: str
    message: str
    traceback: Optional[str] = None
    is_transient: bool = False

    @classmethod
    def from_axo_error(
        cls,
        err: "AxoError",  # noqa: F821 -- forward ref, avoids a hard axo_shared.errors import cycle
        component: str,
        is_transient: bool = False,
        traceback: Optional[str] = None,
    ) -> "FailureDetail":
        return cls(
            error_class=err.name,
            error_code=err.code,
            component=component,
            message=err.message,
            traceback=traceback,
            is_transient=is_transient,
        )


# ── Endpoint infrastructure ────────────────────────────────────────────────
# EndpointDeployed/Failed have no natural emission point in this codebase --
# nothing here "deploys" an endpoint, that's an external actor's action --
# so they're defined for taxonomy completeness only, never published.
ENDPOINT_DEPLOYED = "EndpointDeployed"
ENDPOINT_DEPLOY_FAILED = "EndpointDeployFailed"
ENDPOINT_STARTED = "EndpointStarted"
ENDPOINT_METRICS_REPORTED = "EndpointMetricsReported"
ENDPOINT_STOPPED = "EndpointStopped"
ENDPOINT_UNREACHABLE = "EndpointUnreachable"
ENDPOINT_RECOVERED = "EndpointRecovered"


class EndpointDeployed(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""


class EndpointDeployFailed(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""

    failure: FailureDetail


class EndpointStarted(EventEnvelope):
    """Published once by an endpoint at startup, if AXO_ENDPOINT_API_URI is configured."""

    router_bind: str
    pub_bind: str


class EndpointMetricsReported(EventEnvelope):
    """Published periodically (piggybacked on the heartbeat loop). ``metrics``
    is App._collect_metrics()'s dict verbatim."""

    metrics: Dict[str, Any] = Field(default_factory=dict)


class EndpointStopped(EventEnvelope):
    """Published on graceful shutdown (App.stop())."""

    uptime_ms: float


class EndpointUnreachable(EventEnvelope):
    """Published by axo_vem's EndpointLivenessWorker (not by axo_endpoint
    itself -- a dead/partitioned node can't report its own death) when a
    `running` endpoint's last-seen timestamp goes stale and a direct PING
    to it fails. last_seen_at is a snapshot of the endpoint's own
    last-known-seen timestamp at the moment this fired -- audit context for
    how stale the node already was, not re-derived later."""

    last_seen_at: Optional[str] = None


class EndpointRecovered(EventEnvelope):
    """Published by EndpointLivenessWorker when a previously `unreachable`
    endpoint answers PING successfully again. endpoint_id comes from
    EventEnvelope; no extra fields needed, mirrors EndpointStopped's
    minimalism."""


# ── Cluster consensus ───────────────────────────────────────────────────────
# ClusterElectionInitiated/Failed: Bully's recompute() is a pure, synchronous,
# always-succeeds function -- there's no real "initiated" phase distinct from
# the result, and no failure mode to report. Defined for completeness only,
# never published.
CLUSTER_ELECTION_INITIATED = "ClusterElectionInitiated"
CLUSTER_ELECTION_FAILED = "ClusterElectionFailed"
LEADER_ELECTED = "LeaderElected"
CONSENSUS_VIEW_CHANGED = "ConsensusViewChanged"
CLUSTER_QUORUM_LOST = "ClusterQuorumLost"
CLUSTER_QUORUM_RESTORED = "ClusterQuorumRestored"
CLUSTER_DEGRADED = "ClusterDegraded"


class ClusterElectionInitiated(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""


class ClusterElectionFailed(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""

    failure: FailureDetail


class LeaderElected(EventEnvelope):
    """Published by an endpoint when its own BullyLeaderElector's term changes."""

    leader_ids: List[str]
    term: int


class ConsensusViewChanged(EventEnvelope):
    """Published by an endpoint whenever its own leader/follower role flips."""

    term: int
    was_leader: bool
    is_leader: bool
    leader_ids: List[str]


class ClusterQuorumLost(EventEnvelope):
    """Live member count (self + peers) dropped below the derived quorum size."""

    term: int
    member_count: int
    quorum_size: int


class ClusterQuorumRestored(EventEnvelope):
    """Live member count recovered back to/above the derived quorum size."""

    term: int
    member_count: int
    quorum_size: int


class ClusterDegraded(EventEnvelope):
    """A peer was evicted but the cluster still holds quorum."""

    term: int
    member_count: int
    quorum_size: int
    evicted_peer_id: str


# ── Function lifecycle ──────────────────────────────────────────────────────
FUNCTION_REGISTERED = "FunctionRegistered"
FUNCTION_REGISTER_FAILED = "FunctionRegisterFailed"
FUNCTION_BUILD_STARTED = "FunctionBuildStarted"
FUNCTION_BUILD_COMPLETED = "FunctionBuildCompleted"
FUNCTION_BUILD_FAILED = "FunctionBuildFailed"
FUNCTION_DEPLOYED = "FunctionDeployed"
FUNCTION_DEPLOY_FAILED = "FunctionDeployFailed"
FUNCTION_ACTIVATED = "FunctionActivated"
FUNCTION_DEACTIVATED = "FunctionDeactivated"
FUNCTION_UPDATED = "FunctionUpdated"
FUNCTION_STOPPED = "FunctionStopped"
FUNCTION_CRASHED = "FunctionCrashed"
FUNCTION_DELETED = "FunctionDeleted"
FUNCTION_DELETE_FAILED = "FunctionDeleteFailed"
FUNCTION_ENDPOINT_DETACHED = "FunctionEndpointDetached"


class FunctionRegistered(EventEnvelope):
    """Published when a function is first registered. ``runtime_spec`` is
    RuntimeSpec.to_dict(), never the function code itself. ``params_schema``
    is params_schema_to_list(...), or empty/None for a function that accepts
    an arbitrary, unvalidated params blob. ``name`` is the caller-given
    display name (function_id is a content-derived hash of
    (user_id, virtual_environment_id, name), never human-readable) -- carried
    only on this event since the read-model projector does a $set merge, so
    it persists on the doc for the rest of that function_id/version's life
    without every later lifecycle event needing to repeat it."""

    function_id: str
    version: int
    name: Optional[str] = None
    runtime_spec: Optional[Dict[str, Any]] = None
    params_schema: Optional[List[Dict[str, Any]]] = None
    owner_user_id: Optional[str] = None


class FunctionRegisterFailed(EventEnvelope):
    function_id: str
    version: int
    failure: FailureDetail


class FunctionBuildStarted(EventEnvelope):
    """Published around ContainerSummoner.build_runner_image() -- only fires
    for container-runtime functions needing a fresh per-Python-version base
    image build. ``function_id`` is None for the (normal, shared) case of a
    generic base-image build not tied to one function."""

    function_id: Optional[str] = None
    python_version: str
    image_tag: str


class FunctionBuildCompleted(EventEnvelope):
    function_id: Optional[str] = None
    python_version: str
    image_tag: str
    duration_ms: float


class FunctionBuildFailed(EventEnvelope):
    function_id: Optional[str] = None
    python_version: str
    image_tag: str
    failure: FailureDetail


class FunctionDeployed(EventEnvelope):
    """A worker/container is being (re)provisioned for this function
    (FunctionState.COLD_START, or a container SPAWNED/READY)."""

    function_id: str
    version: int


class FunctionDeployFailed(EventEnvelope):
    function_id: str
    version: int
    failure: FailureDetail


class FunctionActivated(EventEnvelope):
    """A worker/container just began executing a job (FunctionState.RUNNING)."""

    function_id: str
    version: int
    job_id: str


class FunctionDeactivated(EventEnvelope):
    """A worker/container finished running (FunctionState.COMPLETED/FAILED).
    Job-level outcome lives on the Job* events, not duplicated here --
    ``state`` is kept only as a filtering hint."""

    function_id: str
    version: int
    state: str


class FunctionUpdated(EventEnvelope):
    """An existing version's params_schema/env_vars was mutated in place --
    no new version, no code change. Carries the record's full current
    (post-merge) values, not just whatever fields this update touched.
    Takes effect lazily: an already-running worker/container keeps its old
    config until it next naturally redeploys."""

    function_id: str
    version: int
    params_schema: Optional[List[Dict[str, Any]]] = None
    env_vars: Optional[Dict[str, str]] = None
    # The whole current RuntimeSpec (e.g. a runner policy changed
    # max_concurrency) -- the VEM's read model replaces its copy with it.
    runtime_spec: Optional[Dict[str, Any]] = None


class FunctionStopped(EventEnvelope):
    """A worker/container was evicted gracefully -- the idle-TTL/
    max-invocations sweep, or an explicit dismissal. Never fires for a crash
    (see FunctionCrashed) -- this is the routine/graceful teardown family."""

    function_id: str
    version: int
    # runner_budget = evicted (idle) to make room for another function's
    # runner; stale_before_respawn / cancelled = container-runtime cleanup.
    reason: Literal[
        "idle_ttl", "max_invocations", "dismissed", "stale_before_respawn", "cancelled", "runner_budget",
    ]


class FunctionCrashed(EventEnvelope):
    """A worker process died unexpectedly (ungraceful exit), distinct from
    the routine FunctionStopped teardown family. Currently only ever fires
    for the process runtime -- container mid-run crash/OOM detection has no
    monitoring mechanism yet and is explicitly out of scope for now."""

    function_id: str
    version: int


class FunctionDeleted(EventEnvelope):
    function_id: str
    version: int


class FunctionDeleteFailed(EventEnvelope):
    function_id: str
    version: int
    failure: FailureDetail


class FunctionEndpointDetached(EventEnvelope):
    """One endpoint (this event's inherited ``endpoint_id``) no longer holds
    this function version -- e.g. PurgeEndpointUseCase purging a node that
    was one of several replicas. Distinct from FunctionDeleted: the function
    itself is still alive, just with one fewer entry in its endpoint_id
    list. Only ever appended directly by axo_vem (mirrors FunctionDeleted's
    own PurgeEndpointUseCase-authored path) -- axo_endpoint never emits this
    since it has no notion of "detach," only replicate/tombstone."""

    function_id: str
    version: int


# ── Job pipeline ─────────────────────────────────────────────────────────────
JOB_QUEUED = "JobQueued"
JOB_STARTED = "JobStarted"
JOB_COMPLETED = "JobCompleted"
JOB_FAILED = "JobFailed"


class JobQueued(EventEnvelope):
    """A job was accepted for execution (existing JOB_SUBMITTED bus event,
    renamed for past-tense consistency). ``version`` matches every other
    event carrying a function_id (FunctionRegistered, FunctionDeployed, ...)
    -- previously named ``function_version`` here and on JobStarted, the one
    inconsistency in an otherwise uniform "version" naming across this
    taxonomy."""

    job_id: str
    function_id: str
    version: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


class JobStarted(EventEnvelope):
    """A worker/container actually picked the job up and began executing it
    -- distinct from JobQueued (accepted) since a job may sit behind a
    cold-starting worker for a while first. version/params mirror
    JobQueued's -- carried again here (not just there) because the version
    that actually picked up the job is the one that matters for correctness
    (see ExternalEventForwardingBridge, which trusts this field instead of
    re-deriving "latest registered version" from the function registry)."""

    job_id: str
    function_id: str
    version: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


class JobCompleted(EventEnvelope):
    job_id: str
    function_id: str
    duration_ms: Optional[float] = None


class JobFailed(EventEnvelope):
    job_id: str
    function_id: str
    failure: FailureDetail
    duration_ms: Optional[float] = None


# ── Job attempt usage (metering / billing) ──────────────────────────────────
# One record per attempt, delivered reliably (axo_shared.messaging) up to the
# Control Plane, where it is the permanent billing record. event_id doubles as
# the reliable-channel message_id.
JOB_ATTEMPT_RECORDED = "JobAttemptRecorded"

JobAttemptOutcome = Literal["completed", "failed_user", "failed_platform", "cancelled", "timed_out_in_queue"]
BILLABLE_OUTCOMES = ("completed", "failed_user", "cancelled")


def is_billable(outcome: str) -> bool:
    """Every attempt that ran is billed -- completed, failed by user code, or
    cancelled (for the time it ran). Platform failures and jobs that timed
    out in a queue (never ran) are not."""
    return outcome in BILLABLE_OUTCOMES


class JobAttemptRecorded(EventEnvelope):
    job_id: str
    attempt: int
    trace_id: str
    outcome: JobAttemptOutcome
    # Who owns the VE (pays) and who started the job.
    owner_type: Literal["user", "team"]
    owner_id: str
    started_by_type: Literal["user", "api_key", "choreography", "policy"]
    started_by_id: str
    function_id: str
    function_version: int
    # Size of the endpoint the attempt ran on (billing: execution time x size).
    endpoint_cores: float
    endpoint_ram_mb: int
    received_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Wait per layer, e.g. {"control_plane": 5, "vem": 3, "endpoint": 120, "runner": 40}.
    layer_waits_ms: Dict[str, float] = Field(default_factory=dict)
    execution_ms: float = 0.0
    # Per bucket name.
    bytes_read: Dict[str, int] = Field(default_factory=dict)
    bytes_written: Dict[str, int] = Field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def billable(self) -> bool:
        return is_billable(self.outcome)


# ── Data & buckets ───────────────────────────────────────────────────────────
DATA_BUCKET_CREATED = "DataBucketCreated"
DATA_REGISTERED = "DataRegistered"
DATA_UPLOAD_COMPLETED = "DataUploadCompleted"
DATA_DELETED = "DataDeleted"


class DataBucketCreated(EventEnvelope):
    """A named, quota-enforced namespace was registered on one node --
    mirrors FunctionRegistered's shape for the "declare metadata ahead of
    use" pattern, just for buckets instead of functions."""

    name: str
    quota_bytes: int


class DataRegistered(EventEnvelope):
    """A new object version was uploaded to a bucket (axo_storage). ``name``
    is "{bucket}/{key}" -- every object lives in a bucket."""

    name: str
    version: int
    format: str
    kind: str
    total_size: int
    total_chunks: int


class DataUploadCompleted(EventEnvelope):
    """Every chunk of a previously-DataRegistered record is now present on
    the reporting node -- the read-model signal that flips a DataItem from
    "pending" to "ready". Deliberately thin (just the same name/version key
    DataRegistered used) since nothing consuming this needs more."""

    name: str
    version: int


class DataDeleted(EventEnvelope):
    """An object version was removed from its bucket (OBJECT_DELETE) --
    drives removing the DataItem read-model row. Deliberately thin, same
    reasoning as DataUploadCompleted."""

    name: str
    version: int


# ── User profile ─────────────────────────────────────────────────────────────
# UserProfile events never travel through the ZMQ envelope/wire path -- they
# originate inside axo_vem's own process (an HTTP request handler),
# not on a remote axo_endpoint node, so they're appended to Kurrent directly.
USER_PROFILE_CREATED = "UserProfileCreated"
USER_PROFILE_CREATION_FAILED = "UserProfileCreationFailed"
USER_PROFILE_UPDATED = "UserProfileUpdated"
USER_PROFILE_DELETED = "UserProfileDeleted"
# Username/email live in Xolo (external auth), not this domain's storage --
# these two are defined for taxonomy completeness only, never published,
# until Xolo integration threads the underlying data through.
USER_PROFILE_USERNAME_UPDATED = "UserProfileUsernameUpdated"
USER_PROFILE_EMAIL_UPDATED = "UserProfileEmailUpdated"


class Preferences(BaseModel):
    """UI-facing user settings, grouped separately from UserProfile's
    identity-ish fields so future settings of this kind have one obvious
    place to go instead of accumulating as more top-level fields."""

    model_config = {"frozen": True}

    color: Optional[str] = None
    view_mode: str = "list"
    language: str = "en"
    # Default time window (in minutes) the activity/events feed shows for
    # this user -- purely a display default, bounded by whatever
    # AXO_VEM_ACTIVITY_RETENTION_HOURS still actually retains
    # server-side (see infrastructure/api/controllers/history.py and the
    # activity retention worker on the axo_vem side).
    activity_window_minutes: int = 60
    # Minutes an endpoint must have been `unreachable` past which axo-ui
    # surfaces the purge action prominently for this viewer. Purely a
    # UI-surfacing default -- never enforced as a second backend purge gate
    # (see infrastructure/transport/api/controllers/endpoints.py's
    # purge_endpoint route, axo_vem side).
    endpoint_purge_eligible_after_minutes: int = 60


class UserProfileCreated(EventEnvelope):
    profile_photo: str
    preferences: Preferences


class UserProfileCreationFailed(EventEnvelope):
    """Published when Xolo signup succeeded (an external identity now
    exists) but persisting the corresponding local UserProfile failed --
    an audit trail for identities that exist upstream without a local
    profile yet, not a taxonomy-completeness placeholder like
    UserProfileUsernameUpdated/UserProfileEmailUpdated above."""

    failure: FailureDetail


class UserProfileUpdated(EventEnvelope):
    profile_photo: str
    preferences: Preferences


class UserProfileDeleted(EventEnvelope):
    """user_id comes from EventEnvelope."""


class UserProfileUsernameUpdated(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""

    new_username: str


class UserProfileEmailUpdated(EventEnvelope):
    """Defined for taxonomy completeness -- never published (see module note)."""

    new_email: str


# ── Virtual environment / namespace assignment ──────────────────────────────
VIRTUAL_ENV_CREATED = "VirtualEnvironmentCreated"
VIRTUAL_ENV_UPDATED = "VirtualEnvironmentUpdated"
VIRTUAL_ENV_DELETED = "VirtualEnvironmentDeleted"
# An endpoint being (re)assigned to, or removed from, a VirtualEnvironment --
# always self-reported by the endpoint itself after VIRTUAL_ENV_ASSIGN
# succeeds or clears its assignment.
ENDPOINT_VIRTUAL_ENV_ASSIGNED = "EndpointVirtualEnvironmentAssigned"
ENDPOINT_VIRTUAL_ENV_DETACHED = "EndpointVirtualEnvironmentDetached"
# Audit-only: appended by axo_vem's projector itself (not forwarded by any
# endpoint) whenever a LeaderElected/ConsensusViewChanged resolves to a new
# mesh leader belonging to this VE. The read-model leader_endpoint_id field is
# updated immediately by that same projector step -- this event never gets
# waited on, it just gives that change a permanent, replayable record.
VIRTUAL_ENV_LEADER_CHANGED = "VirtualEnvironmentLeaderChanged"


class ResourceQuota(BaseModel):
    """Declared, not enforced -- bookkeeping only for now."""

    model_config = {"frozen": True}

    cpu: float
    ram: int
    disk: int


class VirtualEnvironmentCreated(EventEnvelope):
    virtual_environment_id: str
    name: str
    owner_user_id: str
    resource_quota: ResourceQuota


class VirtualEnvironmentUpdated(EventEnvelope):
    virtual_environment_id: str
    name: str
    resource_quota: ResourceQuota


class VirtualEnvironmentDeleted(EventEnvelope):
    virtual_environment_id: str


class EndpointVirtualEnvironmentAssigned(EventEnvelope):
    """endpoint_id + virtual_environment_id are both already on EventEnvelope."""


class EndpointVirtualEnvironmentDetached(EventEnvelope):
    previous_virtual_environment_id: str


class VirtualEnvironmentLeaderChanged(EventEnvelope):
    virtual_environment_id: str
    leader_endpoint_id: str


# ── Choreography (function/bucket workflow graphs) ──────────────────────────
# Persistence-only -- a Choreography's *run* telemetry is pushed live over a
# WS channel, not event-sourced, so there are no run-lifecycle events here.
CHOREOGRAPHY_CREATED = "ChoreographyCreated"
CHOREOGRAPHY_UPDATED = "ChoreographyUpdated"
CHOREOGRAPHY_DELETED = "ChoreographyDeleted"


class ChoreographyNode(BaseModel):
    """One node on the design canvas: a registered function invocation or a
    bucket reference. kind-specific fields are optional and only meaningful
    for the matching kind (same "one shape, optional extra fields" approach
    IORef uses for its chunk-aware ops)."""

    model_config = {"frozen": True}

    node_id: str
    kind: Literal["function", "bucket"]
    position: Dict[str, float]  # {"x": ..., "y": ...} -- vue-flow layout only, not semantic
    # kind == "function"
    function_id: Optional[str] = None
    function_version: Optional[int] = None
    # Display-only, captured at design time -- not looked up again, so it can
    # go stale if the function is later renamed. function_id stays the key.
    function_name: Optional[str] = None
    max_retries: int = 3
    retry_policy: Literal["constant", "exponential_backoff", "jitter"] = "constant"
    # kind == "bucket"
    bucket_name: Optional[str] = None
    # Items chosen at design time to feed a bucket_to_fn edge, e.g.
    # [{"name": "...", "version": 1}, ...]; empty/omitted means "all items".
    selected_items: Optional[List[Dict[str, Any]]] = None


class ChoreographyEdge(BaseModel):
    """One connection between two nodes. kind is stored explicitly (derived
    once at design time from the pair of node kinds it joins) so the
    orchestrator never has to re-derive it by joining back to the node list."""

    model_config = {"frozen": True}

    edge_id: str
    source_node_id: str
    target_node_id: str
    kind: Literal["fn_to_fn", "fn_to_bucket", "bucket_to_fn"]
    # kind == "fn_to_fn": which of target's declared params receives source's whole result.
    target_param: Optional[str] = None
    # kind == "bucket_to_fn": how many bucket items to process concurrently,
    # hard-capped at the target function's max_concurrency at validation/run time.
    parallelism: int = 1


class ChoreographyGraph(BaseModel):
    model_config = {"frozen": True}

    nodes: List[ChoreographyNode] = Field(default_factory=list)
    edges: List[ChoreographyEdge] = Field(default_factory=list)


class ChoreographyCreated(EventEnvelope):
    choreography_id: str
    name: str
    owner_user_id: str
    graph: ChoreographyGraph


class ChoreographyUpdated(EventEnvelope):
    choreography_id: str
    name: str
    graph: ChoreographyGraph


class ChoreographyDeleted(EventEnvelope):
    choreography_id: str


# ── VE settings (inside one VE's own VEM) ───────────────────────────────────
VE_SETTINGS_CHANGED = "VirtualEnvironmentSettingsChanged"


class VirtualEnvironmentSettingsChanged(EventEnvelope):
    """One change to a VE's settings (target/min endpoints, auto-replace,
    idle and wake times, default max_wait, queue limits, retention). The VEM
    rebuilds its current settings by replaying these, so every value has a
    who/when/why history. ``changes`` holds only the fields that changed."""

    changes: Dict[str, Any]
    changed_by_type: Literal["user", "policy", "system"] = "user"
    changed_by_id: Optional[str] = None
    reason: Optional[str] = None


VE_LIFECYCLE_CHANGED = "VirtualEnvironmentLifecycleChanged"


class VirtualEnvironmentLifecycleChanged(EventEnvelope):
    """What the whole VE is doing changed (Running, Stopping, Stopped,
    Starting, Restoring, Deleting, ...), recorded by its VEM so a restarted
    VEM knows where it left off."""

    lifecycle: VeLifecycle
    reason: Optional[str] = None


# ── Scaling policies (inside one VE's own VEM) ──────────────────────────────
POLICY_SAVED = "ScalingPolicySaved"
POLICY_DELETED = "ScalingPolicyDeleted"
POLICIES_PAUSED = "ScalingPoliciesPaused"
POLICY_DECISION_RECORDED = "ScalingPolicyDecisionRecorded"


class ScalingPolicySaved(EventEnvelope):
    """A policy was created or changed (the whole policy, as a dict)."""

    policy: Dict[str, Any]


class ScalingPolicyDeleted(EventEnvelope):
    policy_id: str


class ScalingPoliciesPaused(EventEnvelope):
    """Policies won't act until ``until`` (epoch seconds) -- e.g. after a
    manual change, so a person's choice isn't immediately undone."""

    until: float
    reason: Optional[str] = None


class ScalingPolicyDecisionRecorded(EventEnvelope):
    """What a policy did (or, in dry run, would have done) and why, e.g.
    "fast-responses added 1 endpoint: p95 job.total_ms = 820 > 500 for 120 s"."""

    policy_id: str
    policy_name: str
    kind: Literal["endpoint", "runner"]
    function_id: Optional[str] = None
    function_version: Optional[int] = None
    before: int
    after: int
    dry_run: bool = False
    reason: str


ENDPOINT_LIFECYCLE_CHANGED = "EndpointLifecycleChanged"

EndpointLifecycleState = Literal[
    "creating", "starting", "running", "sleeping", "draining", "removing", "removed", "stopped", "failed",
]


class EndpointLifecycleChanged(EventEnvelope):
    """One endpoint the VEM manages moved to a new state (written by the VEM,
    not the endpoint). The VEM rebuilds its fleet by replaying these, and
    every move has a reason, e.g. "idle 20 min" or "replacing ep-3"."""

    state: EndpointLifecycleState
    cpu_cores: float
    memory_mb: int
    failures: int = 0          # consecutive failures of this slot (auto-replace gives up after a few)
    replaces: Optional[str] = None
    reason: Optional[str] = None


# ── VE status + metric summaries (VEM -> Control Plane, reliable) ───────────
VE_HEARTBEAT = "VirtualEnvironmentHeartbeat"
METRIC_SUMMARY_REPORTED = "MetricSummaryReported"


class VirtualEnvironmentHeartbeat(EventEnvelope):
    """Sent by a VE's VEM every few seconds. Endpoint counts follow the health
    rules: starting/leaving are neutral, only ``failed`` counts against health."""

    lifecycle: VeLifecycle
    health: Optional[VeHealth] = None  # None while Stopped / Sleeping
    target_endpoints: int
    min_endpoints: int
    running: int = 0
    starting: int = 0
    leaving: int = 0
    failed: int = 0
    sleeping: int = 0
    # When the VE last had nothing to do (None = busy now) -- the Control
    # Plane puts a Free-plan VE to sleep after 30 idle minutes.
    idle_since: Optional[datetime] = None
    # Messages the VEM still has to deliver to the CP (its outbox). Deleting
    # a VE waits for 0 so no billing record is lost.
    outbox_pending: int = 0
    vem_version: Optional[str] = None
    endpoint_version: Optional[str] = None


class MetricSummary(BaseModel):
    """One metric for one dims combination: axo_shared.metrics.MetricRecord's
    metric/dims plus StatSummary.to_dict() (count, sums, min/max, sketch)."""

    model_config = {"frozen": True}

    metric: str
    dims: Dict[str, str] = Field(default_factory=dict)
    stats: Dict[str, Any]


class MetricSummaryReported(EventEnvelope):
    """Every metric of one component for one finished time bin. Queue metrics
    travel here as ordinary metrics -- there is no separate queue event."""

    component_kind: Literal["control_plane", "vem", "endpoint", "runner"]
    component_id: str
    bin_start: float
    bin_width_s: int
    summaries: List[MetricSummary] = Field(default_factory=list)
