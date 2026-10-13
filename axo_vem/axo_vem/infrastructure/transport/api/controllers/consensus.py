from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pymongo.collection import Collection
from axo_vem.infrastructure.auth.service_token import ActingUser

from axo_vem.infrastructure.transport.api.serialization import strip_id


def build_router(consensus: Collection, current_user_dependency: Callable[..., ActingUser]) -> APIRouter:
    router = APIRouter(tags=["endpoint management"])

    @router.get("/consensus/leader")
    def get_leader(_current_user: ActingUser = Depends(current_user_dependency)):
        """Current leader = highest term reported so far (consensus._id is
        the term, not a singleton -- see infrastructure/database/mongo/consensus_repository.py)."""
        doc = consensus.find_one(sort=[("_id", -1)])
        if doc is None:
            raise HTTPException(status_code=404, detail="no consensus view reported yet")
        return strip_id(doc)

    return router
