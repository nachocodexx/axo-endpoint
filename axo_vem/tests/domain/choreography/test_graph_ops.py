import pytest

from axo_vem.domain.choreography.graph_ops import (
    downstream_of,
    graph_problems,
    predecessors,
    render_key,
    sinks,
    topological_order,
)
from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyGraph, ChoreographyNode, ParamMerge


def fn(node_id, **kw):
    return ChoreographyNode(node_id=node_id, kind="function", function_id=kw.pop("function_id", "f"), **kw)


def bucket(node_id, name="alice/b", **kw):
    return ChoreographyNode(node_id=node_id, kind="bucket", bucket_name=name, **kw)


def graph(nodes, *edges):
    return ChoreographyGraph(nodes=nodes, edges=[
        ChoreographyEdge(edge_id=f"e{i}", source_node_id=a, target_node_id=b) for i, (a, b) in enumerate(edges)])


def test_order_sinks_and_downstream():
    g = graph([fn("a"), fn("b"), fn("c"), fn("d")], ("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))
    assert topological_order(g) == ["a", "b", "c", "d"]
    assert sinks(g) == ["d"]
    assert downstream_of(g, "b") == {"d"}
    assert predecessors(g)["d"] == ["b", "c"]


def test_the_same_function_may_appear_in_several_steps():
    g = graph([fn("split"), fn("work-1", function_id="w"), fn("work-2", function_id="w")],
              ("split", "work-1"), ("split", "work-2"))
    assert graph_problems(g) == []


def test_a_loop_is_a_problem():
    g = graph([fn("a"), fn("b")], ("a", "b"), ("b", "a"))
    assert any("loop" in p for p in graph_problems(g))
    with pytest.raises(ValueError):
        topological_order(g)


def test_problems_are_plain_and_specific():
    g = graph([fn("a"), bucket("b1"), bucket("b2"), ChoreographyNode(node_id="o", kind="object")],
              ("a", "b1"), ("b1", "b2"))
    problems = graph_problems(g)
    assert any("bucket → bucket" in p for p in problems)
    g2 = graph([fn("a"), ChoreographyNode(node_id="o", kind="object"), bucket("x", name=None)], ("a", "o"))
    assert set(graph_problems(g2)) >= {"step o needs an object", "step x needs a bucket"}
    assert graph_problems(ChoreographyGraph()) == ["the choreography has no steps"]


def test_two_buckets_into_one_step_and_merge_rules_are_checked():
    rules = {"x": ParamMerge(strategy="from", source="b1"), "y": ParamMerge(strategy="from"),
             "z": ParamMerge(strategy="reduce"), "*": ParamMerge(order=["nope"])}
    g = graph([bucket("b1"), bucket("b2"), fn("a", merge=rules)], ("b1", "a"), ("b2", "a"))
    problems = graph_problems(g)
    assert any("more than one bucket" in p for p in problems)
    assert any("parameter x comes from b1" in p for p in problems)
    assert any("parameter y comes from one step, but no step is picked" in p for p in problems)
    assert any("parameter z is combined" in p for p in problems)
    assert any("the order for every parameter names nope" in p for p in problems)


def test_dangling_and_duplicate_edges():
    g = ChoreographyGraph(nodes=[fn("a"), fn("b")], edges=[
        ChoreographyEdge(edge_id="e1", source_node_id="a", target_node_id="zzz"),
        ChoreographyEdge(edge_id="e2", source_node_id="a", target_node_id="b"),
        ChoreographyEdge(edge_id="e3", source_node_id="a", target_node_id="b"),
    ])
    problems = graph_problems(g)
    assert any("doesn't exist" in p for p in problems) and any("connected twice" in p for p in problems)


def test_key_patterns():
    assert render_key("{choreography}/{run_id}/{source}-{item}.json",
                      {"choreography": "etl", "run_id": "r1", "source": "a", "item": "3"}) == "etl/r1/a-3.json"
    g = graph([fn("a"), bucket("b", key_pattern="{nope}/x")], ("a", "b"))
    assert any("unknown field" in p for p in graph_problems(g))
