from axo_vem.domain.choreography.planning import final_status, next_steps, to_resume
from axo_vem.domain.choreography.run import (
    COMPLETED, COMPLETED_WITH_ERRORS, FAILED, NOT_TAKEN, PENDING, SKIPPED, StepState,
)
from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyGraph, EdgeCondition

from tests.domain.choreography.test_graph_ops import fn, graph


def states(**statuses):
    return {k: StepState(node_id=k, kind="function", status=v) for k, v in statuses.items()}


def test_a_step_starts_when_everything_before_it_is_done_not_by_waves():
    g = graph([fn("a"), fn("slow"), fn("b"), fn("c")], ("a", "b"), ("slow", "c"))
    plan = next_steps(g, states(a=COMPLETED, slow="running", b=PENDING, c=PENDING))
    assert plan.start == ["b"] and plan.skip == []


def test_join_all_skips_when_one_input_failed_and_arrived_runs_with_what_came():
    g = graph([fn("a"), fn("b"), fn("c"), fn("all"), fn("any", join="arrived")],
              ("a", "b"), ("a", "c"), ("b", "all"), ("c", "all"), ("b", "any"), ("c", "any"))
    plan = next_steps(g, states(a=COMPLETED, b=FAILED, c=COMPLETED, all=PENDING, any=PENDING))
    assert plan.start == ["any"] and plan.skip == ["all"]


def test_arrived_with_nothing_arrived_is_skipped():
    g = graph([fn("a"), fn("b"), fn("j", join="arrived")], ("a", "j"), ("b", "j"))
    assert next_steps(g, states(a=FAILED, b=SKIPPED, j=PENDING)).skip == ["j"]


def test_final_status():
    g = graph([fn("a"), fn("b"), fn("c")], ("a", "b"), ("a", "c"))
    assert final_status(g, states(a=COMPLETED, b=COMPLETED, c=COMPLETED), False) == "completed"
    assert final_status(g, states(a=COMPLETED, b=FAILED, c=COMPLETED), False) == "completed_with_errors"
    assert final_status(g, states(a=FAILED, b=SKIPPED, c=SKIPPED), False) == "failed"
    assert final_status(g, states(a=COMPLETED, b=COMPLETED, c=COMPLETED), True) == "cancelled"
    assert final_status(g, states(a=COMPLETED, b=FAILED, c=COMPLETED), False, allow_errors=False) == "failed"
    assert final_status(g, states(a=COMPLETED, b=FAILED, c=COMPLETED), True, stopped=True) == "failed"
    with_errors = states(a=COMPLETED_WITH_ERRORS, b=COMPLETED, c=COMPLETED)
    assert final_status(g, with_errors, False) == "completed_with_errors"
    assert next_steps(graph([fn("a"), fn("b")], ("a", "b")), states(a=COMPLETED_WITH_ERRORS, b=PENDING)).start == ["b"]
    assert to_resume(g, with_errors) == {"a", "b", "c"}


def test_resume_reruns_failed_and_everything_after_it():
    g = graph([fn("a"), fn("b"), fn("c"), fn("d")], ("a", "b"), ("b", "c"), ("a", "d"))
    assert to_resume(g, states(a=COMPLETED, b=FAILED, c=SKIPPED, d=COMPLETED)) == {"b", "c"}


def test_conditions_decide_which_branch_runs_and_the_join_after_it_still_runs():
    c = EdgeCondition.model_validate({"rules": [{"field": "rows", "op": ">", "value": 0}]})
    nodes = graph([fn("count"), fn("report"), fn("alert"), fn("end")]).nodes
    g = ChoreographyGraph(nodes=nodes, edges=[
        ChoreographyEdge(edge_id="yes", source_node_id="count", target_node_id="report", condition=c),
        ChoreographyEdge(edge_id="no", source_node_id="count", target_node_id="alert", otherwise=True),
        ChoreographyEdge(edge_id="r", source_node_id="report", target_node_id="end"),
        ChoreographyEdge(edge_id="a", source_node_id="alert", target_node_id="end")])
    taken = {"yes": "taken", "no": "not_taken"}
    plan = next_steps(g, states(count=COMPLETED, report=PENDING, alert=PENDING, end=PENDING), taken)
    assert plan.start == ["report"] and plan.not_taken == ["alert"]
    plan = next_steps(g, states(count=COMPLETED, report=COMPLETED, alert=NOT_TAKEN, end=PENDING), taken)
    assert plan.start == ["end"]                                   # the branch not taken doesn't hold it
    plan = next_steps(g, states(count=COMPLETED, report=FAILED, alert=NOT_TAKEN, end=PENDING), taken)
    assert plan.skip == ["end"]
    only_branch = ChoreographyGraph(nodes=nodes[:3], edges=g.edges[:2])
    plan = next_steps(only_branch, states(count=COMPLETED, report=PENDING, alert=PENDING), {"yes": "not_taken",
                                                                                         "no": "taken"})
    assert plan.start == ["alert"] and plan.not_taken == ["report"]
    done = states(count=COMPLETED, report=COMPLETED, alert=NOT_TAKEN, end=COMPLETED)
    assert final_status(g, done, False) == "completed"
