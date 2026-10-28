from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from axo_vem.application.choreography.engine import (
    ChoreographyEngine, file_written_event, run_summary, source_choreography,
)
from axo_vem.application.choreography.events import EventsService
from axo_vem.application.choreography.purge_choreography import PurgeChoreographyUseCase
from axo_vem.application.choreography.service import ChoreographyService
from axo_vem.application.visibility import VisibilityService
from axo_vem.domain.errors import NotFoundError
from axo_vem.domain.events.models import ChoreographyGraph, ChoreographySettings, ChoreographyTrigger
from axo_vem.infrastructure.auth.service_token import ActingUser

_RUN_ID = r"^[A-Za-z0-9_-]{8,64}$"


class _SaveRequest(BaseModel):
    name: str
    description: Optional[str] = None
    graph: ChoreographyGraph
    settings: Optional[ChoreographySettings] = None


class _UpdateRequest(BaseModel):
    graph: ChoreographyGraph
    name: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[ChoreographySettings] = None


class _CheckRequest(BaseModel):
    graph: ChoreographyGraph
    choreography_id: Optional[str] = None     # editing this one (its calls can't come back to it)
    settings: Optional[ChoreographySettings] = None


class _RunRequest(BaseModel):
    version: Optional[int] = Field(default=None, ge=1)    # the newest when not given
    input: Any = None                                     # the event the first steps get
    run_id: Optional[str] = Field(default=None, pattern=_RUN_ID)
    # starting bucket step -> the files this run reads instead of the ones
    # picked on the step: [{name, version?}], or "all" (every file)
    files: Optional[Dict[str, Union[List[Dict[str, Any]], str]]] = None


class _BucketEvent(BaseModel):
    bucket: str
    name: str
    version: Optional[int] = None
    format: Optional[str] = None
    source: Optional[str] = None                 # what wrote it ("choreography:<id>", ...)
    allowed_users: Optional[List[str]] = None    # trigger creators who may read the bucket


