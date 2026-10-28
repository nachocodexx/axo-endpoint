import pytest

from axo_shared.active_objects.state import codec


@pytest.mark.parametrize("value", [1, 2.5, "text", None, True, [1, "a"], {"a": {"b": [1]}}])
def test_small_json_values_stay_hot(value):
    entry, data = codec.encode(value, 4096).unwrap()
    assert (entry.tier, entry.kind) == ("hot", "json")
    assert codec.decode(entry, data).unwrap() == value


def test_big_json_values_go_to_blobs():
    entry, _ = codec.encode("x" * 100, 50).unwrap()
    assert (entry.tier, entry.kind) == ("blob", "json")


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, float("nan"), {1: "int key"}, bytearray(b"ab")])
def test_values_json_would_change_are_pickled(value):
    entry, data = codec.encode(value, 4096).unwrap()
    assert (entry.tier, entry.kind) == ("blob", "pickle")
    back = codec.decode(entry, data).unwrap()
    assert type(back) is type(value)


def test_bytes_are_kept_as_given_and_hashes_depend_on_the_kind():
    entry, data = codec.encode(b"1", 4096).unwrap()
    assert (entry.tier, entry.kind, data) == ("blob", "bytes", b"1")
    assert entry.hash != codec.encode(1, 4096).unwrap()[0].hash


def test_a_value_that_cant_be_pickled_is_refused():
    import threading
    err = codec.encode(threading.Lock(), 4096).unwrap_err()
    assert err.name == "ACTIVE_OBJECT_ATTRIBUTE_INVALID"
