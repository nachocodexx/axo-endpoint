from __future__ import annotations

import re
from typing import Dict, List, Set

from axo_vem.domain.choreography.conditions import condition_problems
from axo_vem.domain.choreography.merge import rule_problems
from axo_vem.domain.events.models import ChoreographyGraph, ChoreographyNode

PROCESS_KINDS = frozenset({"function", "object", "choreography"})
# What a bucket step's key pattern may use.
KEY_FIELDS = frozenset({"choreography", "run_id", "step", "source", "item", "date"})
DEFAULT_KEY_PATTERN = "{choreography}/{run_id}/{source}.json"
_FIELD = re.compile(r"{([^{}]*)}")


def is_process(node: ChoreographyNode) -> bool:
    return node.kind in PROCESS_KINDS


def predecessors(graph: ChoreographyGraph) -> Dict[str, List[str]]:
    """node_id -> the steps sending to it, in edge order."""
    preds: Dict[str, List[str]] = {n.node_id: [] for n in graph.nodes}
    for edge in graph.edges:
        if edge.source_node_id not in preds.setdefault(edge.target_node_id, []):
            preds[edge.target_node_id].append(edge.source_node_id)
    return preds


def successors(graph: ChoreographyGraph) -> Dict[str, List[str]]:
    succs: Dict[str, List[str]] = {n.node_id: [] for n in graph.nodes}
    for edge in graph.edges:
        if edge.target_node_id not in succs.setdefault(edge.source_node_id, []):
            succs[edge.source_node_id].append(edge.target_node_id)
    return succs


def downstream_of(graph: ChoreographyGraph, node_id: str) -> Set[str]:
    """Every step reachable forward from ``node_id`` (not itself)."""
    succs = successors(graph)
    seen: Set[str] = set()
    frontier = list(succs.get(node_id, []))
    while frontier:
        current = frontier.pop()
        if current not in seen:
            seen.add(current)
            frontier.extend(succs.get(current, []))
    return seen


def sinks(graph: ChoreographyGraph) -> List[str]:
    """The steps nothing comes after: their outputs are the run's result."""
    succs = successors(graph)
    return [n.node_id for n in graph.nodes if not succs.get(n.node_id)]


def topological_order(graph: ChoreographyGraph) -> List[str]:
    """Raises ValueError naming the steps on a cycle."""
    preds = {k: set(v) for k, v in predecessors(graph).items()}
    order: List[str] = []
    done: Set[str] = set()
    while preds:
        ready = sorted(k for k, deps in preds.items() if deps <= done)
        if not ready:
            raise ValueError(f"the steps {', '.join(sorted(preds))} form a loop")
        for node_id in ready:
            order.append(node_id)
            done.add(node_id)
            del preds[node_id]
    return order


def key_pattern_problem(pattern: str) -> str:
    unknown = sorted({f for f in _FIELD.findall(pattern) if f not in KEY_FIELDS})
    if unknown:
        return f"unknown field(s) {', '.join('{' + f + '}' for f in unknown)} (use {', '.join(sorted(KEY_FIELDS))})"
    if not pattern.strip() or pattern.startswith("/"):
        return "it must not be empty or start with /"
    return ""


def render_key(pattern: str, values: Dict[str, str]) -> str:
    return _FIELD.sub(lambda m: str(values.get(m.group(1), "")), pattern)


def graph_problems(graph: ChoreographyGraph) -> List[str]:
    """Everything that makes the graph unusable, in plain words (empty = fine).
    Whether the functions / objects named exist is checked elsewhere (it
    needs the VE's catalog)."""
    problems: List[str] = []
    if not graph.nodes:
        return ["the choreography has no steps"]
    by_id: Dict[str, ChoreographyNode] = {}
    for node in graph.nodes:
        if node.node_id in by_id:
            problems.append(f"two steps are called {node.node_id}")
        by_id[node.node_id] = node

    seen_edges: Set[tuple] = set()
    for edge in graph.edges:
        source, target = by_id.get(edge.source_node_id), by_id.get(edge.target_node_id)
        if source is None or target is None:
            problems.append(f"connection {edge.edge_id} points at a step that doesn't exist")
            continue
        if source.node_id == target.node_id:
            problems.append(f"step {source.node_id} is connected to itself")
        if (source.node_id, target.node_id) in seen_edges:
            problems.append(f"steps {source.node_id} and {target.node_id} are connected twice")
        seen_edges.add((source.node_id, target.node_id))
        if source.kind == "bucket" and target.kind == "bucket":
            problems.append(f"bucket → bucket ({source.node_id} → {target.node_id}) isn't available yet")
    if problems:
        return problems

    try:
        topological_order(graph)
    except ValueError as exc:
        problems.append(str(exc))

    preds = predecessors(graph)
    for node in graph.nodes:
        where = f"step {node.node_id}"
        if node.kind == "function" and not node.function_id:
            problems.append(f"{where} needs a function")
        if node.kind == "object" and not node.object_name:
            problems.append(f"{where} needs an object")
        if node.kind == "choreography" and not node.choreography_id:
            problems.append(f"{where} needs a choreography to call")
        if node.kind == "bucket":
            if not node.bucket_name:
                problems.append(f"{where} needs a bucket")
            if node.key_pattern:
                problem = key_pattern_problem(node.key_pattern)
                if problem:
                    problems.append(f"{where}: its key pattern has {problem}")
            continue
        bucket_inputs = [p for p in preds[node.node_id] if by_id[p].kind == "bucket"]
        if len(bucket_inputs) > 1:
            problems.append(f"{where} gets files from more than one bucket ({', '.join(bucket_inputs)}); "
                            "connect only one")
        problems.extend(rule_problems(node, [p for p in preds[node.node_id] if is_process(by_id[p])]))
    problems.extend(condition_problems(graph.edges, by_id))
    return problems
