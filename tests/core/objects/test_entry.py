import base64

from axo_shared.active_objects.models import AttributeEntry
from axo_shared.active_objects.state import codec

from axo_endpoint.core.runtime.entry import OBJECT_OUTCOME, OBJECT_PARAM, run_object_operation
from tests.core.objects.object_samples import Ledger


def _spec(op, **extra):
    return {OBJECT_PARAM: {"op": op, "object_id": "o1", "name": "acme", "hot_max_bytes": 64, **extra}}


def test_create_returns_every_value_encoded():
    out = run_object_operation(Ledger, _spec("create", kwargs={"owner": "ana"}), ctx=None)[OBJECT_OUTCOME]
    assert set(out["changed"]) == {"owner", "entries", "blob"}
    entry = AttributeEntry.from_dict(out["changed"]["owner"]["entry"])
    assert codec.decode(entry, base64.b64decode(out["changed"]["owner"]["data"])).unwrap() == "ana"
    assert out["deleted"] == []


def test_a_call_sends_back_only_what_changed_and_reads_big_values_on_demand():
    blob_entry, blob_data = codec.encode(b"y" * 100, 64).unwrap()
    owner_entry, _ = codec.encode("ana", 64).unwrap()
    entries_entry, _ = codec.encode([1], 64).unwrap()
    fetched = []

    def fetch(name):
        fetched.append(name)
        return blob_data

    def params(method, **extra):
        return {**extra, **_spec("call", method=method, values={"owner": "ana", "entries": [1]},
                                 lazy={"blob": blob_entry.to_dict()},
                                 hashes={"owner": owner_entry.hash, "entries": entries_entry.hash,
                                         "blob": blob_entry.hash})}

    out = run_object_operation(Ledger, params("run", amount=4), None, fetch=fetch)[OBJECT_OUTCOME]
    assert out["result"] == 5 and set(out["changed"]) == {"entries"} and fetched == []

    out = run_object_operation(Ledger, params("blob_size"), None, fetch=fetch)[OBJECT_OUTCOME]
    assert out["result"] == 100 and out["changed"] == {} and fetched == ["blob"]


def test_deleting_an_attribute_is_reported():
    owner_entry, _ = codec.encode("ana", 64).unwrap()
    out = run_object_operation(Ledger, _spec("call", method="forget", values={"owner": "ana"},
                                              hashes={"owner": owner_entry.hash}), None)[OBJECT_OUTCOME]
    assert out["deleted"] == ["owner"]
