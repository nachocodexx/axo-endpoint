"""Mongo backends: the manifests and reference counts (always Mongo in a
real VE: each VE has its own), and optionally the hot tier.

Takes a pymongo ``Database`` (or mongomock's); axo_shared doesn't depend on
pymongo itself, so errors are caught broadly and returned as
``ActiveObjectStateBackendError``."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set, TypeVar

from option import Err, Ok, Result

from axo_shared.active_objects.models import AttributeEntry, StateManifest
from axo_shared.active_objects.state.ports import ManifestStore, SweepItem, ValueStore
from axo_shared.errors import ActiveObjectStateBackendError, AxoError

MANIFESTS = "object_manifests"
PAST = "object_manifest_history"
REFS = "object_state_refs"
HOT = "object_state_hot"

T = TypeVar("T")


def _call(what: str, fn: Callable[[], T]) -> Result[T, AxoError]:
    try:
        return Ok(fn())
    except Exception as exc:  # pymongo raises many kinds; all mean "the store failed"
        return Err(ActiveObjectStateBackendError(f"Mongo {what} failed: {exc}", {"backend": "mongo"}))


def _is_duplicate_key(exc: Exception) -> bool:
    return type(exc).__name__ == "DuplicateKeyError"


def ensure_indexes(db: Any) -> None:
    db[REFS].create_index([("count", 1), ("sweep", 1), ("released_at", 1)])
    db[PAST].create_index([("object_id", 1), ("revision", -1)])


class MongoManifestStore(ManifestStore):
    def __init__(self, db: Any) -> None:
        self._manifests = db[MANIFESTS]
        self._past = db[PAST]
        self._refs = db[REFS]

    def get(self, object_id: str) -> Result[Optional[StateManifest], AxoError]:
        doc = _call("read", lambda: self._manifests.find_one({"_id": object_id}))
        if doc.is_err:
            return doc
        return Ok(StateManifest.from_dict(doc.unwrap()) if doc.unwrap() else None)

    def swap(self, manifest: StateManifest, expected_revision: int) -> Result[bool, AxoError]:
        doc = {"_id": manifest.object_id, **manifest.to_dict()}
        if expected_revision == 0:
            try:
                self._manifests.insert_one(doc)
                return Ok(True)
            except Exception as exc:
                if _is_duplicate_key(exc):
                    return Ok(False)
                return Err(ActiveObjectStateBackendError(f"Mongo write failed: {exc}", {"backend": "mongo"}))
        done = _call("write", lambda: self._manifests.replace_one(
            {"_id": manifest.object_id, "revision": expected_revision}, doc))
        return done.map(lambda r: r.matched_count == 1)

    def remove(self, object_id: str, expected_revision: int) -> Result[bool, AxoError]:
        done = _call("delete", lambda: self._manifests.delete_one({"_id": object_id, "revision": expected_revision}))
        return done.map(lambda r: r.deleted_count == 1)

    def archive(self, manifest: StateManifest) -> Result[None, AxoError]:
        doc = {"_id": f"{manifest.object_id}:{manifest.revision}", **manifest.to_dict()}
        return _call("write", lambda: self._past.replace_one({"_id": doc["_id"]}, doc, upsert=True)).map(
            lambda _: None)

    def past(self, object_id: str) -> Result[List[StateManifest], AxoError]:
        docs = _call("read", lambda: list(self._past.find({"object_id": object_id}).sort("revision", -1)))
        return docs.map(lambda ds: [StateManifest.from_dict(d) for d in ds])

    def forget_past(self, object_id: str, revisions: Iterable[int]) -> Result[None, AxoError]:
        ids = [f"{object_id}:{r}" for r in revisions]
        return _call("delete", lambda: self._past.delete_many({"_id": {"$in": ids}})).map(lambda _: None)

    def add_refs(self, entries: Dict[str, AttributeEntry]) -> Result[None, AxoError]:
        for h, e in entries.items():
            done = _call("write", lambda h=h, e=e: self._refs.update_one(
                {"_id": h},
                {"$inc": {"count": 1},
                 "$setOnInsert": {"tier": e.tier, "size": e.size, "released_at": None, "sweep": None}},
                upsert=True))
            if done.is_err:
                return done
        return Ok(None)

    def release_refs(self, hashes: Iterable[str], now: float) -> Result[None, AxoError]:
        ids = list(hashes)
        if not ids:
            return Ok(None)
        return _call("write", lambda: self._refs.update_many(
            {"_id": {"$in": ids}}, {"$inc": {"count": -1}, "$set": {"released_at": now}})).map(lambda _: None)

    def being_swept(self, hashes: Iterable[str]) -> Result[Set[str], AxoError]:
        ids = list(hashes)
        found = _call("read", lambda: list(self._refs.find({"_id": {"$in": ids}, "sweep": {"$ne": None}}, {"_id": 1})))
        return found.map(lambda docs: {d["_id"] for d in docs})

    def claim_unreferenced(self, released_before: float, token: str, limit: int) -> Result[List[SweepItem], AxoError]:
        unused = {"count": {"$lte": 0}, "sweep": None, "released_at": {"$lt": released_before}}
        found = _call("read", lambda: list(self._refs.find(unused, {"_id": 1}).limit(limit)))
        if found.is_err:
            return found
        out: List[SweepItem] = []
        for d in found.unwrap():
            claimed = _call("write", lambda d=d: self._refs.find_one_and_update(
                {**unused, "_id": d["_id"]}, {"$set": {"sweep": token}}))
            if claimed.is_err:
                return claimed
            doc = claimed.unwrap()
            if doc is not None:                   # another sweep may have claimed it first
                out.append(SweepItem(hash=doc["_id"], tier=doc["tier"], size=int(doc["size"])))
        return Ok(out)

    def finish_sweep(self, hash: str, token: str) -> Result[bool, AxoError]:
        deleted = _call("delete", lambda: self._refs.delete_one({"_id": hash, "sweep": token, "count": {"$lte": 0}}))
        if deleted.is_err:
            return deleted
        if deleted.unwrap().deleted_count == 1:
            return Ok(True)
        return _call("write", lambda: self._refs.update_one(
            {"_id": hash, "sweep": token}, {"$set": {"sweep": None}})).map(lambda _: False)

    def sizes(self) -> Result[Dict[str, int], AxoError]:
        docs = _call("read", lambda: list(self._manifests.find({}, {"size_bytes": 1})))
        return docs.map(lambda ds: {d["_id"]: int(d.get("size_bytes") or 0) for d in ds})

    def stored_bytes(self) -> Result[int, AxoError]:
        rows = _call("read", lambda: list(self._refs.aggregate([
            {"$match": {"count": {"$gt": 0}}}, {"$group": {"_id": None, "total": {"$sum": "$size"}}}])))
        return rows.map(lambda r: int(r[0]["total"]) if r else 0)


class MongoValueStore(ValueStore):
    """The hot tier in Mongo, for VEs that run without Redis."""

    def __init__(self, db: Any, collection: str = HOT) -> None:
        self._col = db[collection]

    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        return _call("write", lambda: self._col.update_one(
            {"_id": hash}, {"$setOnInsert": {"data": bytes(data)}}, upsert=True)).map(lambda _: None)

    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        doc = _call("read", lambda: self._col.find_one({"_id": hash}))
        return doc.map(lambda d: bytes(d["data"]) if d else None)

    def get_many(self, hashes: Iterable[str]) -> Result[Dict[str, Optional[bytes]], AxoError]:
        ids = list(hashes)
        if not ids:
            return Ok({})
        docs = _call("read", lambda: list(self._col.find({"_id": {"$in": ids}})))
        if docs.is_err:
            return docs
        found = {d["_id"]: bytes(d["data"]) for d in docs.unwrap()}
        return Ok({h: found.get(h) for h in ids})

    def exists(self, hash: str) -> Result[bool, AxoError]:
        return _call("read", lambda: self._col.count_documents({"_id": hash}, limit=1) > 0)

    def delete(self, hash: str) -> Result[None, AxoError]:
        return _call("delete", lambda: self._col.delete_one({"_id": hash})).map(lambda _: None)
