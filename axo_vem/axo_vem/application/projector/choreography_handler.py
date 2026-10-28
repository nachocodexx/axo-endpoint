from __future__ import annotations

from typing import Any, Dict

from axo_vem.domain.choreography.choreography import Choreography
from axo_vem.domain.choreography.repository import ChoreographyRepository
from axo_vem.domain.events import models
from axo_vem.domain.events.models import ChoreographyGraph, ChoreographySettings

CHOREOGRAPHY_EVENT_TYPES = frozenset({
    models.CHOREOGRAPHY_CREATED, models.CHOREOGRAPHY_UPDATED, models.CHOREOGRAPHY_DELETED,
    models.CHOREOGRAPHY_TRIGGER_ADDED, models.CHOREOGRAPHY_TRIGGER_REMOVED,
})
# Only history (unified_activity): the run record itself is kept by the engine.
CHOREOGRAPHY_RUN_EVENT_TYPES = frozenset({models.CHOREOGRAPHY_RUN_STARTED, models.CHOREOGRAPHY_RUN_FINISHED})


def apply(repository: ChoreographyRepository, event_type: str, data: Dict[str, Any]) -> None:
    """ChoreographyService already saved what these say (so a save can be
    read at once); applying them again is harmless, and is how the read
    model is rebuilt from the event log."""
    choreography_id = data["choreography_id"]
    if event_type in (models.CHOREOGRAPHY_CREATED, models.CHOREOGRAPHY_UPDATED):
        existing = repository.get(choreography_id)
        created_at = data["created_at"] if existing is None else existing.created_at
        owner = data.get("owner_user_id") or (existing.owner_user_id if existing is not None else "")
        repository.save(Choreography(
            choreography_id=choreography_id, name=data["name"], owner_user_id=owner,
            version=int(data.get("version") or 1), graph=ChoreographyGraph.model_validate(data["graph"]),
            settings=ChoreographySettings.model_validate(data.get("settings") or {}),
            description=data.get("description"),
            legacy_triggers=existing.legacy_triggers if existing is not None else [],
            created_at=created_at, updated_at=data["created_at"], updated_by=data.get("user_id"),
        ))
        repository.reserve_name(data["name"], choreography_id)
        if existing is not None and existing.name != data["name"] and existing.version < int(data.get("version") or 1):
            repository.release_name(existing.name, choreography_id)
    elif event_type == models.CHOREOGRAPHY_DELETED:
        existing = repository.get(choreography_id)
        repository.soft_delete(choreography_id, data["created_at"])
        if existing is not None:
            repository.release_name(existing.name, choreography_id)
    elif event_type == models.CHOREOGRAPHY_TRIGGER_ADDED:
        existing = repository.get(choreography_id)
        if existing is not None and all(t.get("trigger_id") != data["trigger_id"] for t in existing.triggers):
            repository.set_triggers(choreography_id, [*existing.triggers, {
                "trigger_id": data["trigger_id"], "kind": data.get("kind"), "bucket": data["bucket"],
                "prefix": data.get("prefix"), "version": data.get("version"), "created_by": data.get("user_id"),
                "created_at": data["created_at"],
            }])
    elif event_type == models.CHOREOGRAPHY_TRIGGER_REMOVED:
        existing = repository.get(choreography_id)
        if existing is not None:
            repository.set_triggers(choreography_id,
                                    [t for t in existing.triggers if t.get("trigger_id") != data["trigger_id"]])