def build_router(
    service: ChoreographyService,
    engine: ChoreographyEngine,
    current_user_dependency: Callable[..., ActingUser],
    purge_use_case: Optional[PurgeChoreographyUseCase] = None,
    retention_cutoff: Optional[Callable[[], Optional[datetime]]] = None,
    visibility: Optional[VisibilityService] = None,
    events: Optional[EventsService] = None,
) -> APIRouter:
    """Choreographies by name (or id). Roles were checked by the Control
    Plane: Viewer reads, Developer creates / edits / runs / sets triggers,
    Admin deletes."""
    router = APIRouter(tags=["choreography"])

    def _since(since_minutes: Optional[int]) -> Optional[datetime]:
        cutoffs = [c for c in ((retention_cutoff() if retention_cutoff else None),) if c is not None]
        if since_minutes is not None and since_minutes > 0:
            cutoffs.append(datetime.now(timezone.utc) - timedelta(minutes=since_minutes))
        return max(cutoffs) if cutoffs else None

    @router.post("/choreographies", status_code=201)
    def create_choreography(body: _SaveRequest, current_user: ActingUser = Depends(current_user_dependency)):
        return service.create(user_id=current_user.key, name=body.name, graph=body.graph,
                              settings=body.settings, description=body.description, user_name=current_user.name)

    @router.post("/choreographies/save")
    def save_choreography(body: _SaveRequest, current_user: ActingUser = Depends(current_user_dependency)):
        """Creates it, or saves a new version of the one with this name
        (nothing changed = the same version, ``existing: true``)."""
        return service.save(user_id=current_user.key, name=body.name, graph=body.graph,
                            settings=body.settings, description=body.description, user_name=current_user.name)

    @router.post("/choreographies/check")
    def check_graph(body: _CheckRequest, _current_user: ActingUser = Depends(current_user_dependency)):
        """Problems (it can't be saved) and warnings (allowed, worth knowing)."""
        return service.check(body.graph, body.choreography_id, body.settings)

    @router.get("/choreographies/designer")
    def designer(_current_user: ActingUser = Depends(current_user_dependency)):
        """What the designer's dialogs show: every step / arrow /
        choreography setting, merge strategies, condition comparisons."""
        return service.designer()

    @router.get("/choreographies")
    def list_choreographies(_current_user: ActingUser = Depends(current_user_dependency)):
        return service.list()

    @router.get("/choreographies/{ref}")
    def get_choreography(ref: str, version: Optional[int] = None,
                         _current_user: ActingUser = Depends(current_user_dependency)):
        return service.detail(ref, version)

    @router.get("/choreographies/{ref}/versions")
    def list_versions(ref: str, _current_user: ActingUser = Depends(current_user_dependency)):
        return service.versions(ref)

    @router.put("/choreographies/{ref}")
    def update_choreography(ref: str, body: _UpdateRequest,
                            current_user: ActingUser = Depends(current_user_dependency)):
        """A new version; runs already going keep theirs."""
        return service.update(user_id=current_user.key, ref=ref, graph=body.graph, name=body.name,
                              settings=body.settings, description=body.description, user_name=current_user.name)

    @router.delete("/choreographies/{ref}")
    def delete_choreography(ref: str, current_user: ActingUser = Depends(current_user_dependency)):
        """Cancels its waiting and running runs, then deletes it (purge later).
        Refused while another choreography calls it."""
        choreography = service.get(ref)
        service.ensure_deletable(choreography)
        cancelled = engine.cancel_all(choreography.choreography_id)
        return {**service.delete(user_id=current_user.key, ref=choreography.choreography_id),
                "runs_cancelled": cancelled}

    if purge_use_case is not None:

        @router.delete("/choreographies/{choreography_id}/purge")
        def purge_choreography(choreography_id: str, _current_user: ActingUser = Depends(current_user_dependency)):
            purge_use_case.execute(choreography_id=choreography_id)
            return {"choreography_id": choreography_id, "purged": True}

    # ── triggers ─────────────────────────────────────────────────────────────
    @router.get("/trigger-buckets")
    def trigger_buckets(_current_user: ActingUser = Depends(current_user_dependency)):
        """The buckets this VE's triggers watch, as of ``at`` (the same list
        the heartbeat carries). The Control Plane reads it right after a call
        that changes triggers, so a file written next is routed here."""
        return {"triggers": service.trigger_buckets(), "at": datetime.now(timezone.utc).isoformat()}

    @router.post("/choreographies/{ref}/triggers", status_code=201)
    def add_trigger(ref: str, body: ChoreographyTrigger, current_user: ActingUser = Depends(current_user_dependency)):
        """A new version with one more trigger: each matching event (a new
        file in a bucket, or an event someone sends) starts a run, acting for you."""
        return service.add_trigger(user_id=current_user.key, ref=ref, trigger=body)

    @router.delete("/choreographies/{ref}/triggers/{trigger_id}")
    def remove_trigger(ref: str, trigger_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        return service.remove_trigger(user_id=current_user.key, ref=ref, trigger_id=trigger_id)

    @router.post("/bucket-events")
    def bucket_event(body: _BucketEvent, _current_user: ActingUser = Depends(current_user_dependency)):
        """A new file was written (sent on by the Control Plane): starts the
        runs of every matching trigger. The same file twice starts nothing new."""
        event = file_written_event(body.bucket, body.name, body.version, format=body.format, source=body.source)
        runs = engine.on_event(event, allowed_users=body.allowed_users,
                               source_choreography_id=source_choreography(body.source))
        if events is not None:
            events.file_written(event, runs)
        return {"runs": runs}

    # ── runs ─────────────────────────────────────────────────────────────────
    @router.post("/choreographies/{ref}/runs", status_code=201)
    def start_run(ref: str, body: Optional[_RunRequest] = None,
                  current_user: ActingUser = Depends(current_user_dependency)):
        body = body or _RunRequest()
        return engine.start(ref, user_id=current_user.key, version=body.version, input=body.input,
                            run_id=body.run_id, files=body.files, started_by_name=current_user.name)

    @router.get("/choreographies/{ref}/runs")
    def list_runs(ref: str, status: Optional[str] = None, since_minutes: Optional[int] = None, limit: int = 100,
                  started_by_type: Optional[str] = None, parent_run_id: Optional[str] = None,
                  current_user: ActingUser = Depends(current_user_dependency)):
        """Newest first: every run when the choreography shares its runs with
        this person (or they're a VE Admin / Owner), else only theirs.
        ``started_by_type`` user / trigger / choreography (called by another
        choreography's step); ``parent_run_id``: the runs that run called."""
        choreography = service.get(ref)
        viewer = current_user.viewer
        everyone = visibility is None or visibility.can_see_run(viewer, choreography.choreography_id, None)
        runs = engine.runs.list(choreography.choreography_id, status=status, since=_since(since_minutes),
                                limit=limit, acting_user_id=None if everyone else viewer.user_id,
                                started_by_type=started_by_type, parent_run_id=parent_run_id)
        return [run_summary(r) for r in runs]

    def _visible(run: Dict[str, Any], user: ActingUser) -> Dict[str, Any]:
        if visibility is not None:
            visibility.require_run(user.viewer, run)
        return run

    def _run_of(ref: str, run_id: str, user: ActingUser):
        choreography = service.get(ref)
        run = engine.run_info(run_id)
        if run["choreography_id"] != choreography.choreography_id:
            raise NotFoundError(f"{choreography.name} has no run {run_id}", error_name="CHOREOGRAPHY_RUN_NOT_FOUND")
        return _visible(run, user)

    @router.get("/choreographies/{ref}/runs/{run_id}")
    def get_run(ref: str, run_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """Everything the run did, step by step (what the canvas draws)."""
        return _run_of(ref, run_id, current_user)

    @router.get("/choreographies/{ref}/runs/{run_id}/result")
    def get_run_result(ref: str, run_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """The last steps' answers (their jobs' kept results; a bucket step: its files)."""
        _run_of(ref, run_id, current_user)
        return {"run_id": run_id, "result": engine.run_result(run_id)}

    @router.post("/choreographies/{ref}/runs/{run_id}/cancel")
    def cancel_run(ref: str, run_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        _run_of(ref, run_id, current_user)
        return engine.cancel(run_id)

    @router.post("/choreographies/{ref}/runs/{run_id}/resume")
    def resume_run(ref: str, run_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """Runs the failed, skipped and cancelled steps again."""
        _run_of(ref, run_id, current_user)
        return engine.resume(run_id)

    @router.get("/choreography-runs/{run_id}")
    def get_run_by_id(run_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        return _visible(engine.run_info(run_id), current_user)

    @router.get("/choreographies/{ref}/usage")
    def usage(ref: str, since_minutes: Optional[int] = None,
              _current_user: ActingUser = Depends(current_user_dependency)):
        """Runs by status / version / trigger / person, success rate, durations."""
        choreography = service.get(ref)
        return {"choreography": choreography.name,
                **engine.runs.usage(choreography.choreography_id, since=_since(since_minutes))}

    return router
