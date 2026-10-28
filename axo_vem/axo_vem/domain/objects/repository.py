from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ObjectClassRepository(ABC):
    """The VE's Axo classes, one document per class version
    (``<class_name>:<version>``): its function registration, callable
    methods, whether it has ``upgrade``, its code hash and who made it."""

    @abstractmethod
    def save(self, doc: Dict[str, Any]) -> None:
        """Upsert by class name + version (the projector replays the same)."""

    @abstractmethod
    def get(self, class_name: str, version: int) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def latest(self, class_name: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def find_by_code_hash(self, class_name: str, code_hash: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def list(self, class_name: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def remove(self, class_name: str, version: int) -> None: ...


class ActiveObjectRepository(ABC):
    """The VE's objects, one document per name (names are unique in a VE).
    A create reserves the name (``status`` "creating") before the endpoint
    runs ``__init__``; the endpoint's events then fill it in (or remove it
    when ``__init__`` failed)."""

    @abstractmethod
    def reserve(self, doc: Dict[str, Any]) -> bool:
        """False when the name is taken."""

    @abstractmethod
    def get(self, name: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def list(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def using(self, class_name: str, version: Optional[int] = None) -> List[Dict[str, Any]]:
        """The objects on that class (that version), whatever their status."""

    @abstractmethod
    def remove(self, name: str, object_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def mark_ready(self, name: str, object_id: str) -> None: ...

    @abstractmethod
    def apply_created(self, data: Dict[str, Any]) -> None: ...

    @abstractmethod
    def apply_called(self, data: Dict[str, Any]) -> None: ...

    @abstractmethod
    def apply_upgraded(self, data: Dict[str, Any]) -> None: ...

    @abstractmethod
    def apply_deleted(self, data: Dict[str, Any]) -> None: ...

    @abstractmethod
    def set_keep_revisions(self, name: str, keep_revisions: Optional[int]) -> None: ...

    @abstractmethod
    def apply_sizes(self, sizes: Dict[str, int], stored_bytes: Optional[int] = None) -> None:
        """Each object's own size, and the VE's whole state (shared values once: what's billed)."""

    @abstractmethod
    def stored_bytes(self) -> Optional[int]:
        """The VE's state size last reported (None = never)."""

    @abstractmethod
    def last_billed_hour(self) -> Optional[float]: ...

    @abstractmethod
    def mark_billed_hour(self, hour_start: float) -> None:
        """The last hour sent to the Control Plane (so a restart catches up)."""
