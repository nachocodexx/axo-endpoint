from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from axo_vem.application.objects.service import ObjectService
from axo_vem.domain.errors import NotOwnerError
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.transport.api.controllers.history import history_page
from axo_vem.infrastructure.transport.api.controllers.jobs import CP_WAIT_HEADER

_JOB_ID = r"^[A-Za-z0-9_-]{8,64}$"
_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$"


class _CreateRequest(BaseModel):
    name: str = Field(pattern=_NAME)
    class_name: str
    class_version: Optional[int] = None          # the latest when not given
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    job_id: Optional[str] = Field(default=None, pattern=_JOB_ID)
    max_wait: Optional[float] = None
    keep_revisions: Optional[int] = Field(default=None, ge=0)   # None = the plan's default


class _CallRequest(BaseModel):
    method: str = "run"
    params: Dict[str, Any] = {}
    job_id: Optional[str] = Field(default=None, pattern=_JOB_ID)
    max_wait: Optional[float] = None
    keep_result: Optional[str] = None       # 1h 1d 7d 30d 90d 1y never; else the VE's default


class _UpgradeRequest(BaseModel):
    class_version: Optional[int] = None


class _CloneRequest(BaseModel):
    name: str = Field(pattern=_NAME)


class _RestoreRequest(BaseModel):
    revision: int = Field(ge=0)


class _ClassCheckRequest(BaseModel):
    class_name: str = ""
    source: str = Field(max_length=1_000_000)


class _ObjectChange(BaseModel):
    keep_revisions: Optional[int] = Field(default=None, ge=0)   # None = the plan's default


