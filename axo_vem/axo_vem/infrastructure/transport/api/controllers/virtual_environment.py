from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.infrastructure.auth.service_token import ActingUser


class _SettingsChange(BaseModel):
    """Only the fields to change; anything omitted stays as it is."""

    model_config = {"extra": "allow"}
    reason: Optional[str] = None


def build_router(
    settings_service: VeSettingsService,
    current_user_dependency: Callable[..., ActingUser],
    lifecycle_service=None,
) -> APIRouter:
    """This VEM's own VE: who it is, its limits, its settings, what it's
    doing (lifecycle, health, endpoint counts, restore progress), and the
    VE-wide operations the Control Plane drives: stop, start, prepare-delete.
    Those return at once; the CP polls GET /ve until they finish."""
    router = APIRouter(tags=["virtual environment"])

    def _view() -> Dict[str, Any]:
        limits = settings_service.limits
        settings = settings_service.current()
        view = {
            "virtual_environment_id": settings_service.virtual_environment_id,
            "plan_limits": limits.__dict__,
            "settings": settings.to_dict(),
            "effective_retention_days": settings.effective_retention_days(limits),
        }
        if lifecycle_service is not None:
            view.update(lifecycle_service.status())
        return view

    @router.get("/ve")
    def get_virtual_environment(_current_user: ActingUser = Depends(current_user_dependency)):
        return _view()

    @router.get("/ve/settings")
    def get_settings(_current_user: ActingUser = Depends(current_user_dependency)):
        return settings_service.current().to_dict()

    @router.patch("/ve/settings")
    def change_settings(body: _SettingsChange, current_user: ActingUser = Depends(current_user_dependency)):
        """Recorded as one VirtualEnvironmentSettingsChanged event, by the
        acting user. 409 lists every value that breaks a rule or plan limit."""
        changes = dict(body.model_extra or {})
        settings_service.change(changes, changed_by_type="user", changed_by_id=current_user.key, reason=body.reason)
        return _view()

    @router.put("/ve/limits")
    def set_limits(body: Dict[str, Any], current_user: ActingUser = Depends(current_user_dependency)):
        """The Control Plane only: a new plan or quota, applied without a
        restart (a target above the new maximum comes down; extra endpoints
        drain). The CP never passes this route through to people."""
        settings_service.set_limits(PlanLimits.from_dict(body), by=current_user.key)
        return _view()

    def _lifecycle():
        if lifecycle_service is None:
            raise HTTPException(status_code=503, detail="VE lifecycle is not configured on this VEM")
        return lifecycle_service

    @router.post("/ve/stop", status_code=202)
    def stop(current_user: ActingUser = Depends(current_user_dependency)):
        """No new jobs; running ones finish (up to the drain timeout); endpoint
        containers stopped but kept. lifecycle → Stopped = ready to stop the base."""
        _lifecycle().stop(by=current_user.key)
        return _view()

    @router.post("/ve/start", status_code=202)
    def start(current_user: ActingUser = Depends(current_user_dependency)):
        """Endpoints back up, then Restoring re-registers every function."""
        _lifecycle().start(by=current_user.key)
        return _view()

    @router.post("/ve/prepare-delete", status_code=202)
    def prepare_delete(current_user: ActingUser = Depends(current_user_dependency)):
        """Like stop, but endpoint containers are removed for good;
        ready_for_removal → true when the CP may remove the base."""
        _lifecycle().prepare_delete(by=current_user.key)
        return _view()

    return router
