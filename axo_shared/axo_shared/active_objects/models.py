"""Shapes of active objects shared by the library, the endpoint, the VEM and
the Control Plane.

An active object is an instance of an ``Axo`` class living in one VE under a
unique name. Its class version (code + callable methods) is saved once per
code change; its state is a set of named attributes, each stored by content
hash in one of two tiers (see ``state/``) and listed in the object's
manifest. Only the manifest says which values are current, so swapping it is
the one step that makes a call's changes visible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

Tier = Literal["hot", "blob"]
# json = JSON text (any JSON-safe value), bytes = raw bytes as given,
# pickle = cloudpickle of anything else (tuples, sets, numpy arrays, ...).
ValueKind = Literal["json", "bytes", "pickle"]

DEFAULT_METHOD = "run"
DEFAULT_KEEP_CLASS_VERSIONS = 10


@dataclass(frozen=True)
class AttributeEntry:
    """One saved attribute: where it lives and how to decode it. ``hash`` is
    the sha256 of the kind and the encoded bytes and is also the value's
    address in its tier, so equal values are stored once per VE."""

    tier: Tier
    kind: ValueKind
    size: int
    hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AttributeEntry":
        return cls(tier=d["tier"], kind=d["kind"], size=int(d["size"]), hash=d["hash"])


@dataclass(frozen=True)
class StateManifest:
    """The current state of one object. ``revision`` goes up by one on every
    saved call (0 = nothing saved yet); a save only succeeds against the
    revision it read, which also stops two leaders from both writing."""

    object_id: str
    revision: int = 0
    attributes: Dict[str, AttributeEntry] = field(default_factory=dict)
    updated_at: float = 0.0

    @property
    def size_bytes(self) -> int:
        return sum(e.size for e in self.attributes.values())

    def hashes(self) -> Dict[str, AttributeEntry]:
        """Each distinct value of this manifest, by hash."""
        return {e.hash: e for e in self.attributes.values()}

    def to_dict(self) -> Dict[str, Any]:
        return {"object_id": self.object_id, "revision": self.revision, "updated_at": self.updated_at,
                "size_bytes": self.size_bytes,
                "attributes": {k: v.to_dict() for k, v in self.attributes.items()}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateManifest":
        return cls(object_id=d["object_id"], revision=int(d.get("revision", 0)),
                   updated_at=float(d.get("updated_at", 0.0)),
                   attributes={k: AttributeEntry.from_dict(v) for k, v in (d.get("attributes") or {}).items()})


@dataclass(frozen=True)
class MethodInfo:
    """A method people can call (``@axo_method``). ``params`` are its named
    arguments in order; ``wants_context`` = it takes ``ctx: Context``;
    ``optional`` = those with a default, ``defaults`` = the JSON ones."""

    name: str
    params: List[str] = field(default_factory=list)
    wants_context: bool = False
    doc: Optional[str] = None
    optional: List[str] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MethodInfo":
        return cls(name=d["name"], params=list(d.get("params") or []),
                   wants_context=bool(d.get("wants_context", False)), doc=d.get("doc"),
                   optional=list(d.get("optional") or []), defaults=dict(d.get("defaults") or {}))


@dataclass(frozen=True)
class ClassVersionRecord:
    """One saved version of an Axo class. Every method and class attribute is
    in the saved code; only ``methods`` can be called from outside. A new
    version is made only when ``code_hash`` changes."""

    class_name: str
    version: int
    code_hash: str
    methods: List[MethodInfo] = field(default_factory=list)
    has_upgrade: bool = False
    python_version: Optional[str] = None
    requirements: List[str] = field(default_factory=list)
    created_at: float = 0.0
    created_by: Optional[str] = None

    def method(self, name: str) -> Optional[MethodInfo]:
        return next((m for m in self.methods if m.name == name), None)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["methods"] = [m.to_dict() for m in self.methods]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClassVersionRecord":
        return cls(class_name=d["class_name"], version=int(d["version"]), code_hash=d["code_hash"],
                   methods=[MethodInfo.from_dict(m) for m in d.get("methods") or []],
                   has_upgrade=bool(d.get("has_upgrade", False)), python_version=d.get("python_version"),
                   requirements=list(d.get("requirements") or []), created_at=float(d.get("created_at", 0.0)),
                   created_by=d.get("created_by"))


@dataclass(frozen=True)
class ActiveObjectRecord:
    """An object as the VE lists it: its name, class version, and the size of
    its state (billed per VE with shared values counted once)."""

    object_id: str
    name: str
    class_name: str
    class_version: int
    created_at: float
    created_by: Optional[str] = None
    cloned_from: Optional[str] = None
    revision: int = 0
    size_bytes: int = 0
    last_call_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
