from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends

from axo_control_plane.application.people import Actor
from axo_control_plane.domain.errors import UnavailableError
from axo_control_plane.infrastructure.api.deps import Services, actor, services

DAY = 86400.0


def build_router() -> APIRouter:
    router = APIRouter()

    def billing(svc: Services):
        if svc.billing is None:
            raise UnavailableError("billing is not available")
        return svc.billing

    @router.get("/ves/{ve_id}/costs", tags=["billing"])
    def ve_costs(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """VE Costs tab (VE Owner and Admin): this period so far, past bills,
        cost per function."""
        return billing(svc).ve_costs(who, ve_id)

    @router.get("/me/ve-buckets", tags=["buckets"])
    def my_ve_buckets(who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The platform buckets of the person's VEs (functions / results / objects), read-only."""
        return billing(svc).my_ve_buckets(who)

    @router.get("/ves/{ve_id}/stored", tags=["billing"])
    def ve_stored(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Job results and object state kept: sizes for every VE role, costs
        for the Owner / Admin."""
        return billing(svc).ve_stored(who, ve_id)

    @router.get("/ves/{ve_id}/costs/jobs", tags=["billing"])
    def job_costs(ve_id: str, job_ids: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """``job_ids=a,b,...``: each job's cost, every attempt together (VE
        Owner and Admin); for one job, also per attempt."""
        return billing(svc).job_costs(who, ve_id, job_ids.split(","))

    @router.get("/ves/{ve_id}/costs/choreographies", tags=["billing"])
    def choreography_costs(ve_id: str, start: Optional[float] = None, end: Optional[float] = None,
                           who: Actor = Depends(actor), svc: Services = Depends(services)):
        """What each choreography's step jobs cost (VE Owner and Admin;
        information, the jobs themselves are on the bill)."""
        return billing(svc).choreography_costs(who, ve_id, start=start, end=end)

    @router.get("/ves/{ve_id}/costs/choreographies/{choreography_id}", tags=["billing"])
    def one_choreography_costs(ve_id: str, choreography_id: str, run_id: Optional[str] = None,
                               start: Optional[float] = None, end: Optional[float] = None,
                               who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Per version, step and run -- or one run's steps (``run_id``)."""
        return billing(svc).choreography_costs(who, ve_id, choreography_id, run_id, start, end)

    @router.get("/ves/{ve_id}/bills/{period_index}", tags=["billing"])
    def ve_bill(ve_id: str, period_index: int, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return billing(svc).ve_bill(who, ve_id, period_index)

    @router.get("/me/billing", tags=["billing"])
    def my_billing(who: Actor = Depends(actor), svc: Services = Depends(services)):
        """My VEs' bills and my storage bills."""
        return billing(svc).my_billing(who)

    @router.get("/billing/bills/{bill_id}", tags=["billing"])
    def one_bill(bill_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """One bill (VE or storage) for whoever pays it: the person, or the
        Team Owner and Team Admins."""
        return billing(svc).bill(who, bill_id)

    @router.get("/me/billing/storage/{month}", tags=["billing"])
    def my_storage_bill(month: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return billing(svc).my_storage_bill(who, month)

    @router.get("/teams/{team_id}/billing", tags=["billing"])
    def team_billing(team_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The team's VE and storage bills + spending (Team Owner and Admins)."""
        return billing(svc).team_billing(who, team_id)

    @router.get("/teams/{team_id}/billing/storage/{month}", tags=["billing"])
    def team_storage_bill(team_id: str, month: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return billing(svc).team_storage_bill(who, team_id, month)

    @router.get("/admin/revenue", tags=["admin"])
    def revenue(start: Optional[float] = None, end: Optional[float] = None, limit: int = 10,
                who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Platform Admin: fees per plan, usage per VE / owner, top spenders
        (default: the last 30 days)."""
        end = time.time() if end is None else end
        start = end - 30 * DAY if start is None else start
        return billing(svc).admin_revenue(who, start, end, limit=max(1, min(limit, 100)))

    return router
