from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Step statuses.
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"          # didn't run: an input it needed never arrived
CANCELLED = "cancelled"
# A step: some of its files failed, the rest go on. A run: something failed,
# at least one last step still finished.
COMPLETED_WITH_ERRORS = "completed_with_errors"
# No followed arrow reaches it (a condition sent the run another way). Not an error.
NOT_TAKEN = "not_taken"
STEP_DONE = frozenset({COMPLETED, COMPLETED_WITH_ERRORS, FAILED, SKIPPED, CANCELLED, NOT_TAKEN})
# What the steps after it may use.
SUCCEEDED = frozenset({COMPLETED, COMPLETED_WITH_ERRORS})

# Run statuses (the step ones above but pending / skipped are shared).
QUEUED = "queued"            # waiting for a free run slot
ACTIVE_RUN_STATUSES = frozenset({QUEUED, RUNNING})
# Choreographies calling choreographies: at most this many levels (the outer one included).
MAX_CALL_DEPTH = 5
RUN_DONE = frozenset({COMPLETED, COMPLETED_WITH_ERRORS, FAILED, CANCELLED})


@dataclass
class ItemState:
    """One file of a step that runs once per file coming from a bucket."""

    index: int
    file: Dict[str, Any]
    status: str = PENDING
    attempt: int = 0
    job_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    output_bytes: int = 0                    # its answer's size; the answer = its job's result
    error: Optional[str] = None
    error_name: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    duration_ms: Optional[float] = None
    child: Optional[Dict[str, Any]] = None   # a choreography step: its run (see StepState)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ItemState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StepState:
    """Everything one step did in a run: what went in (params), its job(s)
    -- whose results are its answer, kept with the job, not here -- and
    times: enough to resume the run and to draw it.

    ``files``: for a bucket step, what it holds for the steps after it (the
    files just written, or the ones picked / the one that triggered the
    run). ``items``: a process step after a bucket step runs once per file.

    ``child`` (a choreography step: a call): the run it started --
    {run_id, choreography_id, choreography_name, version, status, done,
    total, position (its place in line while it waits), answer_job_id}."""

    node_id: str
    kind: str
    status: str = PENDING
    attempt: int = 0
    target_version: Optional[int] = None     # the function / choreography version it ran
    job_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    # parameter -> where its value came from: a previous step, a list of them
    # (collect / reduce), "data" (the bucket file) or "step" (set on the step)
    param_origins: Dict[str, Any] = field(default_factory=dict)
    output_bytes: int = 0
    files: List[Dict[str, Any]] = field(default_factory=list)
    items: List[ItemState] = field(default_factory=list)
    error: Optional[str] = None
    error_name: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[float] = None
    child: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StepState":
        data = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        data["items"] = [ItemState.from_dict(i) for i in d.get("items") or []]
        return cls(**data)


@dataclass
class ChoreographyRun:
    """One run of one choreography version, kept as a single record the
    engine updates as steps move on (not event-sourced: only its start and
    end are events). ``acting_user_id`` = who the run's jobs and bucket
    reads act for (the starter, or the trigger's creator; a called run: the
    calling run's).

    ``parent``: a run started by a choreography step of another run
    ({run_id, choreography_id, choreography_name, step, item});
    ``chain``: the choreographies above it, outermost first (no loops, at
    most MAX_CALL_DEPTH levels). ``versions``: step -> the function /
    choreography version it runs, fixed when the run is queued (a resume
    keeps them)."""

    run_id: str
    choreography_id: str
    choreography_name: str
    version: int
    status: str = QUEUED
    acting_user_id: Optional[str] = None
    started_by_type: str = "user"            # user | trigger | choreography
    started_by_id: Optional[str] = None
    started_by_name: Optional[str] = None      # shown in the runs list
    trigger: Optional[Dict[str, Any]] = None  # what started it: {kind, bucket, name, version, trigger_id}
    input: Any = None                         # the event given when started by hand
    # starting bucket step -> files picked for this run ([{name, version?}] or "all")
    files: Dict[str, Any] = field(default_factory=dict)
    steps: Dict[str, StepState] = field(default_factory=dict)
    # the last steps: {node: {"job_id"} | {"job_ids"} | {"files"}} (run_result reads the answers)
    result: Dict[str, Any] = field(default_factory=dict)
    resumes: int = 0
    # the step whose error stopped a run that doesn't allow completing with errors
    stopped_by: Optional[str] = None
    # arrow (edge_id) -> taken / not_taken, set when its source finished well
    edge_states: Dict[str, str] = field(default_factory=dict)
    parent: Optional[Dict[str, Any]] = None
    chain: List[str] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)   # the calling runs: {choreography_id, run_id, step, version}
    versions: Dict[str, int] = field(default_factory=dict)
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["steps"] = {k: s.to_dict() for k, s in self.steps.items()}
        return data

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChoreographyRun":
        data = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        data["steps"] = {k: StepState.from_dict(s) for k, s in (d.get("steps") or {}).items()}
        return cls(**data)

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in (PENDING, RUNNING, COMPLETED, COMPLETED_WITH_ERRORS, FAILED, SKIPPED, CANCELLED,
                              NOT_TAKEN)}
        for step in self.steps.values():
            out[step.status] = out.get(step.status, 0) + 1
        return out
