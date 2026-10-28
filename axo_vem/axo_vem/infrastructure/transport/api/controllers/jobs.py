from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from pymongo.collection import Collection

from axo_shared import wire
from axo_shared.protocol import Command

from axo_vem.application.compute.submit_function_job import SubmitFunctionJobUseCase
from axo_vem.application.visibility import VisibilityService
from axo_vem.domain.errors import NotFoundError
from axo_vem.domain.execution.repository import JobFilters, JobRepository
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.transport.zmq_command.endpoint_client import resolve_rpc_uri, send_command


# How long the Control Plane held the submission (ms) -- including a
# sleeping VE's wake -- recorded as the job's "control_plane" wait.
CP_WAIT_HEADER = "X-Axo-Wait-Ms"
PAGE_MAX = 200


class _JobSubmitRequest(BaseModel):
    function_id: str
    function_name: Optional[str] = None
    function_version: int
    params: Dict[str, Any] = {}


class _FunctionJobSubmitRequest(BaseModel):
    params: Dict[str, Any] = {}
    # Optional caller-chosen id (the Control Plane brings one for a job it
    # accepted while the VE was asleep); the same id twice runs once.
    job_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")
    max_wait: Optional[float] = None
    # This job's own attempts (else the function's) and how long its result is kept.
    max_attempts: Optional[int] = None
    retry_delay_seconds: Optional[float] = None
    retry_backoff: Optional[str] = None
    keep_result: Optional[str] = None


def _split(raw: Optional[str]) -> List[str]:
    return [part for part in (raw or "").split(",") if part]


