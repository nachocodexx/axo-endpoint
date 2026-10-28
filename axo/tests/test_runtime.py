import pytest

from axo import LocalContext
from axo.errors import AxoMethodNotFoundError
from axo.runtime import call_function, call_object, create_object, upgrade_object
from tests import samples


def test_creating_an_object_runs_init_and_returns_its_state():
    out = create_object(samples.Counter, [5])
    assert out.values == {"count": 5, "history": []} and out.result is None


def test_a_call_returns_its_result_and_every_change_including_in_place_ones():
    out = call_object(samples.Counter, "run", {"by": 2}, LocalContext(), {"count": 5, "history": [1]})
    assert out.result == 7 and out.values == {"count": 7, "history": [1, 2]}


def test_the_default_method_is_run_and_ctx_is_passed_when_asked():
    ctx = LocalContext()
    assert call_object(samples.Counter, None, {}, ctx, {"count": 1, "history": []}).result == 2
    out = call_object(samples.Counter, "reset", {}, ctx, {"count": 9, "history": []})
    assert out.result == "done" and out.values["count"] == 0 and ctx.warnings == ["reset"]


def test_a_method_only_reads_what_it_touches():
    asked = []

    def loader(name):
        asked.append(name)
        return 16

    out = call_object(samples.Counter, "sqrt", {}, None, {}, lazy=["count", "history"], loader=loader)
    assert out.result == 4.0 and asked == ["count"] and out.values == {"count": 16}


def test_unmarked_methods_cant_be_called():
    with pytest.raises(AxoMethodNotFoundError) as info:
        call_object(samples.Counter, "helper", {}, None, {})
    assert info.value.details["methods"] == ["reset", "run", "sqrt"]


def test_a_failing_method_raises_and_the_caller_saves_nothing():
    with pytest.raises(TypeError):
        call_object(samples.Counter, "run", {"by": "x"}, None, {"count": 1, "history": []})


def test_an_upgrade_runs_the_new_class_hook():
    out = upgrade_object(samples.Versioned, 1, None, {"count": 2})
    assert out.values == {"count": 2, "migrated_from": 1, "total": 20}
    plain = upgrade_object(samples.Counter, 1, None, {"count": 2})
    assert plain.values == {"count": 2}


def test_functions_get_named_parameters_and_ctx_when_asked():
    ctx = LocalContext()
    assert call_function(samples.add, {"a": 1, "b": 2}, ctx) == 3
    assert call_function(lambda x: x * 2, {"x": 4}, ctx) == 8
