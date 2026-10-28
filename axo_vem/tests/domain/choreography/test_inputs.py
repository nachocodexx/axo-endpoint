import pytest

from axo_vem.domain.choreography.inputs import (
    DATA_REF_KEY,
    EVENT_OUTPUT_MAX_BYTES,
    ParamInfo,
    StepInputError,
    build_event,
    resolve_params,
    step_summary,
)
from axo_vem.domain.choreography.merge import STRATEGIES
from axo_vem.domain.events.models import ChoreographyNode, ParamMerge


def resolve(*args, **kwargs):
    return resolve_params(*args, **kwargs)[0]


def node(**kw):
    return ChoreographyNode(node_id="n", kind="function", function_id="f", **kw)


INFO = ParamInfo(names=["rows", "limit", "data"], required={"rows"})


def test_previous_json_fills_parameters_by_name_and_the_rest_stays_in_the_event():
    params = resolve(node(), INFO, {"a": {"rows": 120, "path": "x.csv"}})
    assert params == {"rows": 120}        # limit keeps the code's own default


def test_set_on_the_step_beats_the_previous_output():
    assert resolve(node(params={"rows": 5}), INFO, {"a": {"rows": 120}}) == {"rows": 5}


def rule(**kw):
    return ParamMerge(**kw)


OUTPUTS = {"a": {"rows": 1, "limit": 5}, "b": {"rows": 2, "limit": 7}}


def test_priority_by_connection_order_is_the_default():
    params, origins = resolve_params(node(), INFO, OUTPUTS)
    assert params == {"rows": 1, "limit": 5} and origins == {"rows": "a", "limit": "a"}
    only_b = {"a": {"limit": 5}, "b": {"rows": 2}}         # a didn't send rows: the next one is used
    assert resolve(node(), INFO, only_b) == {"rows": 2, "limit": 5}


def test_priority_with_an_order_and_from_one_step():
    assert resolve(node(merge={"*": rule(order=["b"])}), INFO, OUTPUTS) == {"rows": 2, "limit": 7}
    picked = node(merge={"rows": rule(strategy="from", source="b")})
    assert resolve(picked, INFO, OUTPUTS) == {"rows": 2, "limit": 5}


def test_from_a_step_that_did_not_send_it_leaves_it_missing():
    with pytest.raises(StepInputError) as err:
        resolve(node(merge={"rows": rule(strategy="from", source="b")}), INFO, {"a": {"rows": 1}})
    assert err.value.error_name == "CHOREOGRAPHY_STEP_PARAM_MISSING"


def test_random_is_the_same_for_the_same_run_and_step():
    n = node(merge={"*": rule(strategy="random")})
    many = {f"s{i}": {"rows": i} for i in range(8)}
    first = resolve(n, INFO, many, seed="run-1:n")
    assert all(resolve(n, INFO, many, seed="run-1:n") == first for _ in range(5))
    picks = {resolve(n, INFO, many, seed=f"run-{i}:n")["rows"] for i in range(40)}
    assert len(picks) > 1


def test_first_and_last_finished():
    finished = {"a": "2026-10-18T10:00:05+00:00", "b": "2026-10-18T10:00:01+00:00"}
    first = node(merge={"rows": rule(strategy="first_finished")})
    last = node(merge={"rows": rule(strategy="last_finished")})
    assert resolve(first, INFO, OUTPUTS, finished=finished)["rows"] == 2
    assert resolve(last, INFO, OUTPUTS, finished=finished)["rows"] == 1


def test_collect_as_dict_or_list_in_order():
    params, origins = resolve_params(node(merge={"rows": rule(strategy="collect")}), INFO, OUTPUTS)
    assert params["rows"] == {"a": 1, "b": 2} and origins["rows"] == ["a", "b"]
    as_list = node(merge={"rows": rule(strategy="collect", as_list=True, order=["b"])})
    assert resolve(as_list, INFO, OUTPUTS)["rows"] == [2, 1]


@pytest.mark.parametrize("reducer,expected", [("sum", 3), ("min", 1), ("max", 2), ("concat", [1, 2])])
def test_reduce(reducer, expected):
    assert resolve(node(merge={"rows": rule(strategy="reduce", reducer=reducer)}), INFO, OUTPUTS)["rows"] == expected


def test_reduce_failures():
    with pytest.raises(StepInputError) as err:
        resolve(node(merge={"rows": rule(strategy="reduce", reducer="all_equal")}), INFO, OUTPUTS)
    assert err.value.error_name == "CHOREOGRAPHY_STEP_PARAM_AMBIGUOUS"
    same = {"a": {"rows": 4}, "b": {"rows": 4}}
    assert resolve(node(merge={"rows": rule(strategy="reduce", reducer="all_equal")}), INFO, same)["rows"] == 4
    with pytest.raises(StepInputError) as err:
        resolve(node(merge={"rows": rule(strategy="reduce", reducer="sum")}), INFO, {"a": {"rows": "x"}, "b": {"rows": 1}})
    assert err.value.error_name == "CHOREOGRAPHY_STEP_PARAM_MERGE_FAILED"
    lists = {"a": {"rows": [1, 2]}, "b": {"rows": 3}}
    assert resolve(node(merge={"rows": rule(strategy="reduce", reducer="concat")}), INFO, lists)["rows"] == [1, 2, 3]


def test_none_takes_nothing_from_previous_steps():
    n = node(merge={"*": rule(strategy="none"), "rows": rule()})
    assert resolve(n, INFO, OUTPUTS) == {"rows": 1}


def test_origins_mark_step_and_data():
    _params, origins = resolve_params(node(params={"rows": 9}), INFO, OUTPUTS, data={"bucket": "b", "name": "f"})
    assert origins == {"rows": "step", "limit": "a", "data": "data"}


def test_every_strategy_is_described():
    names = [s["name"] for s in STRATEGIES]
    assert names == ["priority", "from", "random", "first_finished", "last_finished", "collect", "reduce", "none"]
    assert all(s["description"] for s in STRATEGIES)


def test_a_missing_required_parameter_fails():
    with pytest.raises(StepInputError) as err:
        resolve(node(), INFO, {"a": [1, 2, 3]})      # not a JSON object: fills nothing
    assert err.value.error_name == "CHOREOGRAPHY_STEP_PARAM_MISSING"


def test_unknown_signature_passes_every_key_and_a_bucket_file_comes_as_data():
    file = {"bucket": "b", "name": "x.csv", "version": 2}
    params = resolve(node(), ParamInfo(), {"a": {"x": 1, "y": 2}}, data=file)
    assert params == {"x": 1, "y": 2, "data": {DATA_REF_KEY: file}}


def test_method_infos_without_recorded_defaults_require_nothing():
    assert ParamInfo.from_method({"name": "add", "params": ["n"]}).required == set()
    info = ParamInfo.from_method({"name": "add", "params": ["n", "by"], "optional": ["by"]})
    assert info.required == {"n"}


def test_big_outputs_travel_as_a_link_and_the_event_holds_only_direct_previous_steps():
    ref = {"bucket": "axo/ve-1-runs", "name": "r/a.json", "version": 1}
    big = step_summary("function", {"p": 1}, None, ref, EVENT_OUTPUT_MAX_BYTES + 1, 5.0, [])
    assert big["output_ref"] == {DATA_REF_KEY: ref} and "output" not in big
    small = step_summary("function", {}, {"ok": True}, None, 10, 1.0, [])
    event = build_event(run={"run_id": "r"}, step="b", trigger=None, input={"x": 1}, previous={"a": small})
    assert event["previous"]["a"]["output"] == {"ok": True} and event["input"] == {"x": 1}