def _json_form(value: Optional[str], field: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not valid JSON: {exc}")


def _waits(cp_wait_ms: Optional[float]) -> Optional[Dict[str, float]]:
    return {"control_plane": max(0.0, cp_wait_ms)} if cp_wait_ms is not None else None


def build_router(
    service: ObjectService,
    current_user_dependency: Callable[..., ActingUser],
    activity_repository: Optional[MongoActivityRepository] = None,
    retention_cutoff: Optional[Callable[[], Optional[datetime]]] = None,
) -> APIRouter:
    """Axo classes and objects (the ``axo`` client's ``register_class`` /
    ``create`` / ``ObjectHandle``). Calls, creates and upgrades are jobs:
    poll ``/endpoints/{endpoint_id}/jobs/{job_id}`` like any other."""
    router = APIRouter(tags=["active objects"])

    @router.post("/object-classes", status_code=201)
    async def register_class(
        class_name: str = Form(...),
        code_format: str = Form("source"),
        code_hash: Optional[str] = Form(None),
        methods: Optional[str] = Form(None),
        has_upgrade: Optional[str] = Form(None),
        runtime_spec: Optional[str] = Form(None),
        code: UploadFile = File(...),
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """A new version of an Axo class -- the same code again answers with
        its existing version (``existing: true``)."""
        return service.register_class(
            user_id=current_user.key, class_name=class_name, code=await code.read(), code_format=code_format,
            code_hash=code_hash or None, methods=_json_form(methods, "methods", []),
            has_upgrade=bool(_json_form(has_upgrade, "has_upgrade", False)),
            runtime_spec=_json_form(runtime_spec, "runtime_spec", None),
        )

    @router.post("/object-classes/check")
    def check_class(body: _ClassCheckRequest, _current_user: ActingUser = Depends(current_user_dependency)):
        """The browser's live check of a class being written: problems (with
        their line), its classes, methods, __init__'s parameters -- never runs it."""
        return service.check_class(body.source, body.class_name)

    @router.delete("/object-classes/{class_name}/{version}")
    def delete_class_version(class_name: str, version: int,
                             current_user: ActingUser = Depends(current_user_dependency)):
        """Only a version no object is on. Admins delete any; a Developer only
        versions they registered."""
        doc = next((c for c in service.classes(class_name) if int(c["version"]) == version), None)
        if doc is not None and not (current_user.at_least("admin") or doc.get("created_by") == current_user.key):
            raise NotOwnerError("only an Admin, or the Developer who registered this version, can delete it")
        return service.delete_class_version(user_id=current_user.key, class_name=class_name, version=version)

    @router.get("/object-classes")
    def list_classes(_current_user: ActingUser = Depends(current_user_dependency)):
        return service.classes()

    @router.get("/object-classes/{class_name}")
    def class_versions(class_name: str, _current_user: ActingUser = Depends(current_user_dependency)):
        return service.classes(class_name)

    @router.post("/objects", status_code=201)
    def create_object(
        body: _CreateRequest,
        current_user: ActingUser = Depends(current_user_dependency),
        cp_wait_ms: Optional[float] = Header(default=None, alias=CP_WAIT_HEADER),
    ):
        """Reserves the name and runs ``__init__`` as a job."""
        return service.create(
            user_id=current_user.key, name=body.name, class_name=body.class_name,
            class_version=body.class_version, args=body.args, kwargs=body.kwargs, job_id=body.job_id,
            max_wait=body.max_wait, upstream_waits_ms=_waits(cp_wait_ms), keep_revisions=body.keep_revisions,
        )

    @router.get("/objects")
    def list_objects(_current_user: ActingUser = Depends(current_user_dependency)):
        return service.objects()

    @router.get("/objects/{name}")
    def get_object(name: str, _current_user: ActingUser = Depends(current_user_dependency)):
        return service.info(name)

    @router.patch("/objects/{name}")
    def change_object(name: str, body: _ObjectChange, current_user: ActingUser = Depends(current_user_dependency)):
        """Its own settings: how many past revisions it keeps (up to the plan's most)."""
        return service.set_keep_revisions(user_id=current_user.key, name=name, keep_revisions=body.keep_revisions)

    @router.post("/objects/{name}/calls")
    def call_object(
        name: str, body: _CallRequest,
        current_user: ActingUser = Depends(current_user_dependency),
        cp_wait_ms: Optional[float] = Header(default=None, alias=CP_WAIT_HEADER),
    ):
        return service.call(
            user_id=current_user.key, name=name, method=body.method, params=body.params, job_id=body.job_id,
            max_wait=body.max_wait, upstream_waits_ms=_waits(cp_wait_ms), keep_result=body.keep_result,
            **current_user.started_by,
        )

    @router.post("/objects/{name}/upgrade")
    def upgrade_object(name: str, body: _UpgradeRequest, current_user: ActingUser = Depends(current_user_dependency)):
        return service.upgrade(user_id=current_user.key, name=name, class_version=body.class_version)

    @router.post("/objects/{name}/clone", status_code=201)
    def clone_object(name: str, body: _CloneRequest, current_user: ActingUser = Depends(current_user_dependency)):
        return service.clone(user_id=current_user.key, source=name, name=body.name)

    @router.post("/objects/{name}/restore")
    def restore_object(name: str, body: _RestoreRequest, current_user: ActingUser = Depends(current_user_dependency)):
        return service.restore(user_id=current_user.key, name=name, revision=body.revision)

    @router.get("/objects/{name}/state")
    def object_state(name: str, revision: Optional[int] = None,
                     _current_user: ActingUser = Depends(current_user_dependency)):
        """Small values in full, big ones by size, and the kept revisions."""
        return service.state(name, revision)

    if activity_repository is not None:

        @router.get("/objects/{name}/history")
        def object_history(name: str, since_minutes: Optional[int] = None, limit: int = 100,
                           before: Optional[str] = None, before_id: Optional[str] = None,
                           _current_user: ActingUser = Depends(current_user_dependency)):
            """Created / every call (method, outcome, revision, what changed)
            / upgraded, newest first, within the plan's retention."""
            obj = service.info(name)
            cutoffs = [c for c in ((retention_cutoff() if retention_cutoff else None),) if c is not None]
            if since_minutes is not None and since_minutes > 0:
                cutoffs.append(datetime.now(timezone.utc) - timedelta(minutes=since_minutes))
            return activity_repository.get_timeline(
                active_object_id=obj["object_id"], since=max(cutoffs) if cutoffs else None,
                **history_page(limit, before, before_id))

    @router.delete("/objects/{name}")
    def delete_object(name: str, current_user: ActingUser = Depends(current_user_dependency)):
        return service.delete(user_id=current_user.key, name=name)

    return router
