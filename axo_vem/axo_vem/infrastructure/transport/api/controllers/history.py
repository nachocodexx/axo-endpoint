from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends

from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository


def build_router(
    activity_repository: MongoActivityRepository,
    current_user_dependency: Callable[..., ActingUser],
    retention_cutoff: Optional[Callable[[], Optional[datetime]]] = None,
) -> APIRouter:
    """This VE's activity timeline from unified_activity: one catch-all view
    plus per-entity views. Nothing older than the VE's retention is ever
    returned (``retention_cutoff``: records before it are hidden, never
    deleted -- raising retention brings them back). ``since_minutes`` narrows
    the window further for a caller that wants less (e.g. the UI's "last
    hour")."""
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
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(
            user_id=user_id,
            endpoint_id=endpoint_id,
            function_id=function_id,
            active_object_id=active_object_id,
            since=_since(since_minutes),
            limit=limit,
        )

    @router.get("/endpoints/{endpoint_id}/history", tags=["endpoint management"])
    def get_endpoint_history(
        endpoint_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(endpoint_id=endpoint_id, since=_since(since_minutes), limit=limit)

    @router.get("/functions/{function_id}/history", tags=["function registries"])
    def get_function_history(
        function_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(function_id=function_id, since=_since(since_minutes), limit=limit)

    @router.get("/active-objects/{active_object_id}/history", tags=["active objects"])
    def get_active_object_history(
        active_object_id: str, since_minutes: Optional[int] = None, limit: int = 100,
        _current_user: ActingUser = Depends(current_user_dependency),
    ):
        return activity_repository.get_timeline(
            active_object_id=active_object_id, since=_since(since_minutes), limit=limit,
        )

    return router
