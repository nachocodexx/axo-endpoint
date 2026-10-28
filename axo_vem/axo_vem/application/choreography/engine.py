"""Runs choreographies.

A step starts as soon as every step before it is done (no waves). A
process step is one job (one per file after a bucket step): its attempts
are the job's own, tried again by the endpoint (``max_attempts``), and its
answer IS the job's result, kept in the VE's results bucket -- the run
keeps only which job ran each step. The job id is fixed by (run, step,
file, resume), so after a VEM restart the run carries on from its record:
a step that was running is polled again on the endpoint it went to
(re-sent if that endpoint forgot it, which runs it only once). Runs of one choreography go at most
``settings.max_parallel_runs`` at a time; the rest wait in line, up to
``settings.queue_limit``.

Every change to a run is saved to its record and sent on the WebSocket
topics ``choreographies`` (run summaries) and ``choreographies:<run_id>``
(each step, for the canvas animation). Starting and ending a run are
events (the history); the run's numbers feed the same statistics as the
rest of Axo (``choreography.*`` metrics, rolled up to the Control Plane).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, cast

from axo_shared.metrics import MetricAggregator
from axo_shared.metrics import names as m
from axo_shared.runtime.spec import PLATFORM_MAX_ATTEMPTS, retry_delay

from axo_vem.application.choreography.jobs import COMPLETED as JOB_COMPLETED
from axo_vem.application.choreography.jobs import PENDING as JOB_PENDING
from axo_vem.application.choreography.jobs import UNKNOWN as JOB_UNKNOWN
from axo_vem.application.choreography.jobs import JobStatus, StepJobs
from axo_vem.application.choreography.service import ChoreographyService, StepCatalog
from axo_vem.domain.choreography.choreography import Choreography
from axo_vem.domain.choreography.graph_ops import (
    DEFAULT_KEY_PATTERN,
    is_process,
    predecessors,
    render_key,
    sinks,
    successors,
)
from axo_vem.domain.choreography.inputs import (
    DATA_PARAM,
    EVENT_OUTPUT_MAX_BYTES,
    StepInputError,
    build_event,
    json_size,
    resolve_params,
    step_summary,
)
from axo_vem.domain.choreography.conditions import TAKEN, edge_states
from axo_vem.domain.choreography.planning import (
    DELIVERED,
    arrow_state,
    final_status,
    is_finished,
    next_steps,
    to_resume,
)
from axo_vem.domain.choreography.repository import ChoreographyRepository
from axo_vem.domain.choreography.triggers import trigger_matches
from axo_vem.domain.choreography.run import (
    ACTIVE_RUN_STATUSES,
    CANCELLED,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    MAX_CALL_DEPTH,
    NOT_TAKEN,
    PENDING,
    QUEUED,
    RUN_DONE,
    RUNNING,
    SKIPPED,
    STEP_DONE,
    SUCCEEDED,
    ChoreographyRun,
    ItemState,
    StepState,
)
from axo_vem.domain.choreography.run_repository import ChoreographyRunRepository
from axo_vem.domain.errors import (
    ConflictError, DomainError, InvalidRequestError, NotFoundError, TooManyRequestsError,
)
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher

RUNS_TOPIC = "choreographies"

# Errors a retry can't fix: the step fails at once.
_FINAL_ERRORS = frozenset({
    "CHOREOGRAPHY_STEP_PARAM_MISSING", "CHOREOGRAPHY_STEP_PARAM_AMBIGUOUS", "CHOREOGRAPHY_STEP_PARAM_MERGE_FAILED",
    "INVALID_FIELD",
    "ACTIVE_OBJECT_STATE_TOO_BIG", "ACTIVE_OBJECT_VALUE_UNSAVEABLE",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _final(error_name: Optional[str]) -> bool:
    return error_name is not None and (error_name in _FINAL_ERRORS or error_name.endswith("_NOT_FOUND"))


def job_id_for(run_id: str, node_id: str, item: Optional[int], resumes: int) -> str:
    """The same step (file, resume) always gets the same job id (so it runs once)."""
    key = f"{run_id}:{node_id}:{'' if item is None else item}:{resumes}"
    return "ch-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def call_run_id(run_id: str, node_id: str, item: Optional[int], generation: int = 0) -> str:
    """The run a choreography step (file) calls: the same one again after a
    restart or a resume (resumed, not started over); ``generation`` goes up
    only when the step is called again with other inputs."""
    key = f"{run_id}:{node_id}:{'' if item is None else item}:{generation}"
    return "sub-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


# A bucket step (written by the VEM itself) tries this many times in all.
BUCKET_STEP_ATTEMPTS = 3


def trigger_run_id(trigger_id: str, bucket: str, name: str, version: Any) -> str:
    """One run per trigger and file version, however often it's reported."""
    key = f"{trigger_id}:{bucket}:{name}:{version}"
    return "trg-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def file_written_event(bucket: str, name: str, version: Any, *, format: Optional[str] = None,
                       source: Optional[str] = None) -> Dict[str, Any]:
    """A new file as an event; its id is fixed per file version."""
    return {"id": f"file:{bucket}:{name}:{version}", "type": models.FILE_WRITTEN, "source": f"bucket:{bucket}",
            "subject": name, "time": _now(),
            "data": {"bucket": bucket, "name": name, "version": version, "format": format, "source": source}}


def event_run_id(trigger_id: str, event_id: str) -> str:
    """One run per trigger and event, however often the event is sent."""
    return "evt-" + hashlib.sha256(f"{trigger_id}:{event_id}".encode("utf-8")).hexdigest()[:40]


# Where a first step's parameters came from when the run's input gave them.
INPUT_SOURCE = "input"


SOURCE_PREFIX = "choreography:"


def source_tag(choreography_id: str) -> str:
    """What a choreography's own bucket writes are tagged with in
    axo_storage; the "object written" report brings it back here."""
    return f"{SOURCE_PREFIX}{choreography_id}"


def source_choreography(source: Optional[str]) -> Optional[str]:
    return source[len(SOURCE_PREFIX):] if source and source.startswith(SOURCE_PREFIX) else None


@dataclass
class _Inputs:
    """What reaches a process step from the steps before it (see _gather)."""

    outputs: Dict[str, Any] = field(default_factory=dict)
    finished: Dict[str, Optional[str]] = field(default_factory=dict)
    previous: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    files: Optional[List[Dict[str, Any]]] = None
    failures: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class _Live:
    run: ChoreographyRun
    choreography: Choreography
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancel: threading.Event = field(default_factory=threading.Event)
    jobs: Dict[str, Tuple[str, str]] = field(default_factory=dict)    # job_id -> (endpoint_id, job_id)
    outputs: Dict[str, Any] = field(default_factory=dict)             # job_id -> its answer (while running)
    in_flight: int = 0
    finished: bool = False
    began: float = 0.0

    def __post_init__(self) -> None:
        graph = self.choreography.graph
        self.nodes = {n.node_id: n for n in graph.nodes}
        self.preds = predecessors(graph)
        self.succs = successors(graph)
        self.edges = {(e.source_node_id, e.target_node_id): e for e in graph.edges}
        self.out_edges = {n.node_id: [e for e in graph.edges if e.source_node_id == n.node_id]
                          for n in graph.nodes}

    def delivers(self, source: str, target: str) -> bool:
        """``source``'s arrow to ``target`` is followed: it finished well (or,
        for an "on failure" arrow, it failed)."""
        edge = self.edges[(source, target)]
        return arrow_state(edge, self.run.steps, self.run.edge_states) == DELIVERED

    def handles_failure(self, node_id: str) -> bool:
        """It has an "on failure" arrow: its failures are handled."""
        return any(e.on_failure for e in self.out_edges[node_id])


