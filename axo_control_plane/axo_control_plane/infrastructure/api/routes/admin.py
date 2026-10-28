from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from axo_control_plane.application.people import Actor
from axo_control_plane.application.settings import UserSettings
from axo_control_plane.domain.plans import Plan, PriceList
from axo_control_plane.infrastructure.api.deps import Services, platform_admin, services, storage_caller


class RoleBody(BaseModel):
    role: str


class StorageQuestion(BaseModel):
    user_id: str
    bucket: str
    mode: str
    virtual_environment_id: Optional[str] = None


def build_router() -> APIRouter:
    router = APIRouter()

    # ── Platform Admin (operational information only) ───────────────────────
    @router.get("/admin/capacity", tags=["admin"])
    def capacity(_: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return svc.capacity.summary()

    @router.get("/admin/users", tags=["admin"])
    def users(_: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return svc.people.list()

    @router.put("/admin/users/{user_id}/platform-role", status_code=204, tags=["admin"])
    def set_role(user_id: str, body: RoleBody, who: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        svc.people.set_platform_role(user_id, body.role, by=who.user_id)

    @router.get("/admin/settings/defaults", tags=["admin", "settings"])
    def setting_defaults(_: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return _settings(svc).describe_defaults()

    @router.patch("/admin/settings/defaults", tags=["admin", "settings"])
    def change_setting_defaults(body: Dict[str, Any], who: Actor = Depends(platform_admin),
                                svc: Services = Depends(services)):
        """The defaults of everyone who didn't choose a value; ``null`` = the built-in one."""
        return _settings(svc).set_defaults(body, by=who.user_id)

    @router.get("/admin/ves", tags=["admin"])
    def all_ves(who: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return svc.ves.list_for(who)

    @router.get("/admin/plans", tags=["admin"])
    def plans(_: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return [p.to_dict() for p in svc.catalog.plans(include_inactive=True)]

    @router.put("/admin/plans/{plan_id}", tags=["admin"])
    def save_plan(plan_id: str, body: Dict[str, Any], who: Actor = Depends(platform_admin),
                  svc: Services = Depends(services)):
        return svc.catalog.save_plan(Plan.from_dict({**body, "plan_id": plan_id}), by=who.user_id).to_dict()

    @router.put("/admin/prices", tags=["admin"])
    def set_prices(body: Dict[str, int], who: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return svc.catalog.set_prices(PriceList.from_dict({**svc.catalog.prices().to_dict(), **body}),
                                      by=who.user_id).to_dict()

    @router.get("/admin/cleanup", tags=["admin"])
    def cleanup_log(limit: int = 100, _: Actor = Depends(platform_admin), svc: Services = Depends(services)):
        return svc.cleanup.recent(min(limit, 500)) if svc.cleanup is not None else []

    # ── axo_storage asks: may this user read / write this bucket? ───────────
    @router.post("/internal/storage/access", tags=["internal"])
    def storage_access(body: StorageQuestion, _: None = Depends(storage_caller), svc: Services = Depends(services)):
        return svc.storage_access.check(body.user_id, body.bucket, body.mode, body.virtual_environment_id)

    return router


def _settings(svc: Services) -> UserSettings:
    return svc.settings or UserSettings(svc.db)
