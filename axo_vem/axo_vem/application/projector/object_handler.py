from __future__ import annotations

from typing import Any, Dict, Optional

from axo_vem.domain.events import models
from axo_vem.domain.objects.repository import ActiveObjectRepository, ObjectClassRepository
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster

OBJECT_EVENT_TYPES = frozenset({
    models.ACTIVE_OBJECT_CLASS_REGISTERED, models.ACTIVE_OBJECT_CLASS_VERSION_DELETED, models.ACTIVE_OBJECT_CREATED, models.ACTIVE_OBJECT_CALLED,
    models.ACTIVE_OBJECT_UPGRADED, models.ACTIVE_OBJECT_DELETED, models.ACTIVE_OBJECT_STATE_SIZES_REPORTED,
})

_WS_TOPIC = "objects"
_LIVE_TYPES = frozenset({models.ACTIVE_OBJECT_CLASS_REGISTERED, models.ACTIVE_OBJECT_CLASS_VERSION_DELETED,
                         models.ACTIVE_OBJECT_CREATED,
                         models.ACTIVE_OBJECT_CALLED, models.ACTIVE_OBJECT_UPGRADED, models.ACTIVE_OBJECT_DELETED})

_CLASS_FIELDS = ("class_name", "version", "code_hash", "methods", "has_upgrade", "function_id", "runtime_spec",
                 "init", "description", "user_id", "created_at")


def apply(
    classes: Optional[ObjectClassRepository], objects: Optional[ActiveObjectRepository],
    event_type: str, data: Dict[str, Any], broadcaster: Optional[Broadcaster] = None,
) -> None:
    """Classes and objects read models. Each call is also in unified_activity
    (the object's history), recorded by the dispatcher.

    Every change except the hourly sizes also goes out live: on "objects"
    (the list page) and, for one object, "objects:<object_id>" (its page),
    each message being the event's data plus its ``event_type``."""
    _project(classes, objects, event_type, data)
    if broadcaster is not None and event_type in _LIVE_TYPES:
        message = {"event_type": event_type, **data}
        broadcaster.broadcast(_WS_TOPIC, message)
        if data.get("object_id"):
            broadcaster.broadcast(f"{_WS_TOPIC}:{data['object_id']}", message)


def _project(
    classes: Optional[ObjectClassRepository], objects: Optional[ActiveObjectRepository],
    event_type: str, data: Dict[str, Any],
) -> None:
    if event_type == models.ACTIVE_OBJECT_CLASS_REGISTERED:
        if classes is not None:
            doc = {k: data.get(k) for k in _CLASS_FIELDS}
            doc["created_by"] = doc.pop("user_id")
            classes.save(doc)
        return
    if event_type == models.ACTIVE_OBJECT_CLASS_VERSION_DELETED:
        if classes is not None:
            classes.remove(data["class_name"], int(data["version"]))
        return
    if objects is None:
        return
    if event_type == models.ACTIVE_OBJECT_CREATED:
        objects.apply_created(data)
    elif event_type == models.ACTIVE_OBJECT_CALLED:
        objects.apply_called(data)
    elif event_type == models.ACTIVE_OBJECT_UPGRADED:
        objects.apply_upgraded(data)
    elif event_type == models.ACTIVE_OBJECT_DELETED:
        objects.apply_deleted(data)
    elif event_type == models.ACTIVE_OBJECT_STATE_SIZES_REPORTED:
        objects.apply_sizes(data.get("objects") or {}, data.get("stored_bytes"))
