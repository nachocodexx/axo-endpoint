"""Conditions on arrows: which arrows out of a finished step are followed.

Only simple comparisons on the step's output (no code), so they're safe to
run inside the VEM."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyNode, ConditionRule, EdgeCondition

TAKEN = "taken"
NOT_TAKEN = "not_taken"
MAX_DEPTH = 3

# What the designer offers, in this order. ``value``: what the value box takes.
OPERATORS: List[Dict[str, Any]] = [
    {"op": "==", "label": "is", "value": "any"},
    {"op": "!=", "label": "is not", "value": "any"},
    {"op": ">", "label": "greater than", "value": "number or text"},
    {"op": ">=", "label": "at least", "value": "number or text"},
    {"op": "<", "label": "less than", "value": "number or text"},
    {"op": "<=", "label": "at most", "value": "number or text"},
    {"op": "in", "label": "is one of", "value": "list"},
    {"op": "not_in", "label": "is none of", "value": "list"},
    {"op": "contains", "label": "contains", "value": "any",
     "description": "A list holding the value, a text holding it, or an object with that key."},
    {"op": "not_contains", "label": "doesn't contain", "value": "any"},
    {"op": "exists", "label": "exists", "value": None},
    {"op": "not_exists", "label": "doesn't exist", "value": None},
    {"op": "is_true", "label": "is true", "value": None, "description": "Not false, 0, empty or null."},
    {"op": "is_false", "label": "is false", "value": None, "description": "False, 0, empty or null."},
    {"op": "is_empty", "label": "is empty", "value": None, "description": "Null, \"\", [] or {}."},
    {"op": "not_empty", "label": "isn't empty", "value": None},
]
_LIST_OPS = frozenset({"in", "not_in"})
_ORDER_OPS = frozenset({">", ">=", "<", "<="})

_MISSING = object()


def field_value(output: Any, path: str) -> Any:
    """The value at ``path`` (dots; numbers index lists), or _MISSING."""
    value = output
    for part in [p for p in path.split(".") if p] if path else []:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.lstrip("-").isdigit() and -len(value) <= int(part) < len(value):
            value = value[int(part)]
        else:
            return _MISSING
    return value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rule(rule: ConditionRule, output: Any, warnings: List[str]) -> bool:
    actual = field_value(output, rule.field)
    where = rule.field or "the output"
    if rule.op == "exists":
        return actual is not _MISSING
    if rule.op == "not_exists":
        return actual is _MISSING
    if actual is _MISSING:
        return False
    op, expected = rule.op, rule.value
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op in _ORDER_OPS:
        if not ((_number(actual) and _number(expected)) or (isinstance(actual, str) and isinstance(expected, str))):
            warnings.append(f"{where} ({actual!r}) can't be compared with {expected!r}: taken as false")
            return False
        return {">": actual > expected, ">=": actual >= expected,
                "<": actual < expected, "<=": actual <= expected}[op]
    if op in _LIST_OPS:
        found = isinstance(expected, list) and actual in expected
        return found if op == "in" else not found
    if op in ("contains", "not_contains"):
        if isinstance(actual, str):
            found = isinstance(expected, str) and expected in actual
        elif isinstance(actual, (list, dict)):
            found = expected in actual if not isinstance(actual, dict) or isinstance(expected, str) else False
        else:
            warnings.append(f"{where} ({actual!r}) isn't a list, text or object: taken as false")
            return False
        return found if op == "contains" else not found
    if op == "is_true":
        return bool(actual)
    if op == "is_false":
        return not actual
    empty = actual in (None, "", [], {})
    return empty if op == "is_empty" else not empty


def matches(condition: EdgeCondition, output: Any, warnings: Optional[List[str]] = None) -> bool:
    found = warnings if warnings is not None else []
    results = (matches(r, output, found) if isinstance(r, EdgeCondition) else _rule(r, output, found)
               for r in condition.rules)
    return all(results) if condition.match == "all" else any(results)


def edge_states(edges: List[ChoreographyEdge], output: Any) -> Tuple[Dict[str, str], List[str]]:
    """The arrows out of one finished step -> taken / not_taken, and any
    warnings (a value that couldn't be compared)."""
    warnings: List[str] = []
    states: Dict[str, str] = {}
    edges = [e for e in edges if not e.on_failure]      # decided by the step's status, not its output
    for edge in edges:
        if edge.condition is not None:
            states[edge.edge_id] = TAKEN if matches(edge.condition, output, warnings) else NOT_TAKEN
    any_taken = any(state == TAKEN for state in states.values())
    for edge in edges:
        if edge.otherwise:
            states[edge.edge_id] = NOT_TAKEN if any_taken else TAKEN
        elif edge.condition is None:
            states[edge.edge_id] = TAKEN
    return states, warnings


def _depth(condition: EdgeCondition) -> int:
    return 1 + max((_depth(r) for r in condition.rules if isinstance(r, EdgeCondition)), default=0)


def _rules(condition: EdgeCondition) -> List[ConditionRule]:
    out: List[ConditionRule] = []
    for r in condition.rules:
        out.extend(_rules(r) if isinstance(r, EdgeCondition) else [r])
    return out


def condition_problems(edges: List[ChoreographyEdge], nodes: Dict[str, ChoreographyNode]) -> List[str]:
    problems: List[str] = []
    for edge in edges:
        source = nodes.get(edge.source_node_id)
        if source is None:
            continue
        where = f"the arrow {edge.source_node_id} → {edge.target_node_id}"
        if edge.on_failure:
            target = nodes.get(edge.target_node_id)
            if edge.condition is not None or edge.otherwise:
                problems.append(f"{where} is 'on failure': it can't also have a condition or be 'otherwise'")
            if source.kind == "bucket" or (target is not None and target.kind == "bucket"):
                problems.append(f"{where}: 'on failure' arrows go from a function or object step to another one")
            continue
        if edge.condition is None and not edge.otherwise:
            continue
        if source.kind == "bucket":
            problems.append(f"{where}: conditions are only for arrows out of a function or object step")
            continue
        if edge.condition is not None and edge.otherwise:
            problems.append(f"{where} has a condition and is also 'otherwise': pick one")
            continue
        if edge.otherwise:
            siblings = [e for e in edges if e.source_node_id == edge.source_node_id and e.condition is not None]
            if not siblings:
                problems.append(f"{where} is 'otherwise', but no other arrow out of {edge.source_node_id} "
                                "has a condition")
            continue
        problems += rule_problems(edge.condition, where)
    return problems


def rule_problems(condition: EdgeCondition, where: str) -> List[str]:
    """Shared by arrows and triggers: how deep groups go, list values."""
    problems: List[str] = []
    if _depth(condition) > MAX_DEPTH:
        problems.append(f"{where}: groups go at most {MAX_DEPTH} deep")
    for rule in _rules(condition):
        if rule.op in _LIST_OPS and not isinstance(rule.value, list):
            problems.append(f"{where}: '{rule.op}' needs a list of values")
    return problems
