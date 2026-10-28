from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.collection import Collection

from axo_vem.application.nodes.purge_endpoint import PurgeEndpointUseCase
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.transport.api.serialization import strip_id


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class _EndpointCreateRequest(BaseModel):
    """"Add N endpoints of size X" -- size omitted = the VE's endpoint size."""

    count: int = 1
    cpu_cores: Optional[float] = None
    memory_mb: Optional[int] = None
    reason: Optional[str] = None


def build_router(
    endpoints: Collection,
    current_user_dependency: Callable[..., ActingUser],
    activity_repository: MongoActivityRepository,
    purge_eligible_after_minutes_default: int = 60,
    purge_endpoint_use_case: Optional[PurgeEndpointUseCase] = None,
    reconciler: Optional[Reconciler] = None,
    settings_service: Optional[VeSettingsService] = None,
    fleet=None,
) -> APIRouter:
    """This VE's endpoints. The Control Plane creates and removes them
    through here; the VEM does the actual work (Reconciler), recording each
    endpoint's lifecycle state (creating, running, sleeping, draining, ...)
    alongside what the endpoint itself last reported."""
    router = APIRouter(tags=["endpoint management"])

    def _decorate(doc: Dict[str, Any]) -> Dict[str, Any]:
        """The endpoint's own report plus: lifecycle (the VEM's view, if it
        manages it), and purge_eligible/unreachable_since for the UI."""
        result = strip_id(doc)
        status = doc.get("status")
        if status == "stopped":
            result["purge_eligible"] = True
            result["unreachable_since"] = None
        elif status == "unreachable":
            since_raw = doc.get("created_at")
            since = _parse_iso(since_raw)
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=purge_eligible_after_minutes_default)
            result["purge_eligible"] = since is not None and since <= cutoff
            result["unreachable_since"] = since_raw
        else:
            result["purge_eligible"] = False
            result["unreachable_since"] = None
        managed = fleet.get(doc["_id"]) if fleet is not None else None
        if managed is not None:
            result.update(_lifecycle(managed))
        return result

    def _lifecycle(managed) -> Dict[str, Any]:
        return {
            "lifecycle": managed.state, "lifecycle_since": managed.since, "lifecycle_reason": managed.reason,
            "cpu_cores": managed.cpu_cores, "memory_mb": managed.memory_mb, "failures": managed.failures,
        }

    def _forgotten(endpoint_id: str) -> bool:
        """Forgotten endpoints are never shown again, even if their doc comes
        back (a projector rebuild replays their EndpointStarted / Stopped)."""
        return fleet is not None and fleet.is_forgotten(endpoint_id)

    def _require_reconciler() -> Reconciler:
        if reconciler is None:
            raise HTTPException(status_code=503, detail="endpoint management is not configured on this VEM")
        return reconciler

    @router.get("/endpoints")
    def list_endpoints(include_removed: bool = False, _current_user: ActingUser = Depends(current_user_dependency)):
        listed = [_decorate(doc) for doc in endpoints.find() if not _forgotten(doc["_id"])]
        if fleet is not None:
            # Managed endpoints that haven't reported in yet (creating).
            known = {e["endpoint_id"] for e in listed if "endpoint_id" in e} | {e.get("_id") for e in listed}
            for managed in fleet.all():
                if managed.endpoint_id not in known:
                    listed.append({"endpoint_id": managed.endpoint_id, **_lifecycle(managed)})
        if not include_removed:
            listed = [e for e in listed if e.get("lifecycle") != "removed"]
        return listed

    @router.post("/endpoints", status_code=201)
    def create_endpoints(body: _EndpointCreateRequest, current_user: ActingUser = Depends(current_user_dependency)):
        """Target + N; the N new endpoints are created right away with this
        size. 409 if it would pass the plan's endpoint maximum."""
        manager = _require_reconciler()
        defaults = settings_service.current() if settings_service is not None else None
        created = manager.create(
            body.count,
            cpu_cores=body.cpu_cores or (defaults.new_endpoint_cpu_cores if defaults else 1.0),
            memory_mb=body.memory_mb or (defaults.new_endpoint_memory_mb if defaults else 1024),
            by=current_user.key, reason=body.reason,
        )
        if len(created) < body.count:
            raise HTTPException(status_code=502, detail="; ".join(manager.last_errors) or "endpoint creation failed")
        return [{"endpoint_id": e.endpoint_id, **_lifecycle(e)} for e in created]

    @router.delete("/endpoints/{endpoint_id}")
    def remove_endpoint(endpoint_id: str, reason: Optional[str] = None,
                        current_user: ActingUser = Depends(current_user_dependency)):
        """Target − 1 and this endpoint is the one that goes: it drains (no
        new jobs, running ones finish), then its container is removed."""
        removed = _require_reconciler().remove(endpoint_id, by=current_user.key, reason=reason)
        return {"endpoint_id": endpoint_id, **_lifecycle(removed)}

    @router.post("/endpoints/{endpoint_id}/restart")
    def restart_endpoint(endpoint_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        restarted = _require_reconciler().restart(endpoint_id, by=current_user.key)
        return {"endpoint_id": endpoint_id, **_lifecycle(restarted)}

    @router.get("/endpoints/{endpoint_id}")
    def get_endpoint(endpoint_id: str, _current_user: ActingUser = Depends(current_user_dependency)):
        doc = endpoints.find_one({"_id": endpoint_id})
        if doc is not None and not _forgotten(endpoint_id):
            return _decorate(doc)
        managed = fleet.get(endpoint_id) if fleet is not None else None
        if managed is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        return {"endpoint_id": endpoint_id, **_lifecycle(managed)}

    @router.delete("/endpoints/{endpoint_id}/purge")
    def purge_endpoint(endpoint_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """"Forget": deletes a gone endpoint's read-model doc (its history
        stays until the VE's retention deletes it) -- requires it to be
        stopped or unreachable; it's never listed again. Cascades to the functions
        only it still held (see PurgeEndpointUseCase). A removed / failed
        endpoint the VEM managed also leaves the fleet (one that never
        reported in has only that)."""
        managed = fleet.get(endpoint_id) if fleet is not None else None
        if managed is not None and endpoints.find_one({"_id": endpoint_id}) is None:
            if not fleet.forget(endpoint_id, by=current_user.key):
                raise HTTPException(status_code=409, detail="only a removed or failed endpoint can be forgotten")
            return {"endpoint_id": endpoint_id}
        if purge_endpoint_use_case is not None:
            result = purge_endpoint_use_case.execute(endpoint_id=endpoint_id)
            if managed is not None:
                fleet.forget(endpoint_id, by=current_user.key)
            return result

        doc = endpoints.find_one({"_id": endpoint_id})
        if doc is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        if doc.get("status") not in ("stopped", "unreachable"):
            raise HTTPException(status_code=409, detail="endpoint must be stopped or unreachable before it can be purged")

        endpoints.delete_one({"_id": endpoint_id})
        if managed is not None:
            fleet.forget(endpoint_id, by=current_user.key)
        return {"endpoint_id": endpoint_id}

    return router
