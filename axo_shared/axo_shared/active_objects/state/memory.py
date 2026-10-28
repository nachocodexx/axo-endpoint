"""In-process backends: tests, local dev, and a single-endpoint VE that
doesn't need its objects to survive a restart."""

from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Set

from option import Ok, Result

from axo_shared.active_objects.models import AttributeEntry, StateManifest
from axo_shared.active_objects.state.ports import ManifestStore, SweepItem, ValueStore
from axo_shared.errors import AxoError


class MemoryValueStore(ValueStore):
    def __init__(self) -> None:
        self._data: Dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        with self._lock:
            self._data[hash] = bytes(data)
        return Ok(None)

    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        with self._lock:
            return Ok(self._data.get(hash))

    def exists(self, hash: str) -> Result[bool, AxoError]:
        with self._lock:
            return Ok(hash in self._data)

    def delete(self, hash: str) -> Result[None, AxoError]:
        with self._lock:
            self._data.pop(hash, None)
        return Ok(None)

    def __len__(self) -> int:
        return len(self._data)


class MemoryManifestStore(ManifestStore):
    def __init__(self) -> None:
        self._manifests: Dict[str, StateManifest] = {}
        self._past: Dict[str, Dict[int, StateManifest]] = {}
        # hash -> {"tier", "size", "count", "released_at", "sweep"}
        self._refs: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, object_id: str) -> Result[Optional[StateManifest], AxoError]:
        with self._lock:
            return Ok(self._manifests.get(object_id))

    def swap(self, manifest: StateManifest, expected_revision: int) -> Result[bool, AxoError]:
        with self._lock:
            current = self._manifests.get(manifest.object_id)
            if (current.revision if current else 0) != expected_revision:
                return Ok(False)
            self._manifests[manifest.object_id] = manifest
            return Ok(True)

    def remove(self, object_id: str, expected_revision: int) -> Result[bool, AxoError]:
        with self._lock:
            current = self._manifests.get(object_id)
            if current is None or current.revision != expected_revision:
                return Ok(False)
            del self._manifests[object_id]
            return Ok(True)

    def archive(self, manifest: StateManifest) -> Result[None, AxoError]:
        with self._lock:
            self._past.setdefault(manifest.object_id, {})[manifest.revision] = manifest
        return Ok(None)

    def past(self, object_id: str) -> Result[List[StateManifest], AxoError]:
        with self._lock:
            kept = self._past.get(object_id, {})
            return Ok([kept[r] for r in sorted(kept, reverse=True)])

    def forget_past(self, object_id: str, revisions: Iterable[int]) -> Result[None, AxoError]:
        with self._lock:
            kept = self._past.get(object_id, {})
            for r in revisions:
                kept.pop(r, None)
            if not kept:
                self._past.pop(object_id, None)
        return Ok(None)

    def add_refs(self, entries: Dict[str, AttributeEntry]) -> Result[None, AxoError]:
        with self._lock:
            for h, e in entries.items():
                ref = self._refs.setdefault(h, {"tier": e.tier, "size": e.size, "count": 0,
                                                "released_at": None, "sweep": None})
                ref["count"] += 1
        return Ok(None)

    def release_refs(self, hashes: Iterable[str], now: float) -> Result[None, AxoError]:
        with self._lock:
            for h in hashes:
                ref = self._refs.get(h)
                if ref is not None:
                    ref["count"] -= 1
                    ref["released_at"] = now
        return Ok(None)

    def being_swept(self, hashes: Iterable[str]) -> Result[Set[str], AxoError]:
        with self._lock:
            return Ok({h for h in hashes if (self._refs.get(h) or {}).get("sweep")})

    def claim_unreferenced(self, released_before: float, token: str, limit: int) -> Result[List[SweepItem], AxoError]:
        out: List[SweepItem] = []
        with self._lock:
            for h, ref in self._refs.items():
                if len(out) >= limit:
                    break
                if ref["count"] <= 0 and ref["sweep"] is None and (ref["released_at"] or 0) < released_before:
                    ref["sweep"] = token
                    out.append(SweepItem(hash=h, tier=ref["tier"], size=ref["size"]))
        return Ok(out)

    def finish_sweep(self, hash: str, token: str) -> Result[bool, AxoError]:
        with self._lock:
            ref = self._refs.get(hash)
            if ref is None or ref["sweep"] != token:
                return Ok(False)
            if ref["count"] <= 0:
                del self._refs[hash]
                return Ok(True)
            ref["sweep"] = None
            return Ok(False)

    def sizes(self) -> Result[Dict[str, int], AxoError]:
        with self._lock:
            return Ok({k: m.size_bytes for k, m in self._manifests.items()})

    def stored_bytes(self) -> Result[int, AxoError]:
        with self._lock:
            return Ok(sum(r["size"] for r in self._refs.values() if r["count"] > 0))

    def ref_count(self, hash: str) -> int:
        """For tests."""
        return (self._refs.get(hash) or {}).get("count", 0)
