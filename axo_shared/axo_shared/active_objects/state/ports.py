"""What the state store needs from its backends. The platform manager picks
one of each per deployment: hot values in Redis / Mongo / memory, blobs in
axo_storage / the file system / memory, manifests and reference counts in
the VE's Mongo (memory for tests and local dev).

Every value is addressed by its content hash, so writing one twice is
harmless and nothing is ever changed in place."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

from option import Ok, Result

from axo_shared.active_objects.models import AttributeEntry, StateManifest, Tier
from axo_shared.errors import AxoError


class ValueStore(ABC):
    """Bytes by content hash (one tier)."""

    @abstractmethod
    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        ...

    @abstractmethod
    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        ...

    @abstractmethod
    def exists(self, hash: str) -> Result[bool, AxoError]:
        ...

    @abstractmethod
    def delete(self, hash: str) -> Result[None, AxoError]:
        """Not an error when it isn't there."""

    def get_many(self, hashes: Iterable[str]) -> Result[Dict[str, Optional[bytes]], AxoError]:
        """Backends that can fetch several values in one round trip override this."""
        out: Dict[str, Optional[bytes]] = {}
        for h in hashes:
            got = self.get(h)
            if got.is_err:
                return got
            out[h] = got.unwrap()
        return Ok(out)


@dataclass(frozen=True)
class SweepItem:
    hash: str
    tier: Tier
    size: int


class ManifestStore(ABC):
    """Manifests (one current per object, plus its kept past revisions) and
    the reference count of every stored value: each manifest, current or
    kept, holds one reference to each distinct value it lists."""

    @abstractmethod
    def get(self, object_id: str) -> Result[Optional[StateManifest], AxoError]:
        ...

    @abstractmethod
    def swap(self, manifest: StateManifest, expected_revision: int) -> Result[bool, AxoError]:
        """Replace the object's manifest only if its revision is still
        ``expected_revision`` (0 = no manifest yet). False = someone else saved
        first; nothing changed."""

    @abstractmethod
    def remove(self, object_id: str, expected_revision: int) -> Result[bool, AxoError]:
        """Delete the manifest if still at that revision."""

    @abstractmethod
    def archive(self, manifest: StateManifest) -> Result[None, AxoError]:
        """Keep a replaced manifest as a past revision (its references stay)."""

    @abstractmethod
    def past(self, object_id: str) -> Result[List[StateManifest], AxoError]:
        """Kept past revisions, newest first."""

    @abstractmethod
    def forget_past(self, object_id: str, revisions: Iterable[int]) -> Result[None, AxoError]:
        ...

    @abstractmethod
    def add_refs(self, entries: Dict[str, AttributeEntry]) -> Result[None, AxoError]:
        """One more manifest uses each of these values."""

    @abstractmethod
    def release_refs(self, hashes: Iterable[str], now: float) -> Result[None, AxoError]:
        """One manifest fewer uses each of these values; a value at zero is
        removed by ``sweep`` after a grace time, never at once."""

    @abstractmethod
    def being_swept(self, hashes: Iterable[str]) -> Result[Set[str], AxoError]:
        """Which of these values a sweep is deleting right now."""

    @abstractmethod
    def claim_unreferenced(self, released_before: float, token: str, limit: int) -> Result[List[SweepItem], AxoError]:
        """Mark up to ``limit`` values unused since ``released_before`` as
        being swept by ``token``."""

    @abstractmethod
    def finish_sweep(self, hash: str, token: str) -> Result[bool, AxoError]:
        """Drop the value's count if it's still unused (True), else just clear
        the sweep mark (False: someone started using it again)."""

    @abstractmethod
    def sizes(self) -> Result[Dict[str, int], AxoError]:
        """Each object's current state size (shown per object; billing uses stored_bytes)."""

    @abstractmethod
    def stored_bytes(self) -> Result[int, AxoError]:
        """Bytes of every value in use, each counted once (what the VE is billed for)."""
