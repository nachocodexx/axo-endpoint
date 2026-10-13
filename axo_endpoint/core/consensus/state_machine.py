from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional

from axo_endpoint.core.consensus.elector import LeaderView


@dataclass(frozen=True)
class ClusterState:
    """Everything that must survive node failure: membership/leader metadata,
    plus a snapshot of function-registry entries. (Data and buckets are not
    here: they live in axo_storage, never on endpoints.)

    ``functions`` holds opaque serialized blobs ("name:version" -> bytes),
    not live FunctionRecord objects, so core.consensus never depends on
    core.functions — layering stays one-directional.
    """

    term: int = 0
    leader_ids: FrozenSet[str] = field(default_factory=frozenset)
    members: FrozenSet[str] = field(default_factory=frozenset)
    functions: Dict[str, bytes] = field(default_factory=dict)
    version: int = 0


@dataclass(frozen=True)
class StateMutation:
    """A diff to fold into ClusterState."""

    function_changes: Dict[str, bytes] = field(default_factory=dict)
    leader_view: Optional[LeaderView] = None


class ReplicatedStateMachine(ABC):
    """Owns the authoritative, replicated view of ClusterState for this node.

    This is whole-state (or whole-diff) snapshot shipping, not a Raft-style
    committed log with an append index — deliberately the simple option.
    """

    @abstractmethod
    def snapshot(self) -> ClusterState:
        """Returns the current state (for sending to a follower, or local reads)."""

    @abstractmethod
    def apply_local(self, mutation: StateMutation) -> None:
        """Folds in a mutation produced locally (e.g. this node, as leader, just
        registered a function)."""

    @abstractmethod
    def apply_remote(self, incoming: ClusterState) -> bool:
        """Folds in a state snapshot received from the leader.

        Returns True iff it actually changed anything (incoming.version > local.version).
        """


class InMemoryReplicatedStateMachine(ReplicatedStateMachine):
    """Process-local, dict-backed ReplicatedStateMachine. Not persisted across restarts."""

    def __init__(self) -> None:
        self._state = ClusterState()
        self._lock = threading.Lock()

    def snapshot(self) -> ClusterState:
        with self._lock:
            return self._state

    def apply_local(self, mutation: StateMutation) -> None:
        with self._lock:
            functions = dict(self._state.functions)
            functions.update(mutation.function_changes)
            leader_ids = self._state.leader_ids
            term = self._state.term
            if mutation.leader_view is not None:
                leader_ids = mutation.leader_view.leader_ids
                term = mutation.leader_view.term
            self._state = ClusterState(
                term=term,
                leader_ids=leader_ids,
                members=self._state.members,
                functions=functions,
                version=self._state.version + 1,
            )

    def apply_remote(self, incoming: ClusterState) -> bool:
        with self._lock:
            if incoming.version <= self._state.version:
                return False
            self._state = incoming
            return True


# ── Wire encoding helpers ──────────────────────────────────────────────────
#
# Each value in ``functions``/``function_changes`` is itself a UTF-8 JSON blob
# (produced by ``registry_sync.serialize_function_record``), so embedding it
# in an outer JSON structure only requires parsing it back to a dict first —
# no extra base64 layer needed on top of what's already inside the blob.


def encode_function_changes(function_changes: Dict[str, bytes]) -> bytes:
    """Encodes a diff of function changes for the STATE_SYNC_PUSH payload."""
    return json.dumps(
        {key: json.loads(blob.decode("utf-8")) for key, blob in function_changes.items()}
    ).encode("utf-8")


def decode_function_changes(payload: bytes) -> Dict[str, bytes]:
    """Decodes a STATE_SYNC_PUSH payload back into per-key serialized blobs."""
    if not payload:
        return {}
    decoded = json.loads(payload.decode("utf-8"))
    return {key: json.dumps(record_dict).encode("utf-8") for key, record_dict in decoded.items()}


def encode_state_changes(function_changes: Dict[str, bytes]) -> bytes:
    """The single payload frame a STATE_SYNC_PUSH command carries."""
    return json.dumps(
        {"functions": {key: json.loads(blob.decode("utf-8")) for key, blob in function_changes.items()}}
    ).encode("utf-8")


def decode_state_changes(payload: bytes) -> Dict[str, bytes]:
    """Decodes a STATE_SYNC_PUSH payload back into function_changes. Any
    other domain an older peer still sends (data/buckets) is ignored."""
    if not payload:
        return {}
    d = json.loads(payload.decode("utf-8"))
    return {key: json.dumps(v).encode("utf-8") for key, v in d.get("functions", {}).items()}


def encode_cluster_state(state: ClusterState) -> bytes:
    """Encodes a full ClusterState snapshot for a STATE_SYNC_PULL reply."""
    return json.dumps(
        {
            "term": state.term,
            "leader_ids": list(state.leader_ids),
            "members": list(state.members),
            "functions": {
                key: json.loads(blob.decode("utf-8")) for key, blob in state.functions.items()
            },
            "version": state.version,
        }
    ).encode("utf-8")


def decode_cluster_state(payload: bytes) -> ClusterState:
    """Decodes a STATE_SYNC_PULL reply payload back into a ClusterState."""
    d = json.loads(payload.decode("utf-8"))
    return ClusterState(
        term=d["term"],
        leader_ids=frozenset(d["leader_ids"]),
        members=frozenset(d["members"]),
        functions={key: json.dumps(v).encode("utf-8") for key, v in d["functions"].items()},
        version=d["version"],
    )
