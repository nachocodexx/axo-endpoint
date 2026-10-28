from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from axo_vem.domain.choreography.conditions import TAKEN

from axo_vem.domain.choreography.graph_ops import downstream_of, predecessors, sinks
from axo_vem.domain.choreography.run import (
    CANCELLED,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    NOT_TAKEN,
    PENDING,
    RUNNING,
    SKIPPED,
    STEP_DONE,
    SUCCEEDED,
    StepState,
)
from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyGraph

DELIVERED = "delivered"
BROKEN = "broken"


@dataclass
class Plan:
    start: List[str]      # every input arrived: run these now
    skip: List[str]       # can never get what they need
    not_taken: List[str] = field(default_factory=list)   # no followed arrow reaches them


def arrow_state(edge: ChoreographyEdge, steps: Dict[str, StepState], edge_states: Dict[str, str]) -> str:
    """delivered (its source finished well and it's followed), not_taken
    (a condition sent the run elsewhere) or broken (its source failed / was
    skipped / cancelled). An ``on_failure`` arrow is delivered when its
    source failed (or some of its files did) and not taken when it worked."""
    source = steps[edge.source_node_id]
    if edge.on_failure:
        if source.status == FAILED or (source.status == COMPLETED_WITH_ERRORS
                                       and any(i.status == FAILED for i in source.items)):
            return DELIVERED
        return NOT_TAKEN if source.status in (COMPLETED, COMPLETED_WITH_ERRORS, NOT_TAKEN) else BROKEN
    if source.status in SUCCEEDED:
        unconditional = edge.condition is None and not edge.otherwise
        followed = edge_states.get(edge.edge_id, TAKEN if unconditional else NOT_TAKEN) == TAKEN
        return DELIVERED if followed else NOT_TAKEN
    if source.status == NOT_TAKEN:
        return NOT_TAKEN
    return BROKEN


def next_steps(graph: ChoreographyGraph, steps: Dict[str, StepState],
               edge_states: Optional[Dict[str, str]] = None) -> Plan:
    """A step starts as soon as all the steps before it are done -- no waves.
    Arrows a condition didn't follow don't count (a step after an if / else
    runs with the branch that was taken). ``join = "all"`` needs every
    other arrow delivered (completed with errors counts: the files that
    worked go on); ``"arrived"`` needs at least one. A step that can't run
    is skipped, one no followed arrow reaches is not_taken, and so, in turn,
    are the ones after them."""
    states = edge_states or {}
    incoming: Dict[str, List[ChoreographyEdge]] = {n.node_id: [] for n in graph.nodes}
    for edge in graph.edges:
        incoming.setdefault(edge.target_node_id, []).append(edge)
    joins = {n.node_id: n.join for n in graph.nodes}
    plan = Plan(start=[], skip=[])
    for node in graph.nodes:
        if steps[node.node_id].status != PENDING:
            continue
        edges = incoming[node.node_id]
        if any(steps[e.source_node_id].status not in STEP_DONE for e in edges):
            continue
        arrived = [arrow_state(e, steps, states) for e in edges]
        delivered = arrived.count(DELIVERED)
        if not edges:
            plan.start.append(node.node_id)
        elif delivered and (BROKEN not in arrived or joins[node.node_id] == "arrived"):
            plan.start.append(node.node_id)
        elif BROKEN in arrived:
            plan.skip.append(node.node_id)
        else:
            plan.not_taken.append(node.node_id)
    return plan


def is_finished(steps: Dict[str, StepState]) -> bool:
    return all(s.status in STEP_DONE for s in steps.values())


def final_status(graph: ChoreographyGraph, steps: Dict[str, StepState], cancelled: bool,
                 allow_errors: bool = True, stopped: bool = False) -> str:
    """completed: every step did. completed_with_errors (when the
    choreography allows it, or every failed step was handled by an "on
    failure" arrow): something failed but at least one last step still
    finished. failed: no last step did, or an error ``stopped`` a run that
    doesn't allow errors."""
    if stopped:
        return FAILED
    if cancelled:
        return CANCELLED
    if all(s.status in (COMPLETED, NOT_TAKEN) for s in steps.values()):
        return COMPLETED
    handlers = {e.source_node_id for e in graph.edges if e.on_failure}
    handled = all(n in handlers for n, s in steps.items() if s.status == FAILED)
    if (allow_errors or handled) and any(steps[n].status in SUCCEEDED for n in sinks(graph)):
        return COMPLETED_WITH_ERRORS
    return FAILED


def to_resume(graph: ChoreographyGraph, steps: Dict[str, StepState]) -> Set[str]:
    # not_taken steps run again only when a step before them does (below)
    """The steps a resume runs again: the failed, skipped and cancelled ones,
    those with failed files (only those files run again) and everything
    after them (finished steps keep their outputs)."""
    again: Set[str] = set()
    for node_id, step in steps.items():
        if step.status in (FAILED, SKIPPED, CANCELLED, RUNNING, PENDING, COMPLETED_WITH_ERRORS):
            again.add(node_id)
            again |= downstream_of(graph, node_id)
    return again
