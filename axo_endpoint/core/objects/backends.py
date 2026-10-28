"""Builds the leader's StateStore from the AXO_ENDPOINT_OBJECT_* settings."""

from __future__ import annotations

from typing import Any, Optional

from option import Err, Ok, Result

from axo_shared.active_objects.state import (
    AxoStorageValueStore,
    FilesystemValueStore,
    MemoryManifestStore,
    MemoryValueStore,
    MongoManifestStore,
    MongoValueStore,
    RedisValueStore,
    StatePolicy,
    StateStore,
    objects_bucket_name,
)
from axo_shared.active_objects.state.mongo import HOT, ensure_indexes

from axo_endpoint.core.errors import InvalidFieldError


def build_state_store(config: Any, storage_client: Optional[Any]) -> Result[StateStore, InvalidFieldError]:
    db = None
    if "mongo" in (config.AXO_ENDPOINT_OBJECT_MANIFESTS, config.AXO_ENDPOINT_OBJECT_HOT):
        if not config.AXO_ENDPOINT_OBJECT_MONGO_URI:
            return Err(_invalid("AXO_ENDPOINT_OBJECT_MONGO_URI", "is required for mongo"))
        import pymongo

        db = pymongo.MongoClient(config.AXO_ENDPOINT_OBJECT_MONGO_URI)[config.AXO_ENDPOINT_OBJECT_MONGO_DB]

    kind = config.AXO_ENDPOINT_OBJECT_MANIFESTS
    if kind == "memory":
        manifests = MemoryManifestStore()
    elif kind == "mongo":
        ensure_indexes(db)
        manifests = MongoManifestStore(db)
    else:
        return Err(_invalid("AXO_ENDPOINT_OBJECT_MANIFESTS", "must be memory or mongo"))

    kind = config.AXO_ENDPOINT_OBJECT_HOT
    if kind == "memory":
        hot = MemoryValueStore()
    elif kind == "mongo":
        hot = MongoValueStore(db, HOT)
    elif kind == "redis":
        if not config.AXO_ENDPOINT_OBJECT_REDIS_URI:
            return Err(_invalid("AXO_ENDPOINT_OBJECT_REDIS_URI", "is required for redis"))
        import redis

        hot = RedisValueStore(redis.Redis.from_url(config.AXO_ENDPOINT_OBJECT_REDIS_URI))
    else:
        return Err(_invalid("AXO_ENDPOINT_OBJECT_HOT", "must be memory, redis or mongo"))

    kind = config.AXO_ENDPOINT_OBJECT_BLOBS
    if kind == "memory":
        blobs = MemoryValueStore()
    elif kind == "filesystem":
        blobs = FilesystemValueStore(config.AXO_ENDPOINT_OBJECT_BLOB_DIR)
    elif kind == "axo_storage":
        if storage_client is None or not config.AXO_ENDPOINT_VIRTUAL_ENV_ID:
            return Err(_invalid("AXO_ENDPOINT_OBJECT_BLOBS",
                                "axo_storage needs AXO_ENDPOINT_STORAGE_URI and AXO_ENDPOINT_VIRTUAL_ENV_ID"))
        ve = config.AXO_ENDPOINT_VIRTUAL_ENV_ID
        blobs = AxoStorageValueStore(storage_client, f"endpoint:{ve}", objects_bucket_name(ve))
    else:
        return Err(_invalid("AXO_ENDPOINT_OBJECT_BLOBS", "must be memory, filesystem or axo_storage"))

    policy = StatePolicy(
        hot_max_bytes=config.AXO_ENDPOINT_OBJECT_HOT_MAX_BYTES,
        max_state_bytes=config.AXO_ENDPOINT_OBJECT_MAX_STATE_BYTES or None,
        keep_revisions=config.AXO_ENDPOINT_OBJECT_KEEP_REVISIONS,
        max_keep_revisions=config.AXO_ENDPOINT_OBJECT_MAX_KEEP_REVISIONS or None,
    )
    return Ok(StateStore(manifests, hot, blobs, policy))


def _invalid(field: str, why: str) -> InvalidFieldError:
    return InvalidFieldError(f"{field} {why}", context={"field": field})
