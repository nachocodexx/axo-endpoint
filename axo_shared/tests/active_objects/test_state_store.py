import fakeredis
import mongomock
import pytest
from option import Err

from axo_shared.active_objects.state import (
    FilesystemValueStore,
    MemoryManifestStore,
    MemoryValueStore,
    MongoManifestStore,
    MongoValueStore,
    RedisValueStore,
    StatePolicy,
    StateStore,
)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _memory(tmp_path):
    return MemoryManifestStore(), MemoryValueStore(), MemoryValueStore()


def _mongo_redis_files(tmp_path):
    db = mongomock.MongoClient().db
    return MongoManifestStore(db), RedisValueStore(fakeredis.FakeRedis()), FilesystemValueStore(str(tmp_path))


def _mongo_only(tmp_path):
    db = mongomock.MongoClient().db
    return MongoManifestStore(db), MongoValueStore(db), MongoValueStore(db, "blobs")


@pytest.fixture(params=[_memory, _mongo_redis_files, _mongo_only], ids=["memory", "mongo+redis+files", "mongo"])
def backends(request, tmp_path):
    return request.param(tmp_path)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(backends, clock):
    manifests, hot, blobs = backends
    return StateStore(manifests, hot, blobs, StatePolicy(hot_max_bytes=64), now_fn=clock)


def test_a_new_object_loads_empty_and_saves_both_tiers(store):
    state = store.load("o1").unwrap()
    assert state.revision == 0 and state.names() == []
    m = store.commit("o1", 0, {"count": 1, "rows": list(range(100)), "raw": b"\x00" * 10, "pair": (1, 2)}).unwrap()
    assert m.revision == 1
    assert {k: (e.tier, e.kind) for k, e in m.attributes.items()} == {
        "count": ("hot", "json"), "rows": ("blob", "json"), "raw": ("blob", "bytes"), "pair": ("blob", "pickle")}
    state = store.load("o1").unwrap()
    assert state.is_loaded("count") and not state.is_loaded("rows")      # blobs load on first read
    assert state.to_dict().unwrap() == {"count": 1, "rows": list(range(100)), "raw": b"\x00" * 10, "pair": (1, 2)}


def test_changes_and_deletes_build_on_the_revision_read(store):
    store.commit("o1", 0, {"a": 1, "b": 2})
    m = store.commit("o1", 1, {"a": 10}, deleted=["b"]).unwrap()
    assert m.revision == 2 and store.load("o1").unwrap().to_dict().unwrap() == {"a": 10}


def test_saving_on_an_old_revision_is_a_conflict_and_changes_nothing(store):
    store.commit("o1", 0, {"a": 1})
    store.commit("o1", 1, {"a": 2})
    err = store.commit("o1", 1, {"a": 3}).unwrap_err()
    assert err.name == "ACTIVE_OBJECT_STATE_CONFLICT" and err.context["revision"] == 2
    assert store.load("o1").unwrap().get("a").unwrap() == 2


def test_two_leaders_racing_on_one_revision_only_one_wins(backends, clock):
    manifests, hot, blobs = backends
    a = StateStore(manifests, hot, blobs, now_fn=clock)
    b = StateStore(manifests, hot, blobs, now_fn=clock)
    a.commit("o1", 0, {"n": 0})
    # b read revision 1 before a saved revision 2: the swap refuses it.
    assert a.commit("o1", 1, {"n": 1}).is_ok
    assert b.commit("o1", 1, {"n": 99}).unwrap_err().name == "ACTIVE_OBJECT_STATE_CONFLICT"
    assert a.load("o1").unwrap().get("n").unwrap() == 1


def test_a_failed_save_leaves_no_trace(store):
    import threading
    store.commit("o1", 0, {"a": 1})
    err = store.commit("o1", 1, {"a": 2, "lock": threading.Lock()}).unwrap_err()
    assert err.name == "ACTIVE_OBJECT_ATTRIBUTE_INVALID" and err.context["attribute"] == "lock"
    state = store.load("o1").unwrap()
    assert state.revision == 1 and state.to_dict().unwrap() == {"a": 1}


@pytest.mark.parametrize("name", ["_private", "event", "store", "not valid", "1x"])
def test_reserved_and_private_names_are_refused(store, name):
    assert store.commit("o1", 0, {name: 1}).unwrap_err().name == "ACTIVE_OBJECT_ATTRIBUTE_INVALID"


def test_the_size_cap_is_checked_before_anything_is_saved(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, StatePolicy(max_state_bytes=50), now_fn=clock)
    store.commit("o1", 0, {"a": "x" * 10})
    err = store.commit("o1", 1, {"b": b"y" * 60}).unwrap_err()
    assert err.name == "ACTIVE_OBJECT_STATE_TOO_LARGE" and err.context["limit"] == 50
    assert store.load("o1").unwrap().names() == ["a"]
    assert store.stored_bytes().unwrap() == 12                         # '"xxxxxxxxxx"'


def test_an_unchanged_save_keeps_the_revision(store):
    store.commit("o1", 0, {"a": 1})
    assert store.commit("o1", 1, {"a": 1}).unwrap().revision == 1


