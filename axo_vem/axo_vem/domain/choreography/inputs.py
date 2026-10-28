from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from axo_vem.domain.choreography.merge import MergeError, merge_value
from axo_vem.domain.events.models import ChoreographyNode

# The parameter a file coming from a bucket step arrives in.
DATA_PARAM = "data"
# Outputs bigger than this travel as a link (``output_ref``), not in the event.
EVENT_OUTPUT_MAX_BYTES = 1024 * 1024
# Marks a link to a saved file, so the axo library turns it into a lazy reference.
DATA_REF_KEY = "__axo_data__"


class StepInputError(Exception):
    """The step can't be called: a parameter is missing or can't be merged. Not retried."""

    def __init__(self, error_name: str, message: str) -> None:
        super().__init__(message)
        self.error_name = error_name


@dataclass
class ParamInfo:
    """What a function or method takes. ``names`` None = not known (a plain
    ``fn(params, ctx)`` without a schema): every key of a previous output is
    passed. ``required`` = no default."""

    names: Optional[List[str]] = None
    required: Set[str] = field(default_factory=set)
    defaults: Dict[str, Any] = field(default_factory=dict)     # the code's own, when known

    @classmethod
    def from_schema(cls, schema: Optional[List[Dict[str, Any]]]) -> "ParamInfo":
        if not schema:
            return cls()
        return cls(names=[p["name"] for p in schema],
                   required={p["name"] for p in schema
                             if p.get("required", True) and p.get("default") is None},
                   defaults={p["name"]: p["default"] for p in schema if p.get("default") is not None})

    @classmethod
    def from_method(cls, method: Optional[Dict[str, Any]]) -> "ParamInfo":
        if method is None:
            return cls()
        names = list(method.get("params") or [])
        optional = set(method.get("optional") or []) | set((method.get("defaults") or {}).keys())
        # A class registered before defaults were recorded: nothing is known to be required.
        required = set(names) - optional if "optional" in method else set()
        return cls(names=names, required=required, defaults=dict(method.get("defaults") or {}))


def json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def resolve_params(node: ChoreographyNode, info: ParamInfo, outputs: Dict[str, Any],
                   data: Union[Dict[str, Any], List[Dict[str, Any]], None] = None, *, finished: Optional[Dict[str, Optional[str]]] = None,
                   seed: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The parameters a step is called with, and where each came from.
    ``outputs``: previous step -> its output, in connection order (only JSON
    objects fill parameters); ``finished``: when each finished; ``seed``:
    the run and step, so ``random`` picks the same again. Priority: set on
    the step > ``data`` (the file from a bucket step, or the list of its
    files for an ``all_at_once`` step) > previous outputs
    (picked with the step's ``merge`` rules) > the code's default (left out
    here, so the code's own default applies)."""
    offered: Dict[str, Dict[str, Any]] = {}
    for source, output in outputs.items():
        if not isinstance(output, dict):
            continue
        for key, value in output.items():
            if info.names is None or key in info.names:
                offered.setdefault(key, {})[source] = value

    params: Dict[str, Any] = {}
    origins: Dict[str, Any] = {}
    for key, by_source in offered.items():
        if key in node.params:
            continue
        try:
            found, value, origin = merge_value(node, key, by_source, finished=finished, seed=seed)
        except MergeError as exc:
            raise StepInputError(exc.error_name, str(exc)) from exc
        if found:
            params[key], origins[key] = value, origin
    if data is not None and (info.names is None or DATA_PARAM in info.names):
        params[DATA_PARAM] = ([{DATA_REF_KEY: f} for f in data] if isinstance(data, list)
                              else {DATA_REF_KEY: data})
        origins[DATA_PARAM] = "data"
    params.update(node.params)
    origins.update({key: "step" for key in node.params})

    missing = sorted(info.required - set(params))
    if missing:
        raise StepInputError(
            "CHOREOGRAPHY_STEP_PARAM_MISSING",
            f"step {node.node_id} needs {', '.join(missing)} and nothing gave "
            f"{'it' if len(missing) == 1 else 'them'} a value")
    return params, origins


def step_summary(node_kind: str, params: Dict[str, Any], output: Any, output_ref: Optional[Dict[str, Any]],
                 output_bytes: int, duration_ms: Optional[float], files: List[Dict[str, Any]],
                 status: str = "completed") -> Dict[str, Any]:
    """What the event says about one previous step (``status``
    completed_with_errors: its output holds only the files that worked)."""
    summary: Dict[str, Any] = {"kind": node_kind, "status": status, "output_bytes": output_bytes,
                               "duration_ms": duration_ms}
    if node_kind == "bucket":
        summary["files"] = files
        return summary
    summary["params"] = params
    if output_ref is not None and output_bytes > EVENT_OUTPUT_MAX_BYTES:
        summary["output_ref"] = {DATA_REF_KEY: output_ref}
    else:
        summary["output"] = output
    return summary


def build_event(*, run: Dict[str, Any], step: str, trigger: Optional[Dict[str, Any]], input: Any,
                previous: Dict[str, Dict[str, Any]],
                file: Union[Dict[str, Any], List[Dict[str, Any]], None] = None) -> Dict[str, Any]:
    """The event a step receives (``ctx.event`` / ``self.event``): the run,
    what started it, the input given by hand, and the DIRECT previous steps
    (their params, output or a link to it when over 1 MB, sizes and time).
    The whole history stays in the run record, so events don't grow along
    a chain. ``file`` (one call per file) or ``files`` (all at once)."""
    event: Dict[str, Any] = {"run": run, "step": step, "trigger": trigger, "input": input, "previous": previous}
    if isinstance(file, list):
        event["files"] = file
    elif file is not None:
        event["file"] = file
    return event
