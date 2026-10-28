from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from axo_vem.application.choreography.events import PAGE_MAX, EventsService
from axo_vem.infrastructure.auth.service_token import ActingUser


class _EmitRequest(BaseModel):
    type: str
    data: Any = None
    # your own id: the same id twice is one event (starts its runs once)
    event_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    source: Optional[str] = Field(default=None, max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)


class _EventTypeRequest(BaseModel):
    description: str = Field(default="", max_length=2000)
    fields: List[Dict[str, Any]] = []


def build_router(events: EventsService, current_user_dependency: Callable[..., ActingUser]) -> APIRouter:
    """Events that start choreographies (Developer+ sends them; the Control
    Plane checks the role), the VE's event types, and "used before"
    suggestions for the designer."""
    router = APIRouter(tags=["choreography"])

    @router.post("/events", status_code=201)
    def emit_event(body: _EmitRequest, current_user: ActingUser = Depends(current_user_dependency)):
        """Sends an event: every choreography with a matching trigger starts a run."""
        return events.emit(sender={"user_id": current_user.key, **current_user.started_by}, event_type=body.type,
                           data=body.data, event_id=body.event_id, source=body.source, subject=body.subject)

    @router.get("/events")
    def list_events(type: Optional[str] = None, before: Optional[str] = None, before_id: Optional[str] = None,
                    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
                    current_user: ActingUser = Depends(current_user_dependency)):
        """Newest first: every event for a VE Admin / Owner; for anyone else
        the ones they sent and the ones that started their triggers."""
        return events.list(current_user.viewer, event_type=type, before=before, before_id=before_id, limit=limit)

    @router.get("/events/{event_id}")
    def get_event(event_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        return events.get(current_user.viewer, event_id)

    @router.get("/event-types")
    def event_types(_current_user: ActingUser = Depends(current_user_dependency)):
        return events.types()

    @router.put("/event-types/{event_type}")
    def save_event_type(event_type: str, body: _EventTypeRequest,
                        current_user: ActingUser = Depends(current_user_dependency)):
        return events.save_type(user_id=current_user.key, event_type=event_type, description=body.description,
                                fields=body.fields)

    @router.delete("/event-types/{event_type}")
    def delete_event_type(event_type: str, _current_user: ActingUser = Depends(current_user_dependency)):
        return events.delete_type(event_type=event_type)

    @router.get("/choreography-suggestions")
    def suggestions(_current_user: ActingUser = Depends(current_user_dependency)):
        """Event types and conditions used before (newest versions), most used first."""
        return events.suggestions()

    return router
