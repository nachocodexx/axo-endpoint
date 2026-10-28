"""Saves and loads the state of active objects.

Saving a call's changes, in order:

1. encode each changed attribute (``codec``) and check the size cap;
2. count one more use of each new value (``add_refs``) -- from then on no
   sweep can delete it;
3. wait while a sweep that started earlier is still deleting one of them,
   then write the values that aren't stored yet (hot ones, then blobs);
4. swap the manifest, only if it is still at the revision the call read.

Nothing before step 4 is visible, so a failed or conflicting call is undone
by not swapping (and giving its references back). The replaced manifest is
kept as a past revision (``keep_revisions``, 10 by default, per object);
past revisions can be loaded and restored. Each kept manifest holds its
values, so they are only released when it is trimmed; ``sweep`` deletes
values nobody has used for ``grace_seconds``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from option import Err, Ok, Result

from axo_shared.active_objects.models import AttributeEntry, StateManifest
from axo_shared.active_objects.state import codec
from axo_shared.active_objects.state.ports import ManifestStore, ValueStore
from axo_shared.errors import (
    ActiveObjectAttributeInvalidError,
    ActiveObjectExistsError,
    ActiveObjectNotFoundError,
    ActiveObjectStateBackendError,
    ActiveObjectStateConflictError,
    ActiveObjectStateTooLargeError,
    ActiveObjectStateUnreadableError,
    AxoError,
)

DEFAULT_KEEP_REVISIONS = 10

# Names the library keeps for itself (self.event, self.store, ...).
RESERVED_NAMES = frozenset({"event", "store", "ctx", "object_id", "object_name"})


@dataclass(frozen=True)
class StatePolicy:
    """Per plan: the largest JSON value kept hot, and the cap on one object's
    state (None = no cap)."""

    hot_max_bytes: int = 4096
    max_state_bytes: Optional[int] = None
    # Past revisions kept per object (an object's own setting can change it,
    # up to ``max_keep_revisions``; None = no ceiling).
    keep_revisions: int = DEFAULT_KEEP_REVISIONS
    max_keep_revisions: Optional[int] = None

    def with_limits(self, limits: Optional[Mapping[str, Any]]) -> "StatePolicy":
        """This policy with the plan's current limits (sent along with each
        object operation, so a plan change applies at once); a missing key
        keeps this policy's value, 0 / None = no cap."""
        if not limits:
            return self
        def cap(key: str, current: Optional[int]) -> Optional[int]:
            if key not in limits:
                return current
            return int(limits[key]) if limits[key] else None
        return StatePolicy(
            hot_max_bytes=int(limits.get("hot_max_bytes") or self.hot_max_bytes),
            max_state_bytes=cap("max_state_bytes", self.max_state_bytes),
            keep_revisions=int(limits["keep_revisions"]) if limits.get("keep_revisions") is not None
            else self.keep_revisions,
            max_keep_revisions=cap("max_keep_revisions", self.max_keep_revisions),
        )


def check_attribute_name(name: str) -> Result[None, AxoError]:
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_") or name in RESERVED_NAMES:
        return Err(ActiveObjectAttributeInvalidError(
            f"{name!r} can't be saved: attribute names must be identifiers, not start with _, "
            f"and not be one of {sorted(RESERVED_NAMES)}", {"attribute": name}))
    return Ok(None)


class ObjectState:
    """An object's saved state as one call sees it. Hot values are read at
    load time; blobs only when first asked for."""

    def __init__(self, manifest: StateManifest, values: Dict[str, Any], blobs: ValueStore) -> None:
        self.manifest = manifest
        self._values = values
        self._blobs = blobs

    @property
    def revision(self) -> int:
        return self.manifest.revision

    def names(self) -> List[str]:
        return list(self.manifest.attributes)

    def is_loaded(self, name: str) -> bool:
        return name in self._values

    def get(self, name: str) -> Result[Any, AxoError]:
        if name in self._values:
            return Ok(self._values[name])
        entry = self.manifest.attributes.get(name)
        if entry is None:
            return Err(ActiveObjectStateUnreadableError(f"The object has no attribute {name!r}", {"attribute": name}))
        data = self._blobs.get(entry.hash)
        if data.is_err:
            return data
        if data.unwrap() is None:
            return Err(ActiveObjectStateUnreadableError(
                f"The saved value of {name!r} is missing", {"attribute": name, "hash": entry.hash}))
        value = codec.decode(entry, data.unwrap())
        if value.is_ok:
            self._values[name] = value.unwrap()
        return value

    def raw(self, name: str) -> Result[bytes, AxoError]:
        """A blob attribute's stored bytes, undecoded (the worker running the
        call decodes it, where its class is loaded)."""
        entry = self.manifest.attributes.get(name)
        if entry is None or entry.tier != "blob":
            return Err(ActiveObjectStateUnreadableError(f"The object has no saved blob {name!r}", {"attribute": name}))
        data = self._blobs.get(entry.hash)
        if data.is_err:
            return data
        if data.unwrap() is None:
            return Err(ActiveObjectStateUnreadableError(
                f"The saved value of {name!r} is missing", {"attribute": name, "hash": entry.hash}))
        return Ok(data.unwrap())

    def hot_values(self) -> Dict[str, Any]:
        """The values loaded with the state (the hot tier, decoded)."""
        return {k: v for k, v in self._values.items()
                if k in self.manifest.attributes and self.manifest.attributes[k].tier == "hot"}

    def to_dict(self) -> Result[Dict[str, Any], AxoError]:
        """Every attribute (loads the blobs)."""
        out: Dict[str, Any] = {}
        for name in self.names():
            got = self.get(name)
            if got.is_err:
                return got
            out[name] = got.unwrap()
        return Ok(out)