def test_equal_values_are_stored_and_billed_once(store):
    big = b"z" * 1000
    store.commit("o1", 0, {"data": big, "copy": big})
    store.commit("o2", 0, {"data": big})
    assert store.stored_bytes().unwrap() == 1000


def test_a_clone_shares_values_and_then_changes_on_its_own(store):
    store.commit("o1", 0, {"a": 1, "data": b"d" * 500})
    clone = store.clone("o1", "o2").unwrap()
    assert clone.revision == 1 and store.stored_bytes().unwrap() == 500 + 1
    store.commit("o2", 1, {"a": 2})
    assert store.load("o1").unwrap().get("a").unwrap() == 1
    assert store.load("o2").unwrap().to_dict().unwrap() == {"a": 2, "data": b"d" * 500}
    assert store.clone("o1", "o2").unwrap_err().name == "ACTIVE_OBJECT_EXISTS"
    assert store.clone("nope", "o3").unwrap_err().name == "ACTIVE_OBJECT_NOT_FOUND"


def test_unused_values_are_swept_after_the_grace_time_only(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, StatePolicy(keep_revisions=0), now_fn=clock)
    store.commit("o1", 0, {"data": b"old"})
    old = store.manifest("o1").unwrap().attributes["data"].hash
    store.commit("o1", 1, {"data": b"new"})
    assert store.sweep(grace_seconds=60).unwrap() == 0 and blobs.exists(old).unwrap()
    clock.t += 61
    assert store.sweep(grace_seconds=60).unwrap() == 1
    assert not blobs.exists(old).unwrap()
    assert store.load("o1").unwrap().get("data").unwrap() == b"new"


def test_a_value_used_again_before_the_sweep_is_kept(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, now_fn=clock)
    store.commit("o1", 0, {"data": b"v"})
    store.drop("o1")
    clock.t += 3600
    store.commit("o2", 0, {"data": b"v"})                              # same value, new object
    assert store.sweep(grace_seconds=60).unwrap() == 0
    assert store.load("o2").unwrap().get("data").unwrap() == b"v"


def test_a_save_waits_for_a_sweep_deleting_its_value_then_writes_it_again(clock):
    manifests, hot, blobs = MemoryManifestStore(), MemoryValueStore(), MemoryValueStore()
    store = StateStore(manifests, hot, blobs, now_fn=clock, sleep_fn=lambda s: None)
    store.commit("o1", 0, {"data": b"v"})
    h = store.manifest("o1").unwrap().attributes["data"].hash
    store.drop("o1")
    clock.t += 7200
    (claimed,) = manifests.claim_unreferenced(clock() - 60, "sweeper", 10).unwrap()
    blobs.delete(h)                                                    # the sweep is half way
    calls = []

    def sleep(_):                                                      # ... and finishes while the save waits
        calls.append(1)
        manifests.finish_sweep(h, "sweeper")

    store._sleep = sleep
    store.commit("o2", 0, {"data": b"v"}).unwrap()
    assert calls and blobs.exists(h).unwrap()
    assert store.load("o2").unwrap().get("data").unwrap() == b"v"


def test_a_missing_value_reads_as_unreadable_not_as_none(store, backends):
    _, _, blobs = backends
    store.commit("o1", 0, {"data": b"x"})
    blobs.delete(store.manifest("o1").unwrap().attributes["data"].hash)
    assert store.load("o1").unwrap().get("data").unwrap_err().name == "ACTIVE_OBJECT_STATE_UNREADABLE"


def test_backend_failures_come_back_as_errors(clock):
    class Down(MemoryValueStore):
        def put(self, hash, data):
            from axo_shared.errors import ActiveObjectStateBackendError
            return Err(ActiveObjectStateBackendError("down"))

    manifests = MemoryManifestStore()
    store = StateStore(manifests, MemoryValueStore(), Down(), now_fn=clock)
    assert store.commit("o1", 0, {"data": b"x"}).unwrap_err().name == "ACTIVE_OBJECT_STATE_BACKEND_FAILURE"
    assert store.manifest("o1").unwrap() is None and store.stored_bytes().unwrap() == 0


def test_blobs_in_a_platform_bucket():
    from option import Ok

    from axo_shared.active_objects.state import AxoStorageValueStore, objects_bucket_name
    from axo_shared.errors import ObjectNotFoundError

    class FakeClient:
        def __init__(self):
            self.objects, self.calls = {}, []

        def upload(self, user, bucket, name, data, format="raw"):
            self.calls.append((user, bucket))
            self.objects[name] = data
            return Ok({"name": name, "version": 1})

        def download(self, user, bucket, name):
            return Ok(self.objects[name]) if name in self.objects else Err(ObjectNotFoundError(name))

        def info(self, user, bucket, name):
            return Ok({"name": name}) if name in self.objects else Err(ObjectNotFoundError(name))

        def delete(self, user, bucket, name):
            return Ok({}) if self.objects.pop(name, None) is not None else Err(ObjectNotFoundError(name))

    client = FakeClient()
    bucket = objects_bucket_name("VE_1")
    assert bucket == "axo/ve-ve-1-objects"
    store = StateStore(MemoryManifestStore(), MemoryValueStore(),
                       AxoStorageValueStore(client, "endpoint:ve-1", bucket))
    store.commit("o1", 0, {"data": b"bytes"})
    assert client.calls == [("endpoint:ve-1", bucket)]
    store.commit("o2", 0, {"data": b"bytes"})                         # already there: no second upload
    assert len(client.calls) == 1
    assert store.load("o2").unwrap().get("data").unwrap() == b"bytes"
    blobs = AxoStorageValueStore(client, "endpoint:ve-1", bucket)
    assert blobs.get("f" * 64).unwrap() is None and blobs.delete("f" * 64).is_ok