class ChoreographyEngine:
    def __init__(
        self,
        service: ChoreographyService,
        repository: ChoreographyRepository,
        runs: ChoreographyRunRepository,
        catalog: StepCatalog,
        jobs: StepJobs,
        publisher: EventPublisher,
        *,
        bucket_storage: Any = None,
        results: Any = None,          # JobsService: the jobs' kept results (result / result_ref / kept)
        broadcaster: Any = None,
        metrics: Optional[MetricAggregator] = None,
        virtual_environment_id: Optional[str] = None,
        step_timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 0.5,
        workers: int = 32,
        clock: Callable[[], float] = time.time,
        logger: Any = None,
    ) -> None:
        self._service = service
        self._repository = repository
        self._runs = runs
        self._catalog = catalog
        self._jobs = jobs
        self._publisher = publisher
        self._buckets = bucket_storage
        self._results = results
        self._broadcaster = broadcaster
        self._metrics = metrics
        self._ve = virtual_environment_id
        self._step_timeout = step_timeout_seconds
        self._poll = poll_interval_seconds
        self._clock = clock
        self._logger = logger
        self._pool = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="choreography")
        self._lock = threading.RLock()
        self._live: Dict[str, _Live] = {}
        # Set by shutdown(): nothing more is written, so runs stay as they
        # were for the next start's recover().
        self._halted = threading.Event()

    # ── starting ─────────────────────────────────────────────────────────────
    def start(self, ref: str, *, user_id: Optional[str], version: Optional[int] = None, input: Any = None,
              run_id: Optional[str] = None, started_by_type: str = "user", started_by_id: Optional[str] = None,
              trigger: Optional[Dict[str, Any]] = None,
              files: Optional[Dict[str, Any]] = None, started_by_name: Optional[str] = None,
              parent: Optional[Dict[str, Any]] = None, chain: Optional[List[str]] = None,
              calls: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Queues a run of ``version`` (the newest when None) and starts it
        if there's a free slot. The same ``run_id`` twice = one run.
        ``files``: starting bucket step -> the files this run reads
        ([{name, version?}], or "all") instead of the ones picked on it.
        ``parent`` / ``chain``: a run a choreography step calls (see
        ChoreographyRun). The version every function / choreography step
        runs is fixed now."""
        if run_id is not None:
            existing = self._runs.get(run_id)
            if existing is not None:
                return self._existing(existing, ref)
        choreography = self._service.get_version(ref, version)
        chain = list(chain or [])
        if choreography.choreography_id in chain:
            raise InvalidRequestError(f"{choreography.name} would call itself (through {len(chain)} level(s))",
                                      error_name="CHOREOGRAPHY_LOOP")
        if len(chain) + 1 > MAX_CALL_DEPTH:
            raise InvalidRequestError(f"choreographies calling choreographies go at most {MAX_CALL_DEPTH} levels deep",
                                      error_name="CHOREOGRAPHY_TOO_DEEP")
        latest = self._service.get(ref)
        if self._runs.count(latest.choreography_id, QUEUED) >= latest.settings.queue_limit:
            raise TooManyRequestsError(
                f"{latest.name} already has {latest.settings.queue_limit} runs waiting",
                error_name="CHOREOGRAPHY_QUEUE_FULL")
        starts = {n.node_id for n in choreography.graph.nodes
                  if n.kind == "bucket" and not predecessors(choreography.graph)[n.node_id]}
        for node_id, picked in (files or {}).items():
            if node_id not in starts:
                raise InvalidRequestError(f"{node_id} isn't a bucket step that starts the run",
                                          error_name="CHOREOGRAPHY_INVALID")
            if picked != "all" and (not isinstance(picked, list) or not picked
                                    or not all(isinstance(f, dict) and f.get("name") for f in picked)):
                raise InvalidRequestError(f"the files for {node_id} are a list of {{name, version}} or \"all\"",
                                          error_name="CHOREOGRAPHY_INVALID")
        if json_size(input) > 1024 * 1024:
            raise ConflictError("A run's input must be at most 1 MB; pass bigger data through a bucket",
                                error_name="VEM_INVALID")
        run = ChoreographyRun(
            run_id=run_id or str(uuid.uuid4()), choreography_id=choreography.choreography_id,
            choreography_name=choreography.name, version=choreography.version, status=QUEUED,
            acting_user_id=user_id, started_by_type=started_by_type, started_by_id=started_by_id or user_id,
            started_by_name=started_by_name, trigger=trigger, input=input, files=dict(files or {}),
            steps={n.node_id: StepState(node_id=n.node_id, kind=n.kind) for n in choreography.graph.nodes},
            parent=parent, chain=chain, calls=list(calls or []), versions=self._fix_versions(choreography),
            queued_at=_now(),
        )
        if not self._runs.insert(run):
            return self._existing(self._runs.get(run.run_id), ref)
        self._broadcast_run(run)
        self._admit(run.choreography_id)
        return self.run_info(run.run_id)

    def _fix_versions(self, choreography: Choreography) -> Dict[str, int]:
        """Step -> the version of the function / choreography it runs: its
        pinned one, else the newest (a function: its default) right now."""
        out: Dict[str, int] = {}
        for node in choreography.graph.nodes:
            if node.kind == "function" and node.function_id:
                function = self._catalog.function(node.function_id, node.function_version)
                if function is not None:
                    out[node.node_id] = function.version
            elif node.kind == "choreography":
                inner = self._catalog.choreography(node.choreography_id, node.choreography_version)
                if inner is not None:
                    out[node.node_id] = inner.version
        return out

    def _existing(self, run: Optional[ChoreographyRun], ref: str) -> Dict[str, Any]:
        choreography = self._service.get(ref)
        if run is None or run.choreography_id != choreography.choreography_id:
            raise ConflictError("That run id is already used by another choreography", error_name="VEM_CONFLICT")
        return run.to_dict()

    def _admit(self, choreography_id: str) -> None:
        """Starts waiting runs while there are free slots."""
        to_begin: List[ChoreographyRun] = []
        with self._lock:
            choreography = self._repository.get(choreography_id)
            if choreography is None:
                return
            running = sum(1 for live in self._live.values() if live.run.choreography_id == choreography_id)
            free = choreography.settings.max_parallel_runs - running
            if free <= 0:
                return
            waiting = list(reversed(self._runs.list(choreography_id, status=QUEUED, limit=1000)))
            for run in waiting[:free]:
                if run.run_id in self._live:
                    continue
                version = (choreography if run.version == choreography.version
                           else self._repository.get_version(choreography_id, run.version))
                if version is None:
                    self._fail_queued(run, f"version {run.version} is gone")
                    continue
                self._live[run.run_id] = _Live(run=run, choreography=version, began=self._clock())
                to_begin.append(run)
        for run in to_begin:
            self._begin(self._live[run.run_id])

    def _begin(self, live: _Live) -> None:
        run = live.run
        with live.lock:
            run.status = RUNNING
            run.started_at = _now()
            self._save(run)
        self._broadcast_run(run)
        self._observe(m.CHOREOGRAPHY_QUEUE_MS, _ms_between(run.queued_at, run.started_at), live)
        self._append(run, models.CHOREOGRAPHY_RUN_STARTED, models.ChoreographyRunStarted(
            choreography_id=run.choreography_id, choreography_name=run.choreography_name, run_id=run.run_id,
            version=run.version,
            started_by_type=run.started_by_type if run.started_by_type in ("trigger", "choreography") else "user",
            started_by_id=run.started_by_id, resumed=run.resumes > 0, user_id=run.acting_user_id,
            virtual_environment_id=self._ve, parent_run_id=(run.parent or {}).get("run_id"),
            parent_step=(run.parent or {}).get("step"),
        ))
        self._advance(live)

    def _fail_queued(self, run: ChoreographyRun, reason: str) -> None:
        run.status = FAILED
        run.finished_at = _now()
        for step in run.steps.values():
            if step.status == PENDING:
                step.status, step.error = CANCELLED, reason
        self._save(run)
        self._broadcast_run(run)

    # ── moving a run on ──────────────────────────────────────────────────────
    def _advance(self, live: _Live) -> None:
        graph = live.choreography.graph
        finish = False
        with live.lock:
            if live.finished:
                return
            steps = live.run.steps
            if live.cancel.is_set():
                for step in steps.values():
                    if step.status == PENDING:
                        step.status, step.finished_at = CANCELLED, _now()
                        step.error = step.error or _stopped_reason(live)
                        self._save_step(live, step)
            while True:
                plan = next_steps(graph, steps, live.run.edge_states)
                if not plan.skip and not plan.not_taken:
                    break
                for node_id in plan.skip:
                    step = steps[node_id]
                    step.status, step.finished_at = SKIPPED, _now()
                    step.error = "an input it needed didn't arrive (an earlier step failed or was skipped)"
                    self._save_step(live, step)
                    self._observe_step(live, step)
                for node_id in plan.not_taken:
                    step = steps[node_id]
                    step.status, step.finished_at = NOT_TAKEN, _now()
                    self._save_step(live, step)
                    self._observe_step(live, step)
            if self._halted.is_set():
                return
            if not live.cancel.is_set():
                for node_id in plan.start:
                    step = steps[node_id]
                    step.status, step.started_at = RUNNING, step.started_at or _now()
                    live.in_flight += 1
                    self._save_step(live, step)
                    if live.nodes[node_id].kind == "choreography":
                        # it only waits for the run it calls (whose steps need the pool): its own thread
                        threading.Thread(target=self._run_step, args=(live, node_id), daemon=True,
                                         name="choreography-call").start()
                    else:
                        self._pool.submit(self._run_step, live, node_id)
            if live.in_flight == 0 and is_finished(steps):
                live.finished = finish = True
        if finish:
            self._finish(live)

    def _run_step(self, live: _Live, node_id: str) -> None:
        step = live.run.steps[node_id]
        node = _pinned(live, live.nodes[node_id])
        began = time.monotonic()
        try:
            if node.kind == "bucket":
                self._bucket_step(live, node, step)
            elif node.kind == "choreography":
                self._call_step(live, node, step)
            else:
                self._process_step(live, node, step)
        except Exception as exc:  # a bug or an outside failure: the step fails, the run goes on
            with live.lock:
                step.status = FAILED
                step.error = step.error or f"{type(exc).__name__}: {exc}"
                step.error_name = step.error_name or getattr(exc, "error_name", None)
        with live.lock:
            if live.cancel.is_set() and step.status in (RUNNING, PENDING):
                step.status = CANCELLED
            if step.status == CANCELLED and live.run.stopped_by not in (None, node_id):
                step.error = step.error or _stopped_reason(live)
            step.finished_at = _now()
            step.duration_ms = round((time.monotonic() - began) * 1000, 3)
            if step.status in SUCCEEDED:
                self._follow_arrows(live, node_id, step)
            self._record_failure_arrows(live, node_id)
            live.in_flight -= 1
            self._save_step(live, step)
        if step.status == FAILED and not live.handles_failure(node_id):
            self._stop_on_error(live, node_id)
        self._observe_step(live, step)
        self._advance(live)

    def _follow_arrows(self, live: _Live, node_id: str, step: StepState) -> None:
        """Which arrows out of a step that finished well are followed (its
        conditions on its output); saved with the step, so a restart agrees."""
        edges = live.out_edges[node_id]
        if not any(e.condition is not None or e.otherwise for e in edges):
            states: Dict[str, str] = {e.edge_id: TAKEN for e in edges}
            warnings: List[str] = []
        else:
            states, warnings = edge_states(edges, self._step_output(live, step))
        live.run.edge_states.update(states)
        step.warnings = list(step.warnings) + [f"condition: {w}" for w in warnings]

    @staticmethod
    def _record_failure_arrows(live: _Live, node_id: str) -> None:
        """Whether each "on failure" arrow out of a finished step was followed
        (for the run's drawing; the planner decides from the step's status)."""
        for edge in live.out_edges[node_id]:
            if edge.on_failure:
                state = arrow_state(edge, live.run.steps, live.run.edge_states)
                live.run.edge_states[edge.edge_id] = TAKEN if state == DELIVERED else NOT_TAKEN

    def _stop_on_error(self, live: _Live, node_id: str) -> None:
        """A run that doesn't allow completing with errors stops at its first
        error: nothing new starts and what's running is cancelled."""
        if live.choreography.settings.allow_completed_with_errors:
            return
        with live.lock:
            if live.cancel.is_set():
                return
            live.run.stopped_by = node_id
            live.cancel.set()
            jobs = list(live.jobs.values())
        for endpoint_id, job_id in jobs:
            if endpoint_id:
                self._jobs.cancel(endpoint_id, job_id)

    def _finish(self, live: _Live) -> None:
        if self._halted.is_set():
            return
        run = live.run
        graph = live.choreography.graph
        with live.lock:
            run.status = final_status(graph, run.steps, live.cancel.is_set(),
                                      allow_errors=live.choreography.settings.allow_completed_with_errors,
                                      stopped=run.stopped_by is not None)
            run.finished_at = _now()
            run.duration_ms = _ms_between(run.started_at, run.finished_at)
            run.result = {}
            for node_id in sinks(graph):
                step = run.steps[node_id]
                if step.status not in SUCCEEDED:
                    continue
                if step.kind == "bucket":
                    run.result[node_id] = {"files": step.files}
                elif step.kind == "choreography":
                    run.result[node_id] = ({"run_ids": [(it.child or {}).get("run_id") for it in step.items
                                                        if it.status == COMPLETED]} if step.items
                                           else {"run_id": (step.child or {}).get("run_id")})
                elif step.items:
                    run.result[node_id] = {"job_ids": [it.job_id for it in step.items if it.status == COMPLETED]}
                else:
                    run.result[node_id] = {"job_id": step.job_id}
            self._save(run)
        with self._lock:
            self._live.pop(run.run_id, None)
        self._broadcast_run(run)
        counts = run.counts()
        dims = self._dims(live)
        self._observe(m.CHOREOGRAPHY_RUNS, 1, live, outcome=run.status)
        if run.duration_ms is not None:
            self._metric(m.CHOREOGRAPHY_RUN_MS, run.duration_ms, {**dims, m.DIM_OUTCOME: run.status})
        self._append(run, models.CHOREOGRAPHY_RUN_FINISHED, models.ChoreographyRunFinished(
            choreography_id=run.choreography_id, choreography_name=run.choreography_name, run_id=run.run_id,
            version=run.version, status=run.status, duration_ms=run.duration_ms,
            steps_completed=counts[COMPLETED], steps_failed=counts[FAILED], steps_skipped=counts[SKIPPED],
            user_id=run.acting_user_id, virtual_environment_id=self._ve,
        ))
        self._admit(run.choreography_id)

    # ── process steps ────────────────────────────────────────────────────────
    def _gather(self, live: _Live, node) -> _Inputs:
        """What reaches a process step from the steps before it: their
        outputs (and when each finished), what its event says about them,
        the files of a bucket step before it and the errors "on failure"
        arrows bring."""
        run = live.run
        got = _Inputs()
        for pred_id in live.preds[node.node_id]:
            pred = run.steps[pred_id]
            if not live.delivers(pred_id, node.node_id):
                continue
            if live.edges[(pred_id, node.node_id)].on_failure:
                # an "on failure" arrow brings the error, not an output
                got.failures[pred_id] = failure_summary(live.nodes[pred_id], pred)
                got.previous[pred_id] = {"kind": pred.kind, "status": pred.status, "params": pred.params,
                                         "duration_ms": pred.duration_ms, "failure": got.failures[pred_id]}
                continue
            if pred.kind == "bucket":
                got.files = list(pred.files)
            else:
                got.outputs[pred_id] = self._step_output(live, pred)
                got.finished[pred_id] = pred.finished_at
            answer_job = _answer_job(pred)
            ref = (self._output_ref(answer_job) if pred.kind != "bucket" and not pred.items
                   and pred.output_bytes > EVENT_OUTPUT_MAX_BYTES else None)
            got.previous[pred_id] = step_summary(pred.kind, pred.params, got.outputs.get(pred_id), ref,
                                                 pred.output_bytes, pred.duration_ms, pred.files, pred.status)
        if not live.preds[node.node_id] and isinstance(run.input, dict):
            # a first step: the run's input (an event's data) fills parameters by name
            got.outputs[INPUT_SOURCE] = run.input
        return got

    def _process_step(self, live: _Live, node, step: StepState) -> None:
        run = live.run
        info = self._catalog.param_info(node)
        if info is None:
            what = (f"function {node.function_name or node.function_id}" if node.kind == "function"
                    else f"object {node.object_name} (method {node.method or 'run'})")
            step.status, step.error = FAILED, f"the {what} doesn't exist any more"
            step.error_name = "FUNCTION_NOT_FOUND" if node.kind == "function" else "ACTIVE_OBJECT_NOT_FOUND"
            return
        if node.kind == "function":
            function = self._catalog.function(node.function_id, node.function_version)
            step.target_version = function.version

        got = self._gather(live, node)
        output_buckets = [live.nodes[s].bucket_name for s in live.succs[node.node_id]
                          if live.nodes[s].kind == "bucket" and live.nodes[s].bucket_name]
        run_info = {"run_id": run.run_id, "choreography": run.choreography_name,
                    "choreography_id": run.choreography_id, "version": run.version,
                    "started_by_type": run.started_by_type, "started_by_id": run.started_by_id,
                    "parent": run.parent}

        def call(holder, item: Optional[int], file: Union[Dict[str, Any], List[Dict[str, Any]], None]) -> None:
            try:
                params, origins = resolve_params(node, info, got.outputs, data=file, finished=got.finished,
                                                 seed=f"{run.run_id}:{node.node_id}")
            except StepInputError as exc:
                holder.status, holder.error, holder.error_name = FAILED, str(exc), exc.error_name
                return
            with live.lock:
                step.param_origins = origins
            event = build_event(run=run_info, step=node.node_id, trigger=run.trigger, input=run.input,
                                previous=got.previous, file=file)
            if got.failures:
                event["failure"] = (next(iter(got.failures.values())) if len(got.failures) == 1
                                    else {"steps": got.failures})
            self._attempts(live, node, holder, params, event, output_buckets, item)

        self._each_file(live, node, step, got.files, call)

    def _each_file(self, live: _Live, node, step: StepState, files: Optional[List[Dict[str, Any]]],
                   call: Callable[[Any, Optional[int], Any], None]) -> None:
        """Calls ``call(holder, item, file)`` once -- or, after a bucket step
        holding several files and with ``files_mode`` per_file, once per file
        (``parallelism`` at once), then sets the step's status from its files:
        the ones that worked go on, a resume runs only the failed ones again."""
        def one(holder, item: Optional[int], file: Any) -> None:
            call(holder, item, file)
            if holder is not step and holder.status == FAILED and not live.handles_failure(node.node_id):
                self._stop_on_error(live, node.node_id)

        if node.files_mode == "all_at_once" and files is not None:
            one(step, None, files)
            return
        if files is None or len(files) == 1:
            one(step, None, files[0] if files else None)
            return

        # A bucket step before it holds several files: one call per file.
        with live.lock:
            if len(step.items) != len(files):
                step.items = [ItemState(index=i, file=f) for i, f in enumerate(files)]
            self._save_step(live, step)
        todo = [it for it in step.items if it.status != COMPLETED]
        with ThreadPoolExecutor(max_workers=max(1, node.parallelism), thread_name_prefix="choreography-item") as pool:
            list(pool.map(lambda it: one(it, it.index, it.file), todo))
        failed = [it for it in step.items if it.status != COMPLETED]
        worked = [it for it in step.items if it.status == COMPLETED]
        step.output_bytes = sum(it.output_bytes for it in worked)
        allow = live.choreography.settings.allow_completed_with_errors
        with live.lock:
            if not failed:
                step.status, step.error, step.error_name = COMPLETED, None, None
                return
            step.error = f"{len(failed)} of {len(step.items)} files failed (first: {failed[0].error})"
            if live.cancel.is_set() and live.run.stopped_by != node.node_id:
                step.status = CANCELLED
            elif allow and worked:
                # the files that worked go on; a resume runs only the failed ones again
                step.status, step.error_name = COMPLETED_WITH_ERRORS, None
            else:
                step.status, step.error_name = FAILED, "CHOREOGRAPHY_STEP_FAILED"

    # ── choreography steps (calls) ───────────────────────────────────────────
    def _call_step(self, live: _Live, node, step: StepState) -> None:
        """A call: a run of another choreography, linked to this one. Its
        parameters are that choreography's run inputs (picked like any
        step's); files handed on become its starting bucket's files; its
        answer is that run's answer. Once per file like a function step."""
        run = live.run
        inner = self._catalog.choreography(node.choreography_id, node.choreography_version)
        info = self._catalog.param_info(node) if inner is not None else None
        if inner is None or info is None:
            which = f" v{node.choreography_version}" if node.choreography_version is not None else ""
            step.status, step.error_name = FAILED, "CHOREOGRAPHY_NOT_FOUND"
            step.error = f"the choreography {node.choreography_name or node.choreography_id}{which} doesn't exist any more"
            return
        step.target_version = inner.version
        got = self._gather(live, node)
        inner_preds = predecessors(inner.graph)
        starts = [n.node_id for n in inner.graph.nodes if n.kind == "bucket" and not inner_preds[n.node_id]]

        def call(holder, item: Optional[int], file: Union[Dict[str, Any], List[Dict[str, Any]], None]) -> None:
            try:
                params, origins = resolve_params(node, info, got.outputs, data=file, finished=got.finished,
                                                 seed=f"{run.run_id}:{node.node_id}")
            except StepInputError as exc:
                holder.status, holder.error, holder.error_name = FAILED, str(exc), exc.error_name
                return
            with live.lock:
                step.param_origins = origins
            files: Optional[Dict[str, Any]] = None
            if starts and origins.get(DATA_PARAM) == "data" and file is not None:
                params.pop(DATA_PARAM, None)
                picked = [dict(f) for f in (file if isinstance(file, list) else [file])]
                files = {node_id: picked for node_id in starts}
            if got.failures and "failure" not in params:
                params["failure"] = (next(iter(got.failures.values())) if len(got.failures) == 1
                                     else {"steps": got.failures})
            self._call(live, node, inner, step, holder, params, files, item)

        self._each_file(live, node, step, got.files, call)

    def _call(self, live: _Live, node, inner: Choreography, step: StepState, holder, params: Dict[str, Any],
              files: Optional[Dict[str, Any]], item: Optional[int]) -> None:
        """Starts the called run (or links to the one it started before: a
        restart, or a resume -- which resumes that run, only its failed
        parts), follows it until it ends, and takes its answer. While it
        waits in that choreography's line, the step waits too, up to its
        ``max_wait``; its time limit covers the whole called run."""
        run = live.run
        timeout = node.timeout_seconds or self._step_timeout
        began = time.monotonic()
        linked = (holder.child or {}).get("run_id")
        with live.lock:
            holder.attempt, holder.status, holder.params = 1, RUNNING, params
        child_id = self._open_call(live, node, inner, step, holder, params, files, item, linked, began)
        if child_id is None:
            return
        while True:
            if live.cancel.is_set():
                if not self._halted.is_set():
                    self._cancel_quietly(child_id)
                holder.status = CANCELLED
                return
            child = self._get_run(child_id)
            self._note_child(live, step, holder, child)
            if child.status in RUN_DONE:
                break
            waited = time.monotonic() - began
            if child.status == QUEUED and node.max_wait is not None and 0 <= node.max_wait <= waited:
                self._cancel_quietly(child_id)
                holder.status, holder.error_name = FAILED, "CHOREOGRAPHY_TOO_BUSY"
                holder.error = (f"{inner.name} had no free run slot for {node.max_wait:g} s "
                                f"({_ordinal((holder.child or {}).get('position') or 1)} in line)")
                return
            if waited >= timeout:
                self._cancel_quietly(child_id)
                holder.status, holder.error_name = FAILED, "CHOREOGRAPHY_STEP_TIMEOUT"
                holder.error = f"the run of {inner.name} took longer than {timeout:g} s"
                return
            live.cancel.wait(self._poll)
        holder.duration_ms = child.duration_ms
        if child.status == CANCELLED:
            holder.status, holder.error_name = FAILED, "CANCELLED"
            holder.error = f"the run of {inner.name} was cancelled"
            return
        if child.status == FAILED:
            holder.status, holder.error_name = FAILED, "CHOREOGRAPHY_CALL_FAILED"
            holder.error = f"the run of {inner.name} failed{_why_failed(child)}"
            return
        try:
            answer, answer_job = self._call_answer(child)
        except (StepInputError, DomainError) as exc:
            holder.status, holder.error = FAILED, str(exc)
            holder.error_name = getattr(exc, "error_name", None) or "CHOREOGRAPHY_CALL_FAILED"
            return
        with live.lock:
            live.outputs[child_id] = answer
            holder.output_bytes = json_size(answer)
            holder.child = {**(holder.child or {}), "answer_job_id": answer_job}
            holder.status, holder.error, holder.error_name = COMPLETED, None, None
            holder.warnings = ([f"the run of {inner.name} completed with errors{_why_failed(child)}"]
                               if child.status == COMPLETED_WITH_ERRORS else [])

    def _open_call(self, live: _Live, node, inner: Choreography, step: StepState, holder, params: Dict[str, Any],
                   files: Optional[Dict[str, Any]], item: Optional[int], linked: Optional[str],
                   began: float) -> Optional[str]:
        """The called run's id: the one this step (or file) was following
        (a restart), else the first id of this step whose run had these same
        inputs (a resume: that run is resumed if it didn't finish well), else
        a new run. Waits while that choreography's line is full, up to the
        step's ``max_wait``. None = the step failed (the reason is on it)."""
        run = live.run
        if linked is not None and self._runs.get(linked) is not None:
            return linked
        child_id = None
        for generation in range(100):
            candidate = call_run_id(run.run_id, node.node_id, item, generation)
            existing = self._runs.get(candidate)
            if existing is None:
                child_id = candidate
                break
            if existing.input == params and (existing.files or {}) == (files or {}):
                if existing.status in RUN_DONE and existing.status != COMPLETED:
                    try:
                        self.resume(candidate)
                    except DomainError as exc:
                        holder.status, holder.error, holder.error_name = FAILED, str(exc), exc.error_name
                        return None
                self._link(live, step, holder, existing)
                return candidate
        if child_id is None:
            holder.status, holder.error, holder.error_name = FAILED, "too many calls", "CHOREOGRAPHY_CALL_FAILED"
            return None
        while True:
            try:
                started = self.start(
                    inner.choreography_id, user_id=run.acting_user_id, version=inner.version, input=params,
                    run_id=child_id, started_by_type="choreography", started_by_id=run.run_id,
                    started_by_name=run.started_by_name, files=files, chain=[*run.chain, run.choreography_id],
                    calls=[*run.calls, {"choreography_id": run.choreography_id, "run_id": run.run_id,
                                        "step": node.node_id, "version": run.version}],
                    parent={"run_id": run.run_id, "choreography_id": run.choreography_id,
                            "choreography_name": run.choreography_name, "version": run.version,
                            "step": node.node_id, "item": item})
                self._link(live, step, holder, ChoreographyRun.from_dict(started))
                return child_id
            except TooManyRequestsError as exc:
                # that choreography's line is full: try again until max_wait
                waited = time.monotonic() - began
                if node.max_wait is not None and 0 <= node.max_wait <= waited or live.cancel.is_set():
                    holder.status, holder.error_name = FAILED, "CHOREOGRAPHY_TOO_BUSY"
                    holder.error = f"{inner.name} is too busy: {exc}"
                    return None
                with live.lock:
                    holder.child = {**(holder.child or {}), "status": "line_full"}
                if live.cancel.wait(max(self._poll, 1.0)):
                    holder.status = CANCELLED
                    return None
            except DomainError as exc:
                holder.status, holder.error, holder.error_name = FAILED, str(exc), exc.error_name
                return None

    def _link(self, live: _Live, step: StepState, holder, child: ChoreographyRun) -> None:
        with live.lock:
            holder.child = {"run_id": child.run_id, "choreography_id": child.choreography_id,
                            "choreography_name": child.choreography_name, "version": child.version,
                            "status": child.status}
            self._save_step(live, step)

    def _note_child(self, live: _Live, step: StepState, holder, child: ChoreographyRun) -> None:
        """Copies the called run's progress onto the step (the canvas shows
        "2 of 3 steps", or its place in line); saved when it changed."""
        counted = [s for s in child.steps.values() if s.kind != "bucket"] or list(child.steps.values())
        note: Dict[str, Any] = {**(holder.child or {}), "status": child.status,
                                "done": sum(1 for s in counted if s.status in STEP_DONE), "total": len(counted)}
        if child.status == QUEUED:
            waiting = self._runs.list(child.choreography_id, status=QUEUED, limit=1000)
            note["position"] = 1 + sum(1 for r in waiting if (r.queued_at or "") < (child.queued_at or "")
                                       and r.run_id != child.run_id)
        else:
            note.pop("position", None)
        with live.lock:
            if note != holder.child:
                holder.child = note
                self._save_step(live, step)

    def _call_answer(self, child: ChoreographyRun) -> Tuple[Any, Optional[str]]:
        """A finished run's answer: its "Its answer" step's, else its only
        last step's, else every last step's by step. With the job that
        holds it (for a link when it's over 1 MB)."""
        choreography = (self._repository.get_version(child.choreography_id, child.version)
                        or self._service.get_version(child.choreography_id, child.version))
        inner = _Live(run=child, choreography=choreography)
        ends = sinks(choreography.graph)
        pick = choreography.settings.answer_step or (ends[0] if len(ends) == 1 else None)
        if pick is None:
            return {n: self._end_answer(inner, n) for n in child.result or {}}, None
        if pick not in (child.result or {}):
            raise StepInputError("CHOREOGRAPHY_CALL_FAILED",
                                 f"the run of {child.choreography_name} ended without its answer step {pick}")
        return self._end_answer(inner, pick), _answer_job(child.steps[pick])

    def _end_answer(self, live: _Live, node_id: str) -> Any:
        kept = (live.run.result or {}).get(node_id) or {}
        if "files" in kept:
            return {"files": kept["files"]}
        return self._step_output(live, live.run.steps[node_id])

    def _cancel_quietly(self, run_id: str) -> None:
        try:
            self.cancel(run_id)
        except DomainError:
            pass

    def _attempts(self, live: _Live, node, holder, params: Dict[str, Any], event: Dict[str, Any],
                  output_buckets: List[str], item: Optional[int]) -> None:
        """Runs the step's job. ``holder`` is the step itself or one of its
        files (both carry the same fields). The job's attempts (a code
        failure tried again per ``max_attempts``, the platform's own
        failures) happen on the endpoint; here a job the endpoint forgot
        (it was lost) is only sent again, with the same id."""
        run = live.run
        timeout = node.timeout_seconds or self._step_timeout
        job_id = job_id_for(run.run_id, node.node_id, item, run.resumes)
        resuming = holder.job_id == job_id and holder.endpoint_id is not None
        with live.lock:
            holder.attempt, holder.job_id, holder.params, holder.status = 1, job_id, params, RUNNING
            self._save_step(live, live.run.steps[node.node_id])
        outcome = None if resuming else self._submit(live, node, holder, params, event, output_buckets, job_id)
        sent = 0 if resuming else 1
        while outcome is None:
            outcome = self._wait(live, holder.endpoint_id, job_id, timeout)
            if outcome.status == JOB_UNKNOWN and sent < PLATFORM_MAX_ATTEMPTS and not live.cancel.is_set():
                sent += 1
                outcome = self._submit(live, node, holder, params, event, output_buckets, job_id)
        if outcome.status == JOB_COMPLETED:
            with live.lock:
                holder.status, holder.error, holder.error_name = COMPLETED, None, None
                holder.warnings = outcome.warnings
                holder.duration_ms = outcome.duration_ms
                live.outputs[job_id] = outcome.output
                holder.output_bytes = json_size(outcome.output)
            return
        if live.cancel.is_set():
            holder.status = CANCELLED
            return
        holder.error, holder.error_name = outcome.error, outcome.error_name
        holder.warnings = outcome.warnings
        # Cancelled on its own (from the jobs list): a failed step.
        holder.status = FAILED

    def _submit(self, live: _Live, node, holder, params, event, output_buckets, job_id) -> Optional[JobStatus]:
        """Starts the job; None when it started (then wait), else the failure."""
        run = live.run
        step_ref: Dict[str, Any] = {"choreography_run_id": run.run_id, "choreography_step": node.node_id,
                                    "choreography_version": run.version}
        if run.calls:
            step_ref["choreography_calls"] = run.calls
        attempts = {"max_attempts": node.max_attempts, "retry_delay_seconds": node.retry_delay_seconds,
                    "retry_backoff": node.retry_backoff}
        # Kept on the job record: the jobs list groups a run's jobs under it.
        choreography = {"choreography_id": run.choreography_id, "choreography_name": run.choreography_name,
                        "run_id": run.run_id, "step": node.node_id, "version": run.version,
                        "run_started_at": run.started_at or run.queued_at,
                        # a called run: the run / step that called it, and the choreographies above
                        "parent": run.parent, "chain": run.chain,
                        # the outermost run and its step this job is part of (the jobs list nests by them)
                        "root_run_id": run.calls[0]["run_id"] if run.calls else run.run_id,
                        "root_step": run.calls[0]["step"] if run.calls else node.node_id}
        keep = live.choreography.settings.keep_results
        try:
            if node.kind == "function":
                started = self._jobs.submit_function(
                    function_id=node.function_id, version=cast(int, live.run.steps[node.node_id].target_version),  # set on start
                    params=params, event=event, output_buckets=output_buckets, job_id=job_id,
                    acting_user_id=run.acting_user_id, choreography_id=run.choreography_id, max_wait=node.max_wait,
                    step_ref=step_ref, attempts=attempts, keep_result=keep, choreography=choreography)
            else:
                started = self._jobs.call_object(
                    name=node.object_name, method=node.method, params=params, event=event,
                    output_buckets=output_buckets, job_id=job_id, acting_user_id=run.acting_user_id,
                    choreography_id=run.choreography_id, max_wait=node.max_wait, step_ref=step_ref,
                    keep_result=keep, choreography=choreography)
        except DomainError as exc:
            return JobStatus("FAILED", error=str(exc), error_name=exc.error_name)
        holder.endpoint_id = started.get("endpoint_id")
        live.jobs[job_id] = (holder.endpoint_id, job_id)
        return None

    def _wait(self, live: _Live, endpoint_id: Optional[str], job_id: str, timeout: float) -> JobStatus:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if live.cancel.is_set():
                    if endpoint_id and not self._halted.is_set():   # a restart leaves jobs running
                        self._jobs.cancel(endpoint_id, job_id)
                    return JobStatus("FAILED", error="cancelled", error_name="CANCELLED")
                status = self._jobs.status(endpoint_id, job_id) if endpoint_id else JobStatus(JOB_UNKNOWN)
                if status.status != JOB_PENDING:
                    return status
                if time.monotonic() >= deadline:
                    if endpoint_id:
                        self._jobs.cancel(endpoint_id, job_id)
                    return JobStatus("FAILED", error=f"the step took longer than {timeout:g} s",
                                     error_name="CHOREOGRAPHY_STEP_TIMEOUT")
                live.cancel.wait(self._poll)
        finally:
            live.jobs.pop(job_id, None)

    # ── answers ──────────────────────────────────────────────────────────────
    def _job_output(self, live: _Live, holder) -> Any:
        """A finished process step's (or file's) answer: its job's result --
        held while the run goes, else read from where it's kept, else (just
        finished, not saved yet) from the endpoint that ran it."""
        child = (holder.child or {}).get("run_id")
        if child:
            # a choreography step: the answer of the run it called
            if child not in live.outputs:
                live.outputs[child] = self._call_answer(self._get_run(child))[0]
            return live.outputs[child]
        job_id = holder.job_id
        if job_id in live.outputs:
            return live.outputs[job_id]
        error: Optional[DomainError] = None
        if self._results is not None:
            try:
                value = self._results.result(job_id)
                live.outputs[job_id] = value
                return value
            except DomainError as exc:
                error = exc
        if holder.endpoint_id:
            status = self._jobs.status(holder.endpoint_id, job_id)
            if status.status == JOB_COMPLETED:
                live.outputs[job_id] = status.output
                return status.output
        raise StepInputError("JOB_RESULT_NOT_KEPT", f"the answer of job {job_id} is not kept any more"
                             + (f" ({error})" if error is not None else ""))

    def _step_output(self, live: _Live, step: StepState) -> Any:
        """Its answer; after a bucket with several files, the list of the
        answers of the files that worked."""
        if step.items:
            return [self._job_output(live, it) for it in step.items if it.status == COMPLETED]
        return self._job_output(live, step)

    def _output_ref(self, job_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Where a big answer is kept, for the next step's event (a file it can read)."""
        if self._results is None or not job_id or not hasattr(self._results, "result_ref"):
            return None
        return self._results.result_ref(job_id)

    # ── bucket steps ─────────────────────────────────────────────────────────
    def _bucket_step(self, live: _Live, node, step: StepState) -> None:
        run = live.run
        if self._buckets is None:
            step.status, step.error = FAILED, "this VE has no bucket storage"
            return
        writers = [p for p in live.preds[node.node_id] if live.delivers(p, node.node_id)]
        attempt = 0
        while True:
            try:
                files = self._write_outputs(live, node, writers) if live.preds[node.node_id] else self._read_files(live, node)
                break
            except DomainError as exc:
                step.error, step.error_name = str(exc), exc.error_name
                attempt += 1
                if _final(exc.error_name) or attempt >= BUCKET_STEP_ATTEMPTS or live.cancel.is_set():
                    step.status = FAILED
                    return
                step.attempt = attempt
                if live.cancel.wait(retry_delay(attempt, 1.0, "exponential")):
                    step.status = CANCELLED
                    return
        with live.lock:
            step.files, step.status, step.error, step.error_name = files, COMPLETED, None, None
            step.output_bytes = sum(int(f.get("size") or 0) for f in files)
        if live.preds[node.node_id]:
            for f in files:
                self._pool.submit(self._safe_bucket_event, f["bucket"], f["name"], f.get("version"),
                                  run.choreography_id)

    def _write_outputs(self, live: _Live, node, writers: List[str]) -> List[Dict[str, Any]]:
        """A bucket after steps holds (and passes on) ONLY this run's files:
        the ones the earlier steps' jobs wrote into it, and -- unless
        ``save_result`` is off -- each earlier step's JSON result."""
        saved = self._save_results(live, node, writers) if node.save_result else []
        return self._job_written(live, node, writers) + saved

    def _job_written(self, live: _Live, node, writers: List[str]) -> List[Dict[str, Any]]:
        """Files the earlier steps' jobs wrote into this bucket during the run
        (axo_storage keeps each write's ``job:<ve>:<job_id>`` tag)."""
        tags: Dict[str, str] = {}
        for source in writers:
            pred = live.run.steps[source]
            for job_id in [pred.job_id, *(i.job_id for i in pred.items)]:
                if job_id:
                    tags[f"job:{self._ve}:{job_id}"] = source
        if not tags:
            return []
        files: List[Dict[str, Any]] = []
        for item in self._buckets.list_objects(live.run.acting_user_id, node.bucket_name):
            writer = tags.get(item.get("source") or "")
            if writer is not None:
                files.append({"bucket": node.bucket_name, "name": item["name"], "version": item.get("version"),
                              "format": item.get("format") or "raw",
                              "size": item.get("total_size", item.get("size")), "source": writer,
                              "written_by": "job"})
        return sorted(files, key=lambda f: (f["name"], int(f.get("version") or 0)))

    def _save_results(self, live: _Live, node, writers: List[str]) -> List[Dict[str, Any]]:
        run = live.run
        pattern = node.key_pattern or DEFAULT_KEY_PATTERN
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        files: List[Dict[str, Any]] = []
        for source in writers:
            pred = run.steps[source]
            pieces = ([(it.index, self._job_output(live, it)) for it in pred.items if it.status == COMPLETED]
                      if pred.items else [(None, self._job_output(live, pred))])
            for index, value in pieces:
                key = render_key(pattern, {"choreography": run.choreography_name, "run_id": run.run_id,
                                           "step": node.node_id, "source": source,
                                           "item": "" if index is None else str(index), "date": date})
                if index is not None and "{item}" not in pattern:
                    key = _with_suffix(key, f"-{index}")
                data = json.dumps(value).encode("utf-8")
                saved = self._buckets.upload(run.acting_user_id, node.bucket_name, key, data, format="json",
                                             source=source_tag(run.choreography_id))
                files.append({"bucket": node.bucket_name, "name": key, "version": saved.get("version"),
                              "format": "json", "size": len(data), "source": source})
        return files

    def _read_files(self, live: _Live, node) -> List[Dict[str, Any]]:
        """A bucket step that starts the run: the file that triggered it, the
        files picked on the step, or every file in the bucket."""
        trigger = live.run.trigger or {}
        if trigger.get("bucket") == node.bucket_name and trigger.get("name"):
            return [{"bucket": node.bucket_name, "name": trigger["name"], "version": trigger.get("version"),
                     "format": trigger.get("format") or "raw"}]
        handed = live.run.files.get(node.node_id)
        if isinstance(handed, list) and handed and all(isinstance(f, dict) and f.get("bucket") for f in handed):
            # handed on by the step that called this run: already read files, kept as they are
            return [dict(f) for f in handed]
        listed = self._buckets.list_objects(live.run.acting_user_id, node.bucket_name)
        latest: Dict[str, Dict[str, Any]] = {}
        for item in listed:
            if item.get("name") not in latest or int(item.get("version") or 0) > int(latest[item["name"]].get("version") or 0):
                latest[item["name"]] = item
        chosen = live.run.files.get(node.node_id, node.selected_items)
        wanted = [{"name": n} for n in sorted(latest)] if chosen == "all" or not chosen else chosen
        files: List[Dict[str, Any]] = []
        for pick in wanted:
            name = str(pick.get("name") or "")
            found = latest.get(name)
            if pick.get("version") is not None:
                found = next((i for i in listed if i.get("name") == name
                              and int(i.get("version") or 0) == int(pick["version"])), None)
            if found is None:
                raise NotFoundError(f"{node.bucket_name} has no file {name}", error_name="OBJECT_NOT_FOUND")
            files.append({"bucket": node.bucket_name, "name": name, "version": found.get("version"),
                          "format": found.get("format") or "raw", "size": found.get("size")})
        return files

    # ── triggers ─────────────────────────────────────────────────────────────
    def on_event(self, event: Dict[str, Any], *, allowed_users: Optional[List[str]] = None,
                 source_choreography_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """An event (CloudEvents shape: id, type, source, time, data): every
        active trigger it matches starts a run of the newest version, acting
        for who added the trigger, with the event's data as the run's input
        (a file's: its bucket step reads that file). The same event twice
        starts nothing new. A choreography's own writes never trigger itself;
        ``allowed_users`` (file events, from the CP): only triggers added by
        someone who may read that bucket."""
        file_event = event.get("type") == models.FILE_WRITTEN
        raw = event.get("data")
        data: Dict[str, Any] = raw if isinstance(raw, dict) else {}
        found = (self._repository.triggers_for_bucket(str(data.get("bucket"))) if file_event
                 else self._repository.triggers_for_event(str(event.get("type"))))
        started: List[Dict[str, Any]] = []
        for choreography in found:
            if choreography.choreography_id == source_choreography_id:
                continue
            for trigger in choreography.triggers:
                if not trigger_matches(trigger, event):
                    continue
                if allowed_users is not None and trigger.get("created_by") not in allowed_users:
                    continue
                run_id = (trigger_run_id(trigger["trigger_id"], str(data.get("bucket")), str(data.get("name")),
                                         data.get("version")) if file_event
                          else event_run_id(trigger["trigger_id"], str(event.get("id"))))
                about = {"kind": event.get("type"), "trigger_id": trigger["trigger_id"],
                         "event": {k: event.get(k) for k in ("id", "type", "source", "time", "subject")}}
                if file_event:
                    about.update({k: data.get(k) for k in ("bucket", "name", "version", "format")})
                try:
                    run = self.start(choreography.choreography_id, user_id=trigger.get("created_by"),
                                     version=trigger.get("version"), run_id=run_id, started_by_type="trigger",
                                     started_by_id=trigger["trigger_id"], trigger=about,
                                     input=None if file_event else event.get("data"))
                    started.append({"choreography": choreography.name, "choreography_id": choreography.choreography_id,
                                    "trigger_id": trigger["trigger_id"], "run_id": run["run_id"],
                                    "status": run["status"]})
                except DomainError as exc:
                    started.append({"choreography": choreography.name, "choreography_id": choreography.choreography_id,
                                    "trigger_id": trigger["trigger_id"], "run_id": run_id, "status": "dropped",
                                    "error": exc.error_name or "VEM_CONFLICT", "message": str(exc)})
        return started

    def on_bucket_written(self, bucket: str, name: str, version: Any, *, format: Optional[str] = None,
                          source: Optional[str] = None, source_choreography_id: Optional[str] = None,
                          allowed_users: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """A new file, as a ``file.written`` event (see on_event)."""
        return self.on_event(file_written_event(bucket, name, version, format=format, source=source),
                             allowed_users=allowed_users, source_choreography_id=source_choreography_id)

    def _safe_bucket_event(self, bucket: str, name: str, version: Any, source: str) -> None:
        try:
            self.on_bucket_written(bucket, name, version, format="json", source_choreography_id=source)
        except Exception:  # a trigger failing must not touch the run that wrote the file
            pass

    # ── cancel / resume / restart ────────────────────────────────────────────
    def cancel(self, run_id: str) -> Dict[str, Any]:
        with self._lock:
            live = self._live.get(run_id)
        if live is None:
            run = self._get_run(run_id)
            if run.status != QUEUED:
                raise ConflictError(f"Run {run_id} already ended ({run.status})",
                                    error_name="CHOREOGRAPHY_RUN_NOT_ACTIVE")
            run.status, run.finished_at = CANCELLED, _now()
            for step in run.steps.values():
                if step.status == PENDING:
                    step.status = CANCELLED
            self._save(run)
            self._broadcast_run(run)
            return run.to_dict()
        live.cancel.set()
        for endpoint_id, job_id in list(live.jobs.values()):
            if endpoint_id:
                self._jobs.cancel(endpoint_id, job_id)
        self._advance(live)
        return live.run.to_dict()

    def resume(self, run_id: str) -> Dict[str, Any]:
        """Runs the failed / skipped / cancelled steps (and the ones after
        them) again; finished steps keep their outputs."""
        run = self._get_run(run_id)
        if run.status not in RUN_DONE or run.status == COMPLETED or run_id in self._live:
            raise ConflictError(f"Run {run_id} can't be resumed ({run.status})",
                                error_name="CHOREOGRAPHY_RUN_NOT_RESUMABLE")
        choreography = self._repository.get_version(run.choreography_id, run.version) or \
            self._service.get_version(run.choreography_id, run.version)
        again = to_resume(choreography.graph, run.steps)
        preds = predecessors(choreography.graph)
        self._check_answers_kept(run, choreography, list(again), preds)
        run.edge_states = {e.edge_id: state for e, state in
                           ((e, run.edge_states.get(e.edge_id)) for e in choreography.graph.edges)
                           if state is not None and e.source_node_id not in again}
        for node_id in again:
            old = run.steps[node_id]
            fresh = StepState(node_id=node_id, kind=old.kind)
            if old.items and not any(p in again for p in preds[node_id]):
                # same files as before: the ones that worked keep their output
                fresh.items = [it if it.status == COMPLETED else ItemState(index=it.index, file=it.file)
                               for it in old.items]
            run.steps[node_id] = fresh
        run.resumes += 1
        run.stopped_by = None
        run.status, run.result, run.finished_at, run.duration_ms = QUEUED, {}, None, None
        run.queued_at = _now()
        self._save(run)
        self._broadcast_run(run)
        self._admit(run.choreography_id)
        return self.run_info(run_id)

    def recover(self) -> int:
        """After a restart: runs that were going carry on, waiting ones wait."""
        active = self._runs.active()
        choreographies = set()
        for run in active:
            choreographies.add(run.choreography_id)
            if run.status != RUNNING:
                continue
            version = self._repository.get_version(run.choreography_id, run.version)
            if version is None:
                self._fail_queued(run, f"version {run.version} is gone")
                continue
            for step in run.steps.values():
                if step.status == RUNNING:
                    step.status = PENDING              # started again; same job ids, so it runs once
            live = _Live(run=run, choreography=version, began=self._clock())
            with self._lock:
                self._live[run.run_id] = live
            self._advance(live)
        for choreography_id in choreographies:
            self._admit(choreography_id)
        return len(active)

    def _check_answers_kept(self, run: ChoreographyRun, choreography: Choreography, again: List[str],
                            preds: Dict[str, List[str]]) -> None:
        """A resume needs the answers of the finished steps the re-run ones
        read (and of the files that worked in a step run again)."""
        if self._results is None or not hasattr(self._results, "kept"):
            return
        needed: List[Tuple[str, Optional[str]]] = []
        for node_id in again:
            for pred_id in preds[node_id]:
                pred = run.steps[pred_id]
                if pred_id in again or pred.kind == "bucket" or pred.status not in SUCCEEDED:
                    continue
                holders = [it for it in pred.items if it.status == COMPLETED] if pred.items else [pred]
                needed += [(pred_id, h.job_id) for h in holders]
        for step_id, job_id in needed:
            if job_id and not self._results.kept(job_id):
                raise ConflictError(f"Run {run.run_id} can't be resumed: the answer of step {step_id} is not "
                                    f"kept any more (its \"keep results for\" time is over)",
                                    error_name="CHOREOGRAPHY_RUN_NOT_RESUMABLE")

    def run_result(self, run_id: str) -> Dict[str, Any]:
        """The last steps' answers (a bucket step: its files)."""
        run = self._get_run(run_id)
        live = _Live(run=run, choreography=self._repository.get_version(run.choreography_id, run.version)
                     or self._service.get_version(run.choreography_id, run.version))
        out: Dict[str, Any] = {}
        for node_id, kept in (run.result or {}).items():
            step = run.steps.get(node_id)
            if step is None or "files" in kept:
                out[node_id] = kept
            else:
                out[node_id] = self._step_output(live, step)
        return out

    def run_summaries(self, run_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """For the jobs list: one line per run."""
        out: Dict[str, Dict[str, Any]] = {}
        for run_id in dict.fromkeys(run_ids):
            try:
                run = self._get_run(run_id)
            except DomainError:
                continue
            out[run_id] = {
                "run_id": run_id, "choreography_id": run.choreography_id,
                "choreography_name": run.choreography_name, "version": run.version, "status": run.status,
                "started_by_type": run.started_by_type, "started_by_id": run.started_by_id,
                "queued_at": run.queued_at, "started_at": run.started_at, "finished_at": run.finished_at,
                "duration_ms": run.duration_ms, "parent": run.parent,
                "steps_total": sum(1 for s in run.steps.values() if s.kind != "bucket"),
            }
        return out

    def cancel_all(self, choreography_id: str) -> int:
        """Before a delete: every waiting / running run of it."""
        stopped = 0
        for run in self._runs.active():
            if run.choreography_id == choreography_id:
                try:
                    self.cancel(run.run_id)
                    stopped += 1
                except DomainError:
                    pass
        return stopped

    def shutdown(self) -> None:
        """Stops without touching any run: the next start's recover() carries them on."""
        self._halted.set()
        with self._lock:
            for live in self._live.values():
                live.cancel.set()                  # wakes every wait; nothing is saved any more
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── reading ──────────────────────────────────────────────────────────────
    @property
    def runs(self) -> ChoreographyRunRepository:
        return self._runs

    def _get_run(self, run_id: str) -> ChoreographyRun:
        with self._lock:
            live = self._live.get(run_id)
        if live is not None:
            return live.run
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"No run {run_id}", error_name="CHOREOGRAPHY_RUN_NOT_FOUND")
        return run

    def run_info(self, run_id: str) -> Dict[str, Any]:
        run = self._get_run(run_id)
        with self._lock:
            live = self._live.get(run_id)
        if live is not None:
            with live.lock:
                return run.to_dict()
        return run.to_dict()

    # ── telling others ───────────────────────────────────────────────────────
    def _save(self, run: ChoreographyRun) -> None:
        if not self._halted.is_set():
            self._runs.save(run)

    def _save_step(self, live: _Live, step: StepState) -> None:
        if self._halted.is_set():
            return
        self._runs.save(live.run)
        if self._broadcaster is not None:
            self._broadcaster.broadcast(f"{RUNS_TOPIC}:{live.run.run_id}",
                                        {"type": "step", "run_id": live.run.run_id, **step.to_dict()})

    def _broadcast_run(self, run: ChoreographyRun) -> None:
        if self._broadcaster is None or self._halted.is_set():
            return
        summary = run_summary(run)
        self._broadcaster.broadcast(RUNS_TOPIC, {"type": "run", **summary})
        self._broadcaster.broadcast(f"{RUNS_TOPIC}:{run.run_id}", {"type": "run", **summary})

    def _append(self, run: ChoreographyRun, event_type: str, event) -> None:
        if self._halted.is_set():
            return
        try:
            self._publisher.append_to_stream(f"choreographies-{run.choreography_id}-runs", event_type,
                                             event.model_dump(mode="json"))
        except Exception:  # the history is best effort; the run record is the truth
            if self._logger is not None:
                self._logger.warning(f"could not record {event_type} for run {run.run_id}")

    def _dims(self, live: _Live) -> Dict[str, str]:
        return {m.DIM_CHOREOGRAPHY: live.run.choreography_name,
                m.DIM_CHOREOGRAPHY_VERSION: str(live.run.version)}

    def _metric(self, metric: str, value: Optional[float], dims: Dict[str, str]) -> None:
        if self._metrics is not None and value is not None:
            self._metrics.observe(metric, float(value), dims)

    def _observe(self, metric: str, value: Optional[float], live: _Live, outcome: Optional[str] = None) -> None:
        dims = self._dims(live)
        if outcome is not None:
            dims[m.DIM_OUTCOME] = outcome
        self._metric(metric, value, dims)

    def _observe_step(self, live: _Live, step: StepState) -> None:
        dims = {**self._dims(live), m.DIM_STEP: step.node_id, m.DIM_OUTCOME: step.status}
        self._metric(m.CHOREOGRAPHY_STEPS, 1, dims)
        self._metric(m.CHOREOGRAPHY_STEP_MS, step.duration_ms, dims)

    def drain_metrics(self, now: Optional[float] = None):
        return self._metrics.drain_closed(now) if self._metrics is not None else []


def failure_summary(node, step: StepState) -> Dict[str, Any]:
    """What an "on failure" arrow carries to its target (``event.failure``):
    the step, its error and job -- or, run once per file, the files that
    failed."""
    out: Dict[str, Any] = {"step": node.node_id, "status": step.status, "error_name": step.error_name,
                           "message": step.error, "job_id": step.job_id,
                           "max_attempts": node.max_attempts}
    if step.child:
        out["run_id"] = step.child.get("run_id")
    if step.items:
        out["failed_files"] = [{"file": it.file, "error_name": it.error_name, "message": it.error,
                                "job_id": it.job_id} for it in step.items if it.status == FAILED]
    return out


def _pinned(live: _Live, node):
    """The step with the version fixed when its run was queued (older runs
    have none: the step picks when it starts)."""
    version = live.run.versions.get(node.node_id)
    if version is None:
        return node
    if node.kind == "function":
        return node.model_copy(update={"function_version": version})
    if node.kind == "choreography":
        return node.model_copy(update={"choreography_version": version})
    return node


def _answer_job(holder) -> Optional[str]:
    """The job whose result is this step's answer (a call: its run's answer step's)."""
    return holder.job_id or (holder.child or {}).get("answer_job_id")


def _why_failed(run: ChoreographyRun) -> str:
    failed = [s for s in run.steps.values() if s.status == FAILED]
    if not failed:
        return ""
    first = failed[0]
    more = f" (and {len(failed) - 1} more)" if len(failed) > 1 else ""
    return f": step {first.node_id}{more} -- {first.error or first.error_name or 'failed'}"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _stopped_reason(live: _Live) -> Optional[str]:
    stopped_by = live.run.stopped_by
    if stopped_by is None:
        return None
    return (f"stopped: step {stopped_by} failed and this choreography doesn't allow completing with errors")


def run_summary(run: ChoreographyRun) -> Dict[str, Any]:
    return {"run_id": run.run_id, "choreography_id": run.choreography_id,
            "choreography_name": run.choreography_name, "version": run.version, "status": run.status,
            "started_by_type": run.started_by_type, "started_by_id": run.started_by_id,
            "started_by_name": run.started_by_name, "trigger": run.trigger, "acting_user_id": run.acting_user_id,
            "resumes": run.resumes, "stopped_by": run.stopped_by, "parent": run.parent,
            "counts": run.counts(),
            "queued_at": run.queued_at, "started_at": run.started_at, "finished_at": run.finished_at,
            "duration_ms": run.duration_ms}


def _with_suffix(key: str, suffix: str) -> str:
    head, dot, ext = key.rpartition(".")
    return f"{head}{suffix}.{ext}" if dot and "/" not in ext else f"{key}{suffix}"


def _ms_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000, 3)
    except ValueError:
        return None
