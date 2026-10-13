from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from axo_vem.application.policies.policy_service import PolicyService
from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.domain.policies.policy import TEMPLATES, ScalingPolicy
from axo_vem.infrastructure.auth.service_token import ActingUser


class _PolicyBody(BaseModel):
    """A full policy, or ``template`` + only the fields to change from it."""

    model_config = {"extra": "allow"}
    template: Optional[str] = None


class _PauseBody(BaseModel):
    minutes: float = 30
    reason: Optional[str] = None


def build_router(policies: PolicyService, current_user_dependency: Callable[..., ActingUser]) -> APIRouter:
    """This VE's scaling policies (evaluated inside the VEM)."""
    router = APIRouter(tags=["scaling policies"])

    def _view(policy: ScalingPolicy) -> Dict[str, Any]:
        return {**policy.to_dict(), "condition": policy.describe()}

    @router.get("/policies")
    def list_policies(_current_user: ActingUser = Depends(current_user_dependency)):
        return {
            "allowed": policies.allowed,
            "paused_until": policies.paused_until() if policies.is_paused() else None,
            "pause_reason": policies.pause_reason(),
            "policies": [_view(p) for p in policies.list()],
        }

    @router.get("/policies/templates")
    def templates(_current_user: ActingUser = Depends(current_user_dependency)):
        return [{"template": name, **template} for name, template in TEMPLATES.items()]

    @router.get("/policies/decisions")
    def decisions(limit: int = 100, _current_user: ActingUser = Depends(current_user_dependency)):
        """What policies did (or would have, in dry run) and why -- newest first."""
        return policies.decisions(limit)

    @router.post("/policies", status_code=201)
    def create_policy(body: _PolicyBody, current_user: ActingUser = Depends(current_user_dependency)):
        fields = dict(body.model_extra or {})
        if body.template is not None:
            template = TEMPLATES.get(body.template)
            if template is None:
                raise NotFoundError(f"no template {body.template!r}")
            fields = {**template["policy"], **fields}
        fields.pop("policy_id", None)
        try:
            policy = ScalingPolicy(**fields)
        except TypeError as exc:
            raise ConflictError(f"invalid policy: {exc}")
        return _view(policies.save(policy, by=current_user.key))

    @router.patch("/policies/{policy_id}")
    def change_policy(policy_id: str, body: _PolicyBody, current_user: ActingUser = Depends(current_user_dependency)):
        """Only the given fields change (e.g. {"dry_run": false} or {"enabled": false})."""
        updated = policies.get(policy_id).with_changes(dict(body.model_extra or {}))
        return _view(policies.save(updated, by=current_user.key))

    @router.delete("/policies/{policy_id}", status_code=204)
    def delete_policy(policy_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        policies.delete(policy_id, by=current_user.key)

    @router.post("/policies/pause")
    def pause(body: _PauseBody, current_user: ActingUser = Depends(current_user_dependency)):
        """Stop every policy from acting for a while (0 minutes = resume now)."""
        until = policies.pause(body.minutes * 60, body.reason or f"paused by {current_user.key}", by=current_user.key)
        return {"paused_until": until if body.minutes > 0 else None}

    return router
