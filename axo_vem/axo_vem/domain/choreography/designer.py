"""What the choreography designer shows: every setting of a step, an arrow
and the choreography, with a plain description (the node / arrow dialogs
are built from this). ``kinds``: the step kinds a setting is for; ``for``:
the event types a trigger setting is for."""

from __future__ import annotations

from typing import Any, Dict, List

from axo_vem.domain.choreography.conditions import MAX_DEPTH, OPERATORS
from axo_vem.domain.choreography.merge import STRATEGIES
from axo_vem.domain.choreography.triggers import BUILT_IN_EVENT_TYPES
from axo_vem.domain.events.models import (
    FILE_WRITTEN, ChoreographyEdge, ChoreographyNode, ChoreographySettings, ChoreographyTrigger,
)

_PROCESS = ["function", "object", "choreography"]
_JOBS = ["function", "object"]             # one job per call (a choreography step runs a whole run)
_ALL = ["function", "object", "choreography", "bucket"]


def _field(model, name: str) -> Dict[str, Any]:
    info = model.model_fields[name]
    out: Dict[str, Any] = {}
    default = info.get_default(call_default_factory=True)
    if isinstance(default, (str, int, float, bool, list, dict)) or default is None:
        out["default"] = default
    for meta in info.metadata:
        for key in ("ge", "le", "gt"):
            if getattr(meta, key, None) is not None:
                out[{"ge": "min", "le": "max", "gt": "above"}[key]] = getattr(meta, key)
    choices = _choices(info.annotation)
    if choices:
        out["choices"] = choices
    return out


def _choices(annotation: Any) -> List[str]:
    """A Literal's values, also inside Optional[...] (None = "use the default")."""
    args = getattr(annotation, "__args__", None) or ()
    if args and all(isinstance(c, str) for c in args):
        return list(args)
    inner = [a for a in args if a is not type(None)]
    return _choices(inner[0]) if len(inner) == 1 else []


def _settings(model, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**row, **_field(model, row["name"])} for row in rows]


STEP_SETTINGS: List[Dict[str, Any]] = _settings(ChoreographyNode, [
    {"name": "label", "kinds": _ALL, "type": "text", "description": "A name shown on the canvas."},
    {"name": "function_id", "kinds": ["function"], "type": "function", "description": "The function it calls."},
    {"name": "function_version", "kinds": ["function"], "type": "version",
     "description": "A fixed version of the function; empty = its newest one when the run starts."},
    {"name": "object_name", "kinds": ["object"], "type": "object", "description": "The object it calls."},
    {"name": "method", "kinds": ["object"], "type": "method", "description": "The method it calls; empty = run."},
    {"name": "choreography_id", "kinds": ["choreography"], "type": "choreography",
     "description": "The choreography it calls: each call starts a run of it, linked to this run. Its parameters "
                    "are that choreography's inputs; its answer is that run's answer."},
    {"name": "choreography_version", "kinds": ["choreography"], "type": "version",
     "description": "A fixed version of that choreography; empty = its newest one when this run starts."},
    {"name": "params", "kinds": _PROCESS, "type": "params",
     "description": "Values set on the step. They win over everything that arrives from earlier steps."},
    {"name": "merge", "kinds": _PROCESS, "type": "merge",
     "description": "How a parameter several earlier steps send is picked, for each parameter or all (*)."},
    {"name": "join", "kinds": _PROCESS, "type": "choice",
     "description": "all: runs when every step before it finished well. arrived: runs with whatever arrived "
                    "once the others are done, if at least one did."},
    {"name": "max_attempts", "kinds": _JOBS, "type": "number",
     "description": "How many times the step's job is tried in all when its code fails (the first try "
                    "included); empty = the function's own setting. Platform failures are tried again on "
                    "their own, free."},
    {"name": "retry_backoff", "kinds": _JOBS, "type": "choice",
     "description": "How long it waits between tries: fixed (always the same) or exponential (doubling)."},
    {"name": "retry_delay_seconds", "kinds": _JOBS, "type": "seconds",
     "description": "The first wait before trying again."},
    {"name": "timeout_seconds", "kinds": _PROCESS, "type": "seconds",
     "description": "The longest one call may take (a choreography step: its whole run, then cancelled); "
                    "empty = the VE's limit."},
    {"name": "max_wait", "kinds": _PROCESS, "type": "seconds",
     "description": "How long a call may wait for a free runner (a choreography step: for a free run slot "
                    "of that choreography, then it fails as too busy): -1 forever, 0 not at all; empty = the "
                    "VE's default (a choreography step: until its time limit)."},
    {"name": "files_mode", "kinds": _PROCESS, "type": "choice",
     "description": "After a bucket with several files: per_file calls it once per file, all_at_once calls it "
                    "once with the list of every file (a choreography step: one run per file, or one run "
                    "starting with every file)."},
    {"name": "parallelism", "kinds": _PROCESS, "type": "number",
     "description": "With per_file: how many files run at the same time."},
    {"name": "bucket_name", "kinds": ["bucket"], "type": "bucket", "description": "The bucket."},
    {"name": "key_pattern", "kinds": ["bucket"], "type": "text",
     "description": "Where an earlier step's output is saved. {choreography} {run_id} {step} {source} {item} "
                    "{date} are filled in.",
     "placeholder": "{choreography}/{run_id}/{source}.json"},
    {"name": "selected_items", "kinds": ["bucket"], "type": "files",
     "description": "The files a run started by hand reads; empty = every file (a trigger reads its own file)."},
    {"name": "save_result", "kinds": ["bucket"], "type": "switch",
     "description": "After a step: also save the step's JSON result as a file. The files the step's job wrote "
                    "into this bucket during the run are passed on either way."},
])

