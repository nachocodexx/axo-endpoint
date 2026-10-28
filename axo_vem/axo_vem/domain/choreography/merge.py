"""How a step picks a parameter several previous steps send (``ParamMerge``).

Every rule is fixed when the choreography is saved; ``priority`` in the
order the steps were connected is the default."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from axo_vem.domain.events.models import MERGE_ALL, ChoreographyNode, ParamMerge

DEFAULT_MERGE = ParamMerge()

# What the designer offers, in this order.
STRATEGIES: List[Dict[str, Any]] = [
    {"name": "priority", "label": "Priority", "same_every_run": True, "options": ["order"],
     "description": "The first step in the order that sent the value. The order is the one the steps were "
                    "connected in, unless you set it. The default."},
    {"name": "from", "label": "From one step", "same_every_run": True, "options": ["source"],
     "description": "Only from the step you pick, even when others send it too."},
    {"name": "random", "label": "Any of them", "same_every_run": False, "options": [],
     "description": "One of the steps that sent it, at random. A retry or resume of the same run picks the "
                    "same one."},
    {"name": "first_finished", "label": "First to finish", "same_every_run": False, "options": [],
     "description": "From the step that finished first. Can change from one run to the next."},
    {"name": "last_finished", "label": "Last to finish", "same_every_run": False, "options": [],
     "description": "From the step that finished last. Can change from one run to the next."},
    {"name": "collect", "label": "All of them", "same_every_run": True, "options": ["order", "as_list"],
     "description": "Every value: {step: value}, or a list in the order when 'as list' is on."},
    {"name": "reduce", "label": "Combine", "same_every_run": True, "options": ["reducer", "order"],
     "description": "One value made from all of them.",
     "reducers": [
         {"name": "sum", "description": "Adds the numbers."},
         {"name": "min", "description": "The smallest (numbers or texts)."},
         {"name": "max", "description": "The largest (numbers or texts)."},
         {"name": "concat", "description": "Joins the lists in the order (a value that isn't a list counts as "
                                           "one item)."},
         {"name": "all_equal", "description": "The value when they all agree; the step fails when they don't."},
     ]},
    {"name": "none", "label": "Don't pass it", "same_every_run": True, "options": [],
     "description": "Not taken from previous steps (it's still in the event). Set it on the step or leave the "
                    "code's default."},
]


class MergeError(Exception):
    """The values can't be merged with the step's rule. Not retried."""

    def __init__(self, error_name: str, message: str) -> None:
        super().__init__(message)
        self.error_name = error_name


def rule_for(node: ChoreographyNode, param: str) -> ParamMerge:
    return node.merge.get(param) or node.merge.get(MERGE_ALL) or DEFAULT_MERGE


def rule_problems(node: ChoreographyNode, senders: List[str]) -> List[str]:
    """What's wrong with a step's rules; ``senders`` = the process steps connected to it."""
    problems: List[str] = []
    where = f"step {node.node_id}"
    for param, rule in node.merge.items():
        what = "every parameter" if param == MERGE_ALL else f"parameter {param}"
        if rule.strategy == "from":
            if not rule.source:
                problems.append(f"{where}: {what} comes from one step, but no step is picked")
            elif rule.source not in senders:
                problems.append(f"{where}: {what} comes from {rule.source}, which isn't a process step "
                                "connected to it")
        if rule.strategy == "reduce" and not rule.reducer:
            problems.append(f"{where}: {what} is combined, but no way to combine is picked")
        unknown = [s for s in rule.order or [] if s not in senders]
        if unknown:
            problems.append(f"{where}: the order for {what} names {', '.join(unknown)}, "
                            "not process steps connected to it")
    return problems


def _ordered(sources: List[str], order: Optional[List[str]]) -> List[str]:
    """``sources`` (connection order), the ones named in ``order`` first."""
    first = [s for s in order or [] if s in sources]
    return first + [s for s in sources if s not in first]


def _pick_random(sources: List[str], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return sources[int.from_bytes(digest[:8], "big") % len(sources)]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _reduce(reducer: str, node_id: str, param: str, values: List[Any]) -> Any:
    where = f"step {node_id} combines {param!r}"
    if reducer == "all_equal":
        if any(v != values[0] for v in values[1:]):
            raise MergeError("CHOREOGRAPHY_STEP_PARAM_AMBIGUOUS",
                             f"{where} only when they all agree, and they don't")
        return values[0]
    if reducer == "concat":
        out: List[Any] = []
        for value in values:
            out.extend(value if isinstance(value, list) else [value])
        return out
    if reducer == "sum":
        if not all(_number(v) for v in values):
            raise MergeError("CHOREOGRAPHY_STEP_PARAM_MERGE_FAILED", f"{where} with a sum, but not all are numbers")
        return sum(values)
    if not (all(_number(v) for v in values) or all(isinstance(v, str) for v in values)):
        raise MergeError("CHOREOGRAPHY_STEP_PARAM_MERGE_FAILED",
                         f"{where} with {reducer}, but they aren't all numbers or all texts")
    return min(values) if reducer == "min" else max(values)


def merge_value(node: ChoreographyNode, param: str, by_source: Dict[str, Any], *,
                finished: Optional[Dict[str, Optional[str]]] = None, seed: str = "") -> Tuple[bool, Any, Any]:
    """``(found, value, origin)`` for one parameter. ``by_source``: the steps
    that sent it -> their value, in connection order. ``origin`` = the step
    (or steps) it came from, kept on the run so the canvas can show it."""
    rule = rule_for(node, param)
    if rule.strategy == "none" or not by_source:
        return False, None, None
    sources = list(by_source)
    if rule.strategy == "from":
        if rule.source in by_source:
            return True, by_source[rule.source], rule.source
        return False, None, None
    if rule.strategy == "priority":
        chosen = _ordered(sources, rule.order)[0]
        return True, by_source[chosen], chosen
    if rule.strategy == "random":
        chosen = _pick_random(sources, f"{seed}:{param}")
        return True, by_source[chosen], chosen
    if rule.strategy in ("first_finished", "last_finished"):
        times = finished or {}
        ranked = sorted(sources, key=lambda s: (times.get(s) or "", sources.index(s)))
        chosen = ranked[0] if rule.strategy == "first_finished" else ranked[-1]
        return True, by_source[chosen], chosen
    ordered = _ordered(sources, rule.order)
    if rule.strategy == "collect":
        value = [by_source[s] for s in ordered] if rule.as_list else {s: by_source[s] for s in ordered}
        return True, value, ordered
    return True, _reduce(rule.reducer or "all_equal", node.node_id, param, [by_source[s] for s in ordered]), ordered
