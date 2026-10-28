import pytest

from axo.core.axo import bind, methods_of, state_of
from tests.samples import Counter


def test_an_object_works_as_plain_python():
    c = Counter(5)
    assert c.run(by=2) == 7 and c.count == 7 and c.object_name is None and c.event is None


def test_callable_methods_are_run_and_the_marked_ones():
    methods = methods_of(Counter)
    assert sorted(methods) == ["reset", "run", "sqrt"]
    assert methods["run"].params == ["by"] and not methods["run"].wants_context
    assert methods["reset"].params == [] and methods["reset"].wants_context


def test_state_is_the_public_attributes_and_deletions_are_tracked():
    c = Counter(1)
    c._scratch = "never saved"
    del c.history
    values, deleted = state_of(c)
    assert values == {"count": 1} and deleted == {"history"}
    c.history = []
    assert state_of(c)[1] == set()


def test_saved_values_not_sent_along_are_fetched_on_first_read():
    fetched = []

    def loader(name):
        fetched.append(name)
        return [1, 2, 3]

    c = bind(Counter.__new__(Counter), {"count": 3}, lazy=["history"], loader=loader, identity=("o1", "visits"))
    assert state_of(c)[0] == {"count": 3}                     # not fetched: not part of what comes back
    assert c.history == [1, 2, 3] and c.history == [1, 2, 3]
    assert fetched == ["history"] and c.object_name == "visits" and c.object_id == "o1"
    assert state_of(c)[0] == {"count": 3, "history": [1, 2, 3]}


def test_deleting_a_value_never_fetched_needs_no_fetch():
    c = bind(Counter.__new__(Counter), {}, lazy=["history"], loader=lambda n: pytest.fail("fetched"))
    del c.history
    assert state_of(c) == ({}, {"history"})


def test_platform_names_cant_be_assigned():
    c = Counter()
    for name in ("event", "store", "ctx"):
        with pytest.raises(AttributeError):
            setattr(c, name, 1)


def test_a_missing_attribute_still_raises():
    with pytest.raises(AttributeError):
        Counter().nope