ARROW_SETTINGS: List[Dict[str, Any]] = _settings(ChoreographyEdge, [
    {"name": "condition", "type": "condition",
     "description": "The arrow is followed only when the earlier step's output matches. Rules are joined by "
                    f"all (and) or any (or), and a rule can be a group ({MAX_DEPTH} levels at most)."},
    {"name": "otherwise", "type": "switch",
     "description": "Followed only when none of the earlier step's arrows with a condition was."},
    {"name": "on_failure", "type": "switch",
     "description": "Followed only when the earlier step fails (or some of its files do): the next step gets "
                    "the error instead of an output, and the failure counts as handled -- it doesn't stop a "
                    "run that stops at its first error. Never with a condition, and only between function / "
                    "object steps."},
])

TRIGGER_SETTINGS: List[Dict[str, Any]] = _settings(ChoreographyTrigger, [
    {"name": "event_type", "type": "event_type",
     "description": "What starts a run: a new file in a bucket (file.written) or an event someone sends "
                    "(client.emit / POST /events), e.g. order.paid."},
    {"name": "bucket", "type": "bucket", "for": [FILE_WRITTEN],
     "description": "The bucket whose new files start a run; its bucket step reads the new file."},
    {"name": "prefix", "type": "text", "for": [FILE_WRITTEN], "placeholder": "incoming/",
     "description": "Only files whose name starts with this (a folder, or the start of a name)."},
    {"name": "condition", "type": "condition",
     "description": "Only events whose data matches these rules (like an arrow's: a field of the data, "
                    "a comparison, a value)."},
    {"name": "targets", "type": "steps",
     "description": "The first steps it's drawn connected to. The event's data fills their parameters by "
                    "name; the whole event is ctx.event / self.event."},
])

CHOREOGRAPHY_SETTINGS: List[Dict[str, Any]] = _settings(ChoreographySettings, [
    {"name": "max_parallel_runs", "type": "number", "description": "How many runs may go at the same time."},
    {"name": "queue_limit", "type": "number",
     "description": "How many more runs may wait in line; past it a new run is refused."},
    {"name": "allow_completed_with_errors", "type": "switch",
     "description": "On: a failed file or branch doesn't stop the rest, and the run can end completed with "
                    "errors. Off: the first error fails the whole run."},
    {"name": "keep_results", "type": "choice",
     "description": "How long its steps' answers are kept (each step's job result); empty = the VE's default. "
                    "A run can be resumed while the answers it needs are kept."},
    {"name": "answer_step", "type": "step",
     "description": "When another choreography calls this one: the last step whose answer is the call's "
                    "answer. Empty: the only last step's; with several, every last step's answer by step."},
])

# Fields only the canvas or the platform sets (not shown as settings).
INTERNAL_FIELDS = {"node_id", "kind", "position", "function_name", "choreography_name", "edge_id", "source_node_id", "target_node_id",
                   "trigger_id", "created_by", "created_at"}


def designer() -> Dict[str, Any]:
    return {"step_settings": STEP_SETTINGS, "arrow_settings": ARROW_SETTINGS,
            "trigger_settings": TRIGGER_SETTINGS, "event_types": BUILT_IN_EVENT_TYPES,
            "choreography_settings": CHOREOGRAPHY_SETTINGS, "merge_strategies": STRATEGIES,
            "condition_operators": OPERATORS}
