import types

import fakeredis
import mongomock
import pytest

from axo_shared.active_objects.state import MongoManifestStore, RedisValueStore, FilesystemValueStore

from axo_endpoint.core.objects.backends import build_state_store


def _config(**overrides):
    base = dict(
        AXO_ENDPOINT_OBJECT_MANIFESTS="memory", AXO_ENDPOINT_OBJECT_HOT="memory", AXO_ENDPOINT_OBJECT_BLOBS="memory",
        AXO_ENDPOINT_OBJECT_MONGO_URI="", AXO_ENDPOINT_OBJECT_MONGO_DB="axo_objects",
        AXO_ENDPOINT_OBJECT_REDIS_URI="", AXO_ENDPOINT_OBJECT_BLOB_DIR="/tmp/x",
        AXO_ENDPOINT_OBJECT_HOT_MAX_BYTES=100, AXO_ENDPOINT_OBJECT_MAX_STATE_BYTES=0,
        AXO_ENDPOINT_OBJECT_KEEP_REVISIONS=3, AXO_ENDPOINT_OBJECT_MAX_KEEP_REVISIONS=0,
        AXO_ENDPOINT_VIRTUAL_ENV_ID="",
    )
    return types.SimpleNamespace(**{**base, **overrides})


def test_memory_by_default_with_the_policy_from_config():
    store = build_state_store(_config(), None).unwrap()
    assert store.policy.hot_max_bytes == 100 and store.policy.max_state_bytes is None
    assert store.policy.keep_revisions == 3 and store.policy.max_keep_revisions is None


def test_mongo_redis_and_files(monkeypatch, tmp_path):
    import pymongo
    import redis

    monkeypatch.setattr(pymongo, "MongoClient", mongomock.MongoClient)
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url: fakeredis.FakeRedis()))
    store = build_state_store(_config(
        AXO_ENDPOINT_OBJECT_MANIFESTS="mongo", AXO_ENDPOINT_OBJECT_HOT="redis", AXO_ENDPOINT_OBJECT_BLOBS="filesystem",
        AXO_ENDPOINT_OBJECT_MONGO_URI="mongodb://x", AXO_ENDPOINT_OBJECT_REDIS_URI="redis://x",
        AXO_ENDPOINT_OBJECT_BLOB_DIR=str(tmp_path)), None).unwrap()
    assert isinstance(store._manifests, MongoManifestStore) and isinstance(store._tiers["hot"], RedisValueStore)
    assert isinstance(store._tiers["blob"], FilesystemValueStore)
    store.commit("o1", 0, {"a": 1, "b": b"bytes"}).unwrap()
    assert store.load("o1").unwrap().get("b").unwrap() == b"bytes"


@pytest.mark.parametrize("overrides, field", [
    ({"AXO_ENDPOINT_OBJECT_MANIFESTS": "mongo"}, "AXO_ENDPOINT_OBJECT_MONGO_URI"),
    ({"AXO_ENDPOINT_OBJECT_HOT": "redis"}, "AXO_ENDPOINT_OBJECT_REDIS_URI"),
    ({"AXO_ENDPOINT_OBJECT_BLOBS": "axo_storage"}, "AXO_ENDPOINT_OBJECT_BLOBS"),
    ({"AXO_ENDPOINT_OBJECT_HOT": "s3"}, "AXO_ENDPOINT_OBJECT_HOT"),
])
def test_bad_settings_are_named(overrides, field):
    assert build_state_store(_config(**overrides), None).unwrap_err().context["field"] == field
