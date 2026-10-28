from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends

from axo_vem.domain.errors import InvalidRequestError
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.database.mongo.activity_repository import AREAS, MongoActivityRepository

MAX_LIMIT = 500


def history_areas(areas: Optional[str]) -> List[str]:
    """``areas=job,endpoint`` -> the areas, each checked."""
    wanted = [a.strip() for a in (areas or "").split(",") if a.strip()]
    unknown = [a for a in wanted if a not in AREAS]
    if unknown:
        raise InvalidRequestError(f"unknown area(s) {', '.join(unknown)}; choose from {', '.join(AREAS)}")
    return wanted


def history_page(limit: int, before: Optional[str], before_id: Optional[str]) -> dict:
    """The page asked for: ``limit`` rows after the row ``before`` /
    ``before_id`` (see build_router)."""
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidRequestError(f"limit must be between 1 and {MAX_LIMIT}")
    at = None
    if before:
        try:
            at = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidRequestError("before must be an ISO time, the created_at of the last row shown")
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    return {"limit": limit, "before": at, "before_id": before_id if at is not None else None}


def build_router(
    activity_repository: MongoActivityRepository,
    current_user_dependency: Callable[..., ActingUser],
    retention_cutoff: Optional[Callable[[], Optional[datetime]]] = None,
) -> APIRouter:
    """This VE's activity timeline from unified_activity: one catch-all view
    plus per-entity views. Nothing older than the VE's retention is ever
    returned (``retention_cutoff``: records before it are hidden; they're
    deleted for good only after the grace days, so raising retention soon
    brings them back). ``since_minutes`` narrows the window further for a
    caller that wants less (e.g. the UI's "last hour").

    Paging ("Load more"): ``before`` / ``before_id`` = the ``created_at`` /
    ``event_id`` of the last row shown; a page shorter than ``limit`` is the
    last one. ``areas`` (comma-separated: job, function, object,
    choreography, endpoint, ve) filters on the server, so a page is always
    full of what was asked for."""
    router = APIRouter()  # no router-level default tag: each route below needs its own

    def _since(since_minutes: Optional[int]) -> Optional[datetime]:
        cutoffs = []
        if retention_cutoff is not None:
            cutoff = retention_cutoff()
            if cutoff is not None:
                cutoffs.append(cutoff)
        if since_minutes is not None and since_minutes > 0:
            cutoffs.append(datetime.now(timezone.utc) - timedelta(minutes=since_minutes))
        return max(cutoffs) if cutoffs else None

    @router.get("/history", tags=["job telemetry"])
    def get_history(
        user_id: Optional[str] = None,
        endpoint_id: Optional[str] = None,
        function_id: Optional[str] = None,
        active_object_id: Optional[str] = None,
        since_minutes: Optional[int] = None,
        limit: int = 100,
        areas: Optional[str] = None,
        before: Optional[str] = None,
        before_id: Optional[str] = None,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(
            user_id=user_id,
            endpoint_id=endpoint_id,
            function_id=function_id,
            active_object_id=active_object_id,
            since=_since(since_minutes),
            areas=history_areas(areas),
            **history_page(limit, before, before_id),
        )

    @router.get("/endpoints/{endpoint_id}/history", tags=["endpoint management"])
    def get_endpoint_history(
        endpoint_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        before: Optional[str] = None, before_id: Optional[str] = None,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(endpoint_id=endpoint_id, since=_since(since_minutes),
                                                **history_page(limit, before, before_id))

    @router.get("/functions/{function_id}/history", tags=["function registries"])
    def get_function_history(
        function_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        before: Optional[str] = None, before_id: Optional[str] = None,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(function_id=function_id, since=_since(since_minutes),
                                                **history_page(limit, before, before_id))

    @router.get("/active-objects/{active_object_id}/history", tags=["active objects"])
    def get_active_object_history(
        active_object_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        before: Optional[str] = None, before_id: Optional[str] = None,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(
            active_object_id=active_object_id, since=_since(since_minutes), **history_page(limit, before, before_id),
        )

    @router.get("/choreographies/{choreography_id}/history", tags=["choreography"])
    def get_choreography_history(
        choreography_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        before: Optional[str] = None, before_id: Optional[str] = None,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        """Created, edits (new versions), runs started / finished, who-sees
        changes and deletion: every row whose meta names this choreography."""
        return activity_repository.get_timeline(
            choreography_id=choreography_id, since=_since(since_minutes), **history_page(limit, before, before_id),
        )

    return router
