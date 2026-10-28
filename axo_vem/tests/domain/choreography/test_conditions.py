import pytest

from axo_vem.domain.choreography.conditions import NOT_TAKEN, TAKEN, condition_problems, edge_states, matches
from axo_vem.domain.choreography.graph_ops import graph_problems
from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyGraph, ChoreographyNode, EdgeCondition

from tests.domain.choreography.test_graph_ops import bucket, fn


def cond(*rules, match="all"):
    return EdgeCondition.model_validate({"match": match, "rules": list(rules)})


def rule(field, op, value=None):
    return {"field": field, "op": op, "value": value}


OUT = {"rows": 120, "name": "sales", "tags": ["eu", "q1"], "stats": {"count": 3}, "items": [{"id": 7}],
       "empty": [], "flag": False}


@pytest.mark.parametrize("r,expected", [
    (rule("rows", ">", 0), True), (rule("rows", "<=", 100), False), (rule("rows", "==", 120), True),
    (rule("rows", "!=", 120), False), (rule("name", ">=", "a"), True),
    (rule("stats.count", "==", 3), True), (rule("items.0.id", "==", 7), True), (rule("items.5.id", "==", 7), False),
    (rule("name", "in", ["sales", "hr"]), True), (rule("name", "not_in", ["sales"]), False),
    (rule("tags", "contains", "eu"), True), (rule("name", "contains", "al"), True),
    (rule("stats", "contains", "count"), True), (rule("tags", "not_contains", "us"), True),
    (rule("rows", "exists"), True), (rule("nope", "exists"), False), (rule("nope", "not_exists"), True),
    (rule("flag", "is_false"), True), (rule("rows", "is_true"), True),
    (rule("empty", "is_empty"), True), (rule("tags", "not_empty"), True),
    (rule("nope", ">", 1), False),                       # a missing field never matches (but not_exists)
])
def test_rules(r, expected):
    assert matches(cond(r), OUT) is expected


def test_whole_output_and_groups():
    assert matches(cond(rule("", ">", 5)), 9)
    both = cond(rule("rows", ">", 0), {"match": "any", "rules": [rule("name", "==", "x"), rule("flag", "is_false")]})
    assert matches(both, OUT)
    assert not matches(cond(rule("rows", ">", 0), rule("name", "==", "x")), OUT)
    assert matches(cond(rule("rows", ">", 0), rule("name", "==", "x"), match="any"), OUT)


def test_values_that_cant_be_compared_are_false_with_a_warning():
    warnings = []
    assert not matches(cond(rule("name", ">", 3)), OUT, warnings)
    assert "can't be compared" in warnings[0]


def edge(i, a, b, **kw):
    return ChoreographyEdge(edge_id=i, source_node_id=a, target_node_id=b, **kw)


def test_otherwise_is_followed_only_when_no_condition_matched():
    edges = [edge("big", "c", "r", condition=cond(rule("rows", ">", 100))),
             edge("small", "c", "s", condition=cond(rule("rows", "<", 10))),
             edge("else", "c", "o", otherwise=True), edge("always", "c", "log")]
    assert edge_states(edges, {"rows": 120})[0] == {"big": TAKEN, "small": NOT_TAKEN, "else": NOT_TAKEN,
                                                    "always": TAKEN}
    assert edge_states(edges, {"rows": 50})[0] == {"big": NOT_TAKEN, "small": NOT_TAKEN, "else": TAKEN,
                                                   "always": TAKEN}


def test_condition_problems():
    nodes = {n.node_id: n for n in (fn("c"), fn("x"), fn("y"), bucket("b"))}
    problems = condition_problems([
        edge("e1", "b", "x", condition=cond(rule("rows", ">", 0))),
        edge("e2", "c", "x", condition=cond(rule("rows", ">", 0)), otherwise=True),
        edge("e3", "x", "y", otherwise=True),
        edge("e4", "c", "y", condition=cond(rule("a", "in", 3))),
        edge("e5", "y", "x", condition=cond({"rules": [{"rules": [{"rules": [rule("a", "exists")]}]}]})),
    ], nodes)
    assert any("only for arrows out of a function or object" in p for p in problems)
    assert any("has a condition and is also 'otherwise'" in p for p in problems)
    assert any("no other arrow out of x has a condition" in p for p in problems)
    assert any("'in' needs a list" in p for p in problems)
    assert any("at most 3 deep" in p for p in problems)
    g = ChoreographyGraph(nodes=list(nodes.values())[:3], edges=[edge("e3", "x", "y", otherwise=True)])
    assert any("otherwise" in p for p in graph_problems(g))