def build_router(
    job_repository: JobRepository,
    current_user_dependency: Callable[..., ActingUser],
    endpoints: Optional[Collection] = None,
    command_timeout_seconds: float = 3.0,
    submit_function_job_use_case: Optional[SubmitFunctionJobUseCase] = None,
    jobs_service: Any = None,
    run_summaries: Optional[Callable[[List[str]], Dict[str, Dict[str, Any]]]] = None,
    visibility: Optional[VisibilityService] = None,
) -> APIRouter:
    router = APIRouter(tags=["job telemetry"])

    def _check(job_id: str, user: ActingUser) -> None:
        """Who sees runs and results (application/visibility.py)."""
        if visibility is not None and not user.viewer.sees_all:
            job = job_repository.get(job_id)
            visibility.require_job(user.viewer, job.to_dict() if job is not None else None)

    if submit_function_job_use_case is not None:

        @router.post("/functions/{function_id}/{version}/jobs", tags=["function registries"])
        def submit_function_job(
            function_id: str,
            version: int,
            body: _FunctionJobSubmitRequest,
            current_user: ActingUser = Depends(current_user_dependency),
            cp_wait_ms: Optional[float] = Header(default=None, alias=CP_WAIT_HEADER),
        ):
            """Callers never pick an endpoint -- the target node is resolved
            server-side as one of this VE's running endpoints (see
            SubmitFunctionJobUseCase). Answers ``{job_id, endpoint_id,
            status}``; follow the job with GET /jobs/{job_id}."""
            return submit_function_job_use_case.execute(
                function_id=function_id, version=version, params=body.params, acting_user_id=current_user.key,
                job_id=body.job_id, max_wait=body.max_wait,
                upstream_waits_ms={"control_plane": max(0.0, cp_wait_ms)} if cp_wait_ms is not None else None,
                attempts={"max_attempts": body.max_attempts, "retry_delay_seconds": body.retry_delay_seconds,
                          "retry_backoff": body.retry_backoff},
                keep_result=body.keep_result,
                **current_user.started_by,
            )

    @router.get("/jobs")
    def list_jobs(
        function_id: Optional[str] = None,
        version: Optional[int] = None,
        status: Optional[str] = Query(default=None, description="comma separated: QUEUED,RUNNING,..."),
        outcome: Optional[str] = Query(default=None, description="comma separated: failed_user,failed_platform,..."),
        started_by_type: Optional[str] = None,
        started_by_id: Optional[str] = None,
        error: Optional[str] = Query(default=None, description="an error name, e.g. FUNCTION_EXEC_ERROR"),
        job_id: Optional[str] = Query(default=None, description="the start of a job id"),
        since: Optional[str] = None,
        until: Optional[str] = None,
        choreography_id: Optional[str] = None,
        run_id: Optional[str] = None,
        endpoint_id: Optional[str] = None,
        before: Optional[str] = None,
        before_id: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=PAGE_MAX),
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """The VE's jobs, newest first (the ones this person may see): a job on its own is one row; the jobs
        of one choreography run are one row together (``type: "run"``, with
        how many of the run's jobs match the filters). ``next`` = the
        ``before`` / ``before_id`` of the next page (None = no more)."""
        filters = JobFilters(
            function_id=function_id, function_version=version, statuses=_split(status), outcomes=_split(outcome),
            started_by_type=started_by_type, started_by_id=started_by_id, error_name=error, job_id=job_id,
            since=since, until=until, choreography_id=choreography_id, run_id=run_id, endpoint_id=endpoint_id,
            visible_to=visibility.shared(current_user.viewer) if visibility is not None else None,
        )
        rows = job_repository.page(filters, before=before, before_id=before_id, limit=limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        runs = run_summaries([r.run_id for r in rows if r.run_id]) if run_summaries is not None else {}
        out = []
        for row in rows:
            if row.run_id:
                run = runs.get(row.run_id) or {"run_id": row.run_id}
                out.append({"type": "run", "row_id": row.row_id, "row_at": row.row_at, "run": run,
                            "jobs": [j.to_dict() for j in row.jobs], "shown": len(row.jobs),
                            "steps": run.get("steps_total")})
            else:
                out.append({"type": "job", "row_id": row.row_id, "row_at": row.row_at, "job": row.jobs[0].to_dict()})
        last = rows[-1] if rows else None
        return {"rows": out, "next": {"before": last.row_at, "before_id": last.row_id} if more and last else None}

    @router.get("/functions/{function_id}/jobs")
    def list_jobs_for_function(
        function_id: str,
        version: Optional[int] = None,
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """Every job of one function (optionally one version) this person may see, newest first."""
        jobs = [job.to_dict() for job in job_repository.list_by_function(function_id, version)]
        if visibility is None or current_user.viewer.sees_all:
            return jobs
        return [job for job in jobs if visibility.can_see_job(current_user.viewer, job)]

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """The job with every attempt, newest last. Tracebacks only for
        Developers and above."""
        _check(job_id, current_user)
        if jobs_service is not None:
            return jobs_service.detail(job_id, with_traceback=current_user.at_least("developer"))
        job = job_repository.get(job_id)
        if job is None:
            raise NotFoundError("job not found")
        return job.to_dict()

    if jobs_service is not None:

        @router.get("/jobs/{job_id}/result")
        def job_result(job_id: str, download: bool = False,
                       current_user: ActingUser = Depends(current_user_dependency)):
            """The job's result (always JSON); ``download=true`` = as a file."""
            _check(job_id, current_user)
            value = jobs_service.result(job_id)
            headers = {"Content-Disposition": f'attachment; filename="{job_id}.json"'} if download else {}
            return Response(json.dumps(value, indent=2 if download else None, default=str),
                            media_type="application/json", headers=headers)

        @router.post("/jobs/{job_id}/cancel")
        def cancel_job(job_id: str, current_user: ActingUser = Depends(current_user_dependency)):
            """Stops a queued, running or waiting-to-retry job."""
            _check(job_id, current_user)
            return jobs_service.cancel(job_id, current_user.key)

        if submit_function_job_use_case is not None:

            @router.post("/jobs/{job_id}/retry")
            def retry_job(job_id: str, current_user: ActingUser = Depends(current_user_dependency)):
                """A new attempt of the same job (same id, same params)."""
                _check(job_id, current_user)
                return jobs_service.retry(job_id, current_user.key, submit_function_job_use_case.resubmit)

        @router.get("/results")
        def kept_results(before: Optional[str] = None, before_id: Optional[str] = None,
                         limit: int = Query(default=50, ge=1, le=PAGE_MAX),
                         current_user: ActingUser = Depends(current_user_dependency)):
            """The VE's results bucket, as this person may browse it (the
            Buckets page): their jobs' results plus the ones shared with them;
            everything for a VE Admin / Owner. A file = GET /jobs/{id}/result."""
            shared = visibility.shared(current_user.viewer) if visibility is not None else None
            return job_repository.kept_results(shared, before=before, before_id=before_id, limit=limit)

        @router.get("/ve/results/shortened")
        def shortened(longest: str, _current_user: ActingUser = Depends(current_user_dependency)):
            """How many kept results a shorter "longest time" would shorten (the settings page warns first)."""
            return {"longest": longest, "shortened": jobs_service.shortened_by(longest)}

    if endpoints is not None:

        @router.post("/endpoints/{endpoint_id}/jobs", tags=["endpoint management"])
        def submit_job(
            endpoint_id: str,
            body: _JobSubmitRequest,
            current_user: ActingUser = Depends(current_user_dependency),
        ):
            """Proxies JOB_SUBMIT straight to one endpoint (troubleshooting);
            normal submissions use POST /functions/{id}/{version}/jobs."""
            rpc_uri = _rpc(endpoints, endpoint_id)
            command = Command(
                operation=wire.JOB_SUBMIT,
                content_type="application/json",
                envelope={
                    "function_id": body.function_id,
                    "function_name": body.function_name,
                    "function_version": body.function_version,
                    "params": body.params,
                    "acting_user_id": current_user.key,
                    **{k: v for k, v in current_user.started_by.items() if k != "started_by_name"},
                },
            )
            result = send_command(rpc_uri, command, command_timeout_seconds)
            if result.is_err:
                raise HTTPException(status_code=504, detail=str(result.unwrap_err()))
            command_result = result.unwrap()
            if not command_result.ok:
                raise HTTPException(status_code=409, detail=command_result.error)
            return {"endpoint_id": endpoint_id, **(command_result.metadata or {})}

        @router.get("/endpoints/{endpoint_id}/jobs/{job_id}", tags=["endpoint management"])
        def poll_job_result(
            endpoint_id: str,
            job_id: str,
            current_user: ActingUser = Depends(current_user_dependency),
        ):
            """Relays JOB_RESULT to the node -- the quickest answer while a
            job runs (the endpoint keeps a result only an hour; the VEM keeps
            it as long as the job asks: GET /jobs/{job_id}/result)."""
            _check(job_id, current_user)
            rpc_uri = _rpc(endpoints, endpoint_id)
            command = Command(operation=wire.JOB_RESULT, content_type="application/json", envelope={"job_id": job_id})
            result = send_command(rpc_uri, command, command_timeout_seconds)
            if result.is_err:
                raise HTTPException(status_code=504, detail=str(result.unwrap_err()))
            command_result = result.unwrap()
            if not command_result.ok:
                raise HTTPException(status_code=409, detail=command_result.error)
            return {"job_id": job_id, "endpoint_id": endpoint_id, **(command_result.metadata or {})}

    return router


def _rpc(endpoints: Collection, endpoint_id: str) -> str:
    doc = endpoints.find_one({"_id": endpoint_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    router_bind = doc.get("router_bind")
    if not router_bind:
        raise HTTPException(status_code=409, detail="endpoint has no known router_bind")
    return resolve_rpc_uri(router_bind, endpoint_id)