def test_past_revisions_are_kept_loaded_and_restored(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, StatePolicy(keep_revisions=2), now_fn=clock)
    for n in range(4):                                                 # revisions 1..4
        store.commit("o1", n, {"n": n, "data": bytes([n]) * 100})
    assert [m.revision for m in store.revisions("o1").unwrap()] == [4, 3, 2]
    assert store.load("o1", revision=2).unwrap().to_dict().unwrap() == {"n": 1, "data": b"\x01" * 100}
    assert store.load("o1", revision=1).unwrap_err().name == "ACTIVE_OBJECT_STATE_UNREADABLE"
    # Kept revisions hold their values (billed); revision 1's went with it.
    assert store.stored_bytes().unwrap() == 3 * (100 + 1)
    restored = store.restore("o1", revision=2, base_revision=4).unwrap()
    assert restored.revision == 5 and store.load("o1").unwrap().get("n").unwrap() == 1
    assert store.restore("o1", revision=2, base_revision=4).unwrap_err().name == "ACTIVE_OBJECT_STATE_CONFLICT"


def test_an_object_can_keep_fewer_revisions_than_the_plan(store):
    for n in range(3):
        store.commit("o1", n, {"n": n}, keep_revisions=1)
    assert [m.revision for m in store.revisions("o1").unwrap()] == [3, 2]


def test_dropping_an_object_releases_its_past_too(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, now_fn=clock)
    store.commit("o1", 0, {"data": b"a"})
    store.commit("o1", 1, {"data": b"b"})
    assert store.stored_bytes().unwrap() == 2
    store.drop("o1")
    assert store.stored_bytes().unwrap() == 0 and store.revisions("o1").unwrap() == []
    clock.t += 7200
    assert store.sweep(grace_seconds=60).unwrap() == 2


def test_values_encoded_elsewhere_are_checked_and_saved(store):
    from axo_shared.active_objects.models import AttributeEntry
    from axo_shared.active_objects.state import codec

    good = codec.encode({"k": 1}, 4096).unwrap()
    m = store.commit_encoded("o1", 0, {"conf": good}).unwrap()
    assert m.attributes["conf"].tier == "hot" and store.load("o1").unwrap().get("conf").unwrap() == {"k": 1}
    big = codec.encode("y" * 200, 4096).unwrap()                       # hot for the sender, too big for this plan
    assert store.commit_encoded("o1", 1, {"text": big}).unwrap().attributes["text"].tier == "blob"
    entry, data = good
    damaged = (entry, data + b" ")
    assert store.commit_encoded("o1", 2, {"x": damaged}).unwrap_err().name == "ACTIVE_OBJECT_STATE_UNREADABLE"
    assert store.commit_encoded("o1", 2, {"_x": good}).unwrap_err().name == "ACTIVE_OBJECT_ATTRIBUTE_INVALID"
    store.commit("o2", 0, {"a": b"12345"})
    assert store.sizes().unwrap() == {"o1": m.size_bytes + big[0].size, "o2": 5}


def test_the_plan_caps_how_many_revisions_an_object_may_ask_for(backends, clock):
    manifests, hot, blobs = backends
    store = StateStore(manifests, hot, blobs, StatePolicy(keep_revisions=2, max_keep_revisions=3), now_fn=clock)
    for n in range(6):
        store.commit("o1", n, {"n": n}, keep_revisions=50)             # asks for 50, the plan allows 3
    assert [m.revision for m in store.revisions("o1").unwrap()] == [6, 5, 4, 3]


def test_limits_sent_with_an_operation_override_the_launch_policy():
    base = StatePolicy(hot_max_bytes=4096, max_state_bytes=None, keep_revisions=10)
    assert base.with_limits(None) is base
    sent = base.with_limits({"hot_max_bytes": 64, "max_state_bytes": 1000, "keep_revisions": 3,
                             "max_keep_revisions": 5})
    assert sent == StatePolicy(hot_max_bytes=64, max_state_bytes=1000, keep_revisions=3, max_keep_revisions=5)
    assert base.with_limits({"max_state_bytes": 0}).max_state_bytes is None    # 0 = no cap
    assert base.with_limits({"hot_max_bytes": 64}).keep_revisions == 10        # missing = kept
