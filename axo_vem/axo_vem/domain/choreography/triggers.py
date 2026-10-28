"""Triggers: "start when ..." -- an event starts a run of a choreography.

An event has the CloudEvents shape (``id``, ``type``, ``source``, ``time``,
``data``). ``file.written`` is the built-in one (a new file in a bucket);
people send their own types (``client.emit`` / ``POST /events``). Triggers
are saved with each version of the graph; only the newest version's are
active. A run a trigger starts acts for who added the trigger.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from axo_vem.domain.choreography.conditions import matches, rule_problems
from axo_vem.domain.events.models import (
    FILE_WRITTEN, RESERVED_EVENT_PREFIXES, ChoreographyGraph, ChoreographyTrigger, EdgeCondition,
)

EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)*$")
EVENT_TYPE_MAX = 100

# Built-in event types: what the Events page and the designer list first.
BUILT_IN_EVENT_TYPES: List[Dict[str, Any]] = [
    {"type": FILE_WRITTEN, "built_in": True,
     "description": "A new file (or a new version of one) was written in a bucket.",
     "fields": [{"name": "bucket", "type": "text", "description": "The bucket (owner/name)."},
                {"name": "name", "type": "text", "description": "The file's full name (folders/file.ext)."},
                {"name": "version", "type": "number", "description": "The file's version."},
                {"name": "format", "type": "text", "description": "How it was written (raw, json, csv, ...)."},
                {"name": "source", "type": "text",
                 "description": "What wrote it, when known (job:<ve>:<job id>, choreography:<id>)."}]},
]


def event_type_problem(event_type: str, built_in_ok: bool = True) -> Optional[str]:
    """None = a usable type name (``order.paid``, ``user-signed-up``)."""
    if built_in_ok and any(t["type"] == event_type for t in BUILT_IN_EVENT_TYPES):
        return None
    if not event_type or len(event_type) > EVENT_TYPE_MAX or not EVENT_TYPE_PATTERN.match(event_type):
        return (f"'{event_type}' isn't an event type name: lowercase letters, numbers, - and _, "
                "in parts joined by dots (e.g. order.paid)")
    if event_type.startswith(RESERVED_EVENT_PREFIXES):
        return f"'{event_type}': names starting with {', '.join(RESERVED_EVENT_PREFIXES)} are the platform's"
    return None


def trigger_id_for(trigger: ChoreographyTrigger) -> str:
    """A trigger saved without an id gets one from what it watches, so the
    same trigger saved again (e.g. from Python) keeps its id and creator."""
    key = json.dumps({"type": trigger.event_type, "bucket": trigger.bucket, "prefix": trigger.prefix or None,
                      "condition": trigger.condition.model_dump(mode="json") if trigger.condition else None},
                     sort_keys=True)
    return "tr-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def stamp(graph: ChoreographyGraph, previous: Optional[ChoreographyGraph], user_id: str,
          now: str) -> ChoreographyGraph:
    """Gives each trigger its id and creator: a trigger the previous version
    had keeps who added it (and when); a new one is the saver's."""
    if not graph.triggers:
        return graph
    before = {t.trigger_id: t for t in (previous.triggers if previous is not None else [])}
    stamped = []
    for trigger in graph.triggers:
        trigger_id = trigger.trigger_id or trigger_id_for(trigger)
        old = before.get(trigger_id)
        stamped.append(trigger.model_copy(update={
            "trigger_id": trigger_id, "prefix": trigger.prefix or None,
            "created_by": old.created_by if old is not None else user_id,
            "created_at": old.created_at if old is not None else now,
        }))
    return graph.model_copy(update={"triggers": stamped})


def trigger_problems(graph: ChoreographyGraph) -> List[str]:
    problems: List[str] = []
    nodes = {n.node_id: n for n in graph.nodes}
    targeted = {e.target_node_id for e in graph.edges}
    seen: Dict[str, int] = {}
    for index, trigger in enumerate(graph.triggers):
        where = f"trigger {trigger.trigger_id or index + 1}"
        if trigger.trigger_id:
            seen[trigger.trigger_id] = seen.get(trigger.trigger_id, 0) + 1
        problem = event_type_problem(trigger.event_type)
        if problem:
            problems.append(f"{where}: {problem}")
            continue
        if trigger.event_type == FILE_WRITTEN:
            if not (trigger.bucket or "").strip():
                problems.append(f"{where}: a 'file written' trigger needs a bucket")
        elif trigger.bucket or trigger.prefix:
            problems.append(f"{where}: only 'file written' triggers watch a bucket")
        for target in trigger.targets:
            node = nodes.get(target)
            if node is None:
                problems.append(f"{where}: there's no step {target}")
            elif target in targeted:
                problems.append(f"{where}: step {target} isn't a first step (an arrow already leads to it)")
            elif trigger.event_type == FILE_WRITTEN and (node.kind != "bucket" or node.bucket_name != trigger.bucket):
                problems.append(f"{where}: a 'file written' trigger starts the bucket step of {trigger.bucket}")
        if trigger.condition is not None:
            problems += rule_problems(trigger.condition, where)
    problems += [f"two triggers have the id {t}" for t, n in seen.items() if n > 1]
    return problems


def trigger_warnings(graph: ChoreographyGraph) -> List[str]:
    warnings: List[str] = []
    for trigger in graph.triggers:
        if trigger.event_type == FILE_WRITTEN and trigger.bucket and not any(
                n.kind == "bucket" and n.bucket_name == trigger.bucket for n in graph.nodes):
            warnings.append(f"the trigger on {trigger.bucket} starts the run, but no step reads that bucket: "
                            "add a bucket step for it to read the new file")
    return warnings


def trigger_matches(trigger: Dict[str, Any], event: Dict[str, Any]) -> bool:
    """Does this active trigger (a stored dict) start a run for this event?"""
    if trigger.get("event_type", FILE_WRITTEN) != event.get("type"):
        return False
    raw = event.get("data")
    data: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    if event.get("type") == FILE_WRITTEN:
        if trigger.get("bucket") != data.get("bucket") or \
                not str(data.get("name") or "").startswith(trigger.get("prefix") or ""):
            return False
    condition = trigger.get("condition")
    if condition:
        return matches(EdgeCondition.model_validate(condition), event.get("data"))
    return True