class StateStore:
    def __init__(
        self,
        manifests: ManifestStore,
        hot: ValueStore,
        blobs: ValueStore,
        policy: StatePolicy = StatePolicy(),
        now_fn: Callable[[], float] = time.time,
        sweep_wait_seconds: float = 30.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._manifests = manifests
        self._tiers = {"hot": hot, "blob": blobs}
        self.policy = policy
        self._now = now_fn
        self._sweep_wait = sweep_wait_seconds
        self._sleep = sleep_fn

    # ── reading ──────────────────────────────────────────────────────────────
    def manifest(self, object_id: str) -> Result[Optional[StateManifest], AxoError]:
        return self._manifests.get(object_id)

    def revisions(self, object_id: str) -> Result[List[StateManifest], AxoError]:
        """The current manifest and the kept past ones, newest first."""
        got = self._manifests.get(object_id)
        if got.is_err:
            return got
        past = self._manifests.past(object_id)
        if past.is_err:
            return past
        return Ok(([got.unwrap()] if got.unwrap() else []) + past.unwrap())

    def _manifest_at(self, object_id: str, revision: Optional[int]) -> Result[StateManifest, AxoError]:
        got = self._manifests.get(object_id)
        if got.is_err:
            return got
        current = got.unwrap() or StateManifest(object_id=object_id)
        if revision is None or revision == current.revision:
            return Ok(current)
        past = self._manifests.past(object_id)
        if past.is_err:
            return past
        found = next((m for m in past.unwrap() if m.revision == revision), None)
        if found is None:
            return Err(ActiveObjectStateUnreadableError(
                f"Revision {revision} of object {object_id} is no longer kept",
                {"object_id": object_id, "revision": revision}))
        return Ok(found)

    def load(self, object_id: str, revision: Optional[int] = None) -> Result[ObjectState, AxoError]:
        """The current state (or a kept past ``revision``). An object that
        never saved anything loads as empty (revision 0)."""
        got = self._manifest_at(object_id, revision)
        if got.is_err:
            return got
        manifest = got.unwrap()
        hot = {name: e for name, e in manifest.attributes.items() if e.tier == "hot"}
        fetched = self._tiers["hot"].get_many({e.hash for e in hot.values()})
        if fetched.is_err:
            return fetched
        values: Dict[str, Any] = {}
        for name, entry in hot.items():
            data = fetched.unwrap().get(entry.hash)
            if data is None:
                return Err(ActiveObjectStateUnreadableError(
                    f"The saved value of {name!r} is missing", {"attribute": name, "hash": entry.hash}))
            value = codec.decode(entry, data)
            if value.is_err:
                return value
            values[name] = value.unwrap()
        return Ok(ObjectState(manifest, values, self._tiers["blob"]))

    # ── saving ───────────────────────────────────────────────────────────────
    def commit(
        self,
        object_id: str,
        base_revision: int,
        changes: Mapping[str, Any],
        deleted: Iterable[str] = (),
        policy: Optional[StatePolicy] = None,
        keep_revisions: Optional[int] = None,
    ) -> Result[StateManifest, AxoError]:
        """Save one call's changes on top of ``base_revision`` (the revision
        the call loaded). Returns the new manifest; on any error nothing the
        call did is visible."""
        policy = policy or self.policy
        encoded: Dict[str, Tuple[AttributeEntry, bytes]] = {}
        for name, value in changes.items():
            checked = check_attribute_name(name)
            if checked.is_err:
                return checked
            one = codec.encode(value, policy.hot_max_bytes)
            if one.is_err:
                return Err(_with_attribute(one.unwrap_err(), name))
            encoded[name] = one.unwrap()
        return self.commit_encoded(object_id, base_revision, encoded, deleted, policy, keep_revisions)

    def commit_encoded(
        self,
        object_id: str,
        base_revision: int,
        changes: Mapping[str, Tuple[AttributeEntry, bytes]],
        deleted: Iterable[str] = (),
        policy: Optional[StatePolicy] = None,
        keep_revisions: Optional[int] = None,
    ) -> Result[StateManifest, AxoError]:
        """``commit`` for values already encoded where the class lives (the
        worker running the call): each hash is checked against its bytes, and
        a JSON value only stays hot within this policy's limit."""
        policy = policy or self.policy
        got = self._manifests.get(object_id)
        if got.is_err:
            return got
        current = got.unwrap() or StateManifest(object_id=object_id)
        if current.revision != base_revision:
            return Err(self._conflict(object_id, base_revision, current.revision))
        attributes = {k: v for k, v in current.attributes.items() if k not in set(deleted)}
        payloads: Dict[str, bytes] = {}
        for name, (entry, data) in changes.items():
            checked = check_attribute_name(name)
            if checked.is_err:
                return checked
            if entry.hash != codec.content_hash(entry.kind, data) or entry.size != len(data):
                return Err(ActiveObjectStateUnreadableError(
                    f"Attribute {name!r} arrived damaged (hash or size mismatch)", {"attribute": name}))
            if entry.tier == "hot" and (entry.kind != "json" or entry.size > policy.hot_max_bytes):
                entry = AttributeEntry(tier="blob", kind=entry.kind, size=entry.size, hash=entry.hash)
            attributes[name] = entry
            payloads[entry.hash] = data
        manifest = StateManifest(object_id=object_id, revision=base_revision + 1,
                                 attributes=attributes, updated_at=self._now())
        if policy.max_state_bytes is not None and manifest.size_bytes > policy.max_state_bytes:
            return Err(ActiveObjectStateTooLargeError(
                f"The object would hold {manifest.size_bytes} bytes; its limit is {policy.max_state_bytes}",
                {"object_id": object_id, "size_bytes": manifest.size_bytes, "limit": policy.max_state_bytes}))
        if manifest.attributes == current.attributes:
            return Ok(current)                     # nothing changed: no new revision
        return self._swap_in(manifest, current, payloads, _keep(policy, keep_revisions))

    def restore(self, object_id: str, revision: int, base_revision: int,
                keep_revisions: Optional[int] = None,
                policy: Optional[StatePolicy] = None) -> Result[StateManifest, AxoError]:
        """Make a kept past revision the current state again (as a new revision)."""
        got = self._manifests.get(object_id)
        if got.is_err:
            return got
        current = got.unwrap() or StateManifest(object_id=object_id)
        if current.revision != base_revision:
            return Err(self._conflict(object_id, base_revision, current.revision))
        old = self._manifest_at(object_id, revision)
        if old.is_err:
            return old
        manifest = StateManifest(object_id=object_id, revision=base_revision + 1,
                                 attributes=dict(old.unwrap().attributes), updated_at=self._now())
        return self._swap_in(manifest, current, {}, _keep(policy or self.policy, keep_revisions))

    def clone(self, source_id: str, target_id: str, keep_revisions: Optional[int] = None,
              policy: Optional[StatePolicy] = None) -> Result[StateManifest, AxoError]:
        """A new object with the same state; values are shared, not copied."""
        got = self._manifests.get(source_id)
        if got.is_err:
            return got
        if got.unwrap() is None:
            return Err(ActiveObjectNotFoundError(f"Object {source_id} has no saved state", {"object_id": source_id}))
        existing = self._manifests.get(target_id)
        if existing.is_err:
            return existing
        if existing.unwrap() is not None:
            return Err(ActiveObjectExistsError(f"Object {target_id} already has state", {"object_id": target_id}))
        source = got.unwrap()
        manifest = StateManifest(object_id=target_id, revision=1, attributes=dict(source.attributes),
                                 updated_at=self._now())
        return self._swap_in(manifest, StateManifest(object_id=target_id), {},
                             _keep(policy or self.policy, keep_revisions))

    def drop(self, object_id: str) -> Result[None, AxoError]:
        """Forget an object's state and past revisions (values are swept once unused)."""
        got = self._manifests.get(object_id)
        if got.is_err:
            return got
        current = got.unwrap()
        if current is not None:
            removed = self._manifests.remove(object_id, current.revision)
            if removed.is_err:
                return removed
            if not removed.unwrap():
                return self.drop(object_id)       # it changed meanwhile: drop that one
            released = self._manifests.release_refs(current.hashes(), self._now())
            if released.is_err:
                return released
        return self._trim(object_id, 0)

    def _swap_in(self, manifest: StateManifest, current: StateManifest,
                 payloads: Dict[str, bytes], keep: int) -> Result[StateManifest, AxoError]:
        # The new manifest holds its own reference to every value it lists;
        # the replaced one keeps its references while it is a kept revision.
        mine = manifest.hashes()
        added = self._manifests.add_refs(mine)
        if added.is_err:
            return added
        new = {h: e for h, e in mine.items() if h not in current.hashes()}
        written = self._write_values(new, payloads)
        if written.is_err:
            self._manifests.release_refs(mine, self._now())
            return written
        swapped = self._manifests.swap(manifest, current.revision)
        if swapped.is_err or not swapped.unwrap():
            self._manifests.release_refs(mine, self._now())
            if swapped.is_err:
                return swapped
            latest = self._manifests.get(manifest.object_id)
            seen = latest.unwrap().revision if latest.is_ok and latest.unwrap() else 0
            return Err(self._conflict(manifest.object_id, current.revision, seen))
        # The new state is saved; a failure below only keeps values longer.
        if current.revision > 0:
            if keep > 0 and self._manifests.archive(current).is_ok:
                self._trim(manifest.object_id, keep)
            else:
                self._manifests.release_refs(current.hashes(), self._now())
        return Ok(manifest)

    def _trim(self, object_id: str, keep: int) -> Result[None, AxoError]:
        past = self._manifests.past(object_id)
        if past.is_err:
            return past
        dropped = past.unwrap()[keep:]
        if not dropped:
            return Ok(None)
        forgot = self._manifests.forget_past(object_id, [m.revision for m in dropped])
        if forgot.is_err:
            return forgot
        for m in dropped:
            released = self._manifests.release_refs(m.hashes(), self._now())
            if released.is_err:
                return released
        return Ok(None)

    def _write_values(self, entries: Dict[str, AttributeEntry], payloads: Dict[str, bytes]) -> Result[None, AxoError]:
        if not entries:
            return Ok(None)
        deadline = self._now() + self._sweep_wait
        while True:
            busy = self._manifests.being_swept(entries)
            if busy.is_err:
                return busy
            if not busy.unwrap():
                break
            if self._now() >= deadline:
                return Err(ActiveObjectStateBackendError(
                    "Old copies of some values are still being deleted; try again", {"hashes": sorted(busy.unwrap())}))
            self._sleep(0.05)
        for tier in ("hot", "blob"):              # blobs last: the slowest, and the hot tier is cheap to redo
            store = self._tiers[tier]
            for h, entry in entries.items():
                if entry.tier != tier:
                    continue
                there = store.exists(h)
                if there.is_err:
                    return there
                if there.unwrap():
                    continue
                if h not in payloads:
                    return Err(ActiveObjectStateUnreadableError(
                        "A shared value is missing from the store", {"hash": h}))
                put = store.put(h, payloads[h])
                if put.is_err:
                    return put
        return Ok(None)

    # ── housekeeping ─────────────────────────────────────────────────────────
    def sweep(self, grace_seconds: float = 3600.0, limit: int = 500) -> Result[int, AxoError]:
        """Delete values no manifest has used for ``grace_seconds``. Safe to run
        on several endpoints at once."""
        token = uuid.uuid4().hex
        claimed = self._manifests.claim_unreferenced(self._now() - grace_seconds, token, limit)
        if claimed.is_err:
            return claimed
        deleted = 0
        for item in claimed.unwrap():
            gone = self._tiers[item.tier].delete(item.hash)
            if gone.is_err:
                return gone
            finished = self._manifests.finish_sweep(item.hash, token)
            if finished.is_err:
                return finished
            deleted += int(finished.unwrap())
        return Ok(deleted)

    def stored_bytes(self) -> Result[int, AxoError]:
        return self._manifests.stored_bytes()

    def sizes(self) -> Result[Dict[str, int], AxoError]:
        return self._manifests.sizes()

    @staticmethod
    def _conflict(object_id: str, expected: int, seen: int) -> AxoError:
        return ActiveObjectStateConflictError(
            f"Object {object_id} is at revision {seen}, the call read {expected}",
            {"object_id": object_id, "expected_revision": expected, "revision": seen})


def _with_attribute(err: AxoError, name: str) -> AxoError:
    err.context = {**err.context, "attribute": name}
    err.message = f"Attribute {name!r}: {err.message}"
    return err


def _keep(policy: StatePolicy, override: Optional[int]) -> int:
    """The object's own setting (else the plan default), never past the plan's ceiling."""
    keep = max(0, policy.keep_revisions if override is None else int(override))
    return keep if policy.max_keep_revisions is None else min(keep, policy.max_keep_revisions)
