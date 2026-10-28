from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from axo_vem.application.visibility import VisibilityService
from axo_vem.infrastructure.auth.service_token import ActingUser


class _VisibilityRequest(BaseModel):
    mode: str = Field(description="runner (only who runs it) | ve (everyone in the VE) | teams")
    teams: List[str] = []          # team ids, for mode "teams"
    name: Optional[str] = None     # what to call it in the history


def build_router(
    visibility: VisibilityService,
    current_user_dependency: Callable[..., ActingUser],
    resolve: Optional[Callable[[str, str], str]] = None,
) -> APIRouter:
    """"Who sees runs and results" of a function (its id), a choreography
    (its name or id) or an object (its name). Anyone in the VE reads it;
    its creator and the VE's Admins / Owner change it."""
    router = APIRouter(tags=["visibility"])

    def _ref(kind: str, ref: str) -> str:
        return resolve(kind, ref) if resolve is not None else ref

    @router.get("/visibility/{kind}/{ref}")
    def get_visibility(kind: str, ref: str, current_user: ActingUser = Depends(current_user_dependency)):
        return visibility.describe(current_user.viewer, kind, _ref(kind, ref))

    @router.put("/visibility/{kind}/{ref}")
    def set_visibility(kind: str, ref: str, body: _VisibilityRequest,
                       current_user: ActingUser = Depends(current_user_dependency)):
        return visibility.set(current_user.viewer, kind, _ref(kind, ref), body.mode, body.teams,
                              name=body.name or ref)

    return router
