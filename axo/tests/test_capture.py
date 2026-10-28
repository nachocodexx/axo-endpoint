import pytest

from axo import Axo, axo_function
from axo.core.axo import methods_of
from axo.core.capture import capture, load
from tests import samples


def test_a_class_is_sent_as_its_source_with_the_module_imports():
    code = capture(samples.Counter).unwrap()
    assert code.format == "source" and code.kind == "class"
    assert "import math" in code.text and "class Counter(Axo):" in code.text and "UsesGlobal" not in code.text
    cls = load(code).unwrap()
    assert cls is not samples.Counter and sorted(methods_of(cls)) == ["reset", "run", "sqrt"]
    c = cls(16)
    assert c.square_root() == 4.0


def test_the_hash_only_changes_with_the_code():
    assert capture(samples.Counter).unwrap().code_hash == capture(samples.Counter).unwrap().code_hash
    assert capture(samples.Counter).unwrap().code_hash != capture(samples.UsesGlobal).unwrap().code_hash


def test_code_using_module_globals_is_pickled_instead():
    code = capture(samples.UsesGlobal).unwrap()
    assert code.format == "pickle"
    assert load(code).unwrap()().run() == 3
    fn = capture(samples.scale).unwrap()
    assert fn.format == "pickle" and load(fn).unwrap()(x=2) == 6
    # what the platform's Code tab shows for it (the pickle itself is never downloadable)
    assert "Sent as a pickle" in fn.display and "def scale" in fn.display


def test_a_function_keeps_its_decorator_and_options():
    code = capture(samples.add).unwrap()
    assert code.format == "source" and "@axo_function(requirements=[\"numpy\"]" in code.text
    loaded = load(code).unwrap()
    assert loaded.options.max_concurrency == 4 and loaded(a=1, b=2) == 3


def test_things_defined_inside_a_function_are_pickled():
    def make():
        class Inner(Axo):
            def run(self):
                return "inner"
        return Inner

    code = capture(make()).unwrap()
    assert code.format == "pickle" and load(code).unwrap()().run() == "inner"


def test_a_renamed_function_is_pickled_so_its_name_is_kept():
    def original(x):
        return x

    named = axo_function(original, name="renamed")
    assert capture(named).unwrap().format == "pickle"


def test_other_things_are_refused():
    with pytest.raises(Exception):
        capture(3).unwrap()
    assert capture(Axo).unwrap_err().name == "ACTIVE_OBJECT_CLASS_INVALID"
