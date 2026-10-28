from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from axo_vem.domain.choreography.choreography import Choreography


class ChoreographyRepository(ABC):
    """Choreographies (their newest version), every version, and the names
    in use. Written straight by ChoreographyService (so a save is readable at
    once) and again, idempotently, by the projector from the event log.
    Soft-deleted ones are never returned."""

    @abstractmethod
    def get(self, choreography_id: str) -> Optional[Choreography]: ...

    @abstractmethod
    def get_by_name(self, name: str) -> Optional[Choreography]: ...

    @abstractmethod
    def list(self) -> List[Choreography]: ...

    @abstractmethod
    def save(self, choreography: Choreography) -> None:
        """The newest version, and that version in the version list."""

    @abstractmethod
    def get_version(self, choreography_id: str, version: int) -> Optional[Choreography]: ...

    @abstractmethod
    def versions(self, choreography_id: str) -> List[Dict[str, Any]]:
        """Newest first: version, name, updated_at, updated_by."""

    @abstractmethod
    def set_triggers(self, choreography_id: str, triggers: List[Dict[str, Any]]) -> None: ...

    @abstractmethod
    def reserve_name(self, name: str, choreography_id: str) -> bool:
        """False if another choreography holds it."""

    @abstractmethod
    def release_name(self, name: str, choreography_id: str) -> None: ...

    @abstractmethod
    def soft_delete(self, choreography_id: str, deleted_at) -> None: ...

    @abstractmethod
    def triggers_for_bucket(self, bucket: str) -> List[Choreography]:
        """Every live choreography with a trigger on ``bucket``."""

    @abstractmethod
    def triggers_for_event(self, event_type: str) -> List[Choreography]:
        """Every live choreography whose newest version has a trigger on ``event_type``."""

    @abstractmethod
    def trigger_buckets(self) -> List[Dict[str, str]]:
        """Each (bucket, who added the trigger) of every live choreography, once."""
