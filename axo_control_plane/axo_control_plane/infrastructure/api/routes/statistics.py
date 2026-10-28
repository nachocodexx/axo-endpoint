from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Depends

from axo_control_plane.application.people import Actor
from axo_control_plane.application.statistics import DEFAULT_STATISTICS
from axo_control_plane.domain.errors import InvalidError, UnavailableError
from axo_control_plane.infrastructure.api.deps import Services, actor, services

DAY = 86400.0


def _window(start: Optional[float], end: Optional[float]) -> Tuple[float, float]:
    """Defaults: the last 24 hours."""
    end = time.time() if end is None else end
    return (end - DAY if start is None else start), end


def _dims(text: str) -> Dict[str, str]:
    """``function:f1,outcome:completed`` -> {"function": "f1", "outcome": "completed"}."""
    out: Dict[str, str] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        key, sep, value = part.partition(":")
        if not sep or not key:
            raise InvalidError("dims must look like name:value,name:value")
        out[key] = value
    return out


def _statistics(text: str):
    return tuple(s.strip() for s in text.split(",") if s.strip()) or DEFAULT_STATISTICS


def build_router() -> APIRouter:
    router = APIRouter()

    def stats(svc: Services):
        if svc.statistics is None:
            raise UnavailableError("statistics are not available")
        return svc.statistics

    @router.get("/ves/{ve_id}/stats/chart", tags=["statistics"])
    def chart(ve_id: str, metric: str, start: Optional[float] = None, end: Optional[float] = None,
              statistics: str = "", dims: str = "", group_by: Optional[str] = None, step_s: Optional[int] = None,
              component_kind: Optional[str] = None, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """A metric over time from the time bins (e.g. job.total_ms p95 per
        function); any role in the VE."""
        s, e = _window(start, end)
        return stats(svc).chart(who, ve_id, metric, s, e, statistics=_statistics(statistics), dims=_dims(dims),
                                group_by=group_by, step_s=step_s, component_kind=component_kind)

    @router.get("/ves/{ve_id}/stats/summary", tags=["statistics"])
    def summary(ve_id: str, metric: str, start: Optional[float] = None, end: Optional[float] = None,
                statistics: str = "", dims: str = "", group_by: Optional[str] = None,
                component_kind: Optional[str] = None, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The whole window as one value set (per ``group_by`` value)."""
        s, e = _window(start, end)
        return stats(svc).summary(who, ve_id, metric, s, e, statistics=_statistics(statistics), dims=_dims(dims),
                                  group_by=group_by, component_kind=component_kind)

    @router.get("/ves/{ve_id}/stats/top", tags=["statistics"])
    def top(ve_id: str, by: str, start: Optional[float] = None, end: Optional[float] = None, limit: int = 10,
            sort: str = "attempts", who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Top functions / versions / users / API keys / outcomes / buckets
        (at most 90 days); costs only for the VE Owner and Admin."""
        s, e = _window(start, end)
        return stats(svc).top(who, ve_id, by, s, e, limit=max(1, min(limit, 100)), sort=sort)

    @router.get("/ves/{ve_id}/stats/heatmap", tags=["statistics"])
    def heatmap(ve_id: str, start: Optional[float] = None, end: Optional[float] = None, time_zone: str = "UTC",
                who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Attempts per weekday x hour (at most 90 days)."""
        s, e = _window(start, end)
        return stats(svc).heatmap(who, ve_id, s, e, time_zone=time_zone)

    @router.get("/ves/{ve_id}/stats/jobs/{job_id}", tags=["statistics"])
    def job_times(ve_id: str, job_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Where one job's time went, per attempt."""
        return stats(svc).job_times(who, ve_id, job_id)

    # ── the person's dashboard: several VEs at once ─────────────────────────
    def _ids(ve_ids: str):
        return [v for v in (p.strip() for p in ve_ids.split(",")) if v] or None

    @router.get("/me/stats/chart", tags=["statistics"])
    def my_chart(metric: str, ve_ids: str = "", start: Optional[float] = None, end: Optional[float] = None,
                 statistics: str = "", dims: str = "", group_by: Optional[str] = None, step_s: Optional[int] = None,
                 component_kind: Optional[str] = None, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Like /ves/{id}/stats/chart over every VE the person has a role in
        (or ``ve_ids``, comma separated), merged exactly on the server."""
        s, e = _window(start, end)
        return stats(svc).my_chart(who, _ids(ve_ids), metric, s, e, statistics=_statistics(statistics),
                                   dims=_dims(dims), group_by=group_by, step_s=step_s, component_kind=component_kind)

    @router.get("/me/stats/summary", tags=["statistics"])
    def my_summary(metric: str, ve_ids: str = "", start: Optional[float] = None, end: Optional[float] = None,
                   statistics: str = "", dims: str = "", group_by: Optional[str] = None,
                   component_kind: Optional[str] = None, who: Actor = Depends(actor),
                   svc: Services = Depends(services)):
        s, e = _window(start, end)
        return stats(svc).my_summary(who, _ids(ve_ids), metric, s, e, statistics=_statistics(statistics),
                                     dims=_dims(dims), group_by=group_by, component_kind=component_kind)

    @router.get("/me/stats/top", tags=["statistics"])
    def my_top(by: str, ve_ids: str = "", start: Optional[float] = None, end: Optional[float] = None,
               limit: int = 10, sort: str = "attempts", who: Actor = Depends(actor),
               svc: Services = Depends(services)):
        """Costs only when the person is Owner / Admin of every VE included."""
        s, e = _window(start, end)
        return stats(svc).my_top(who, _ids(ve_ids), by, s, e, limit=max(1, min(limit, 100)), sort=sort)

    @router.get("/me/stats/heatmap", tags=["statistics"])
    def my_heatmap(ve_ids: str = "", start: Optional[float] = None, end: Optional[float] = None,
                   time_zone: str = "UTC", who: Actor = Depends(actor), svc: Services = Depends(services)):
        s, e = _window(start, end)
        return stats(svc).my_heatmap(who, _ids(ve_ids), s, e, time_zone=time_zone)

    @router.get("/me/overview", tags=["statistics"])
    def my_overview(who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The dashboard's "My VEs": every VE with a role, its status, and
        this period's bill so far where the person may see money."""
        if svc.billing is None:
            raise UnavailableError("billing is not available")
        return svc.billing.my_overview(who)

    return router
