from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from axo_vem.domain.choreography.run import ChoreographyRun


class ChoreographyRunRepository(ABC):
    """Run records, written by the engine as steps move on."""

    @abstractmethod
    def get(self, run_id: str) -> Optional[ChoreographyRun]: ...

    @abstractmethod
    def save(self, run: ChoreographyRun) -> None: ...

    @abstractmethod
    def insert(self, run: ChoreographyRun) -> bool:
        """False if a run with this id already exists (the same id = one run)."""

    @abstractmethod
    def list(self, choreography_id: Optional[str] = None, status: Optional[str] = None, since=None,
             limit: int = 100, acting_user_id: Optional[str] = None, started_by_type: Optional[str] = None,
             parent_run_id: Optional[str] = None) -> List[ChoreographyRun]:
        """Newest first. ``acting_user_id``: only the runs started for that
        person; ``started_by_type``: user / trigger / choreography;
        ``parent_run_id``: only the runs that run's steps called."""

    @abstractmethod
    def active(self) -> List[ChoreographyRun]:
        """Every queued or running run, oldest first (what a restart picks up)."""

    @abstractmethod
    def count(self, choreography_id: str, status: str) -> int: ...

    @abstractmethod
    def overview(self, since) -> Dict[str, Dict[str, Any]]:
        """Per choreography, in one pass: its newest run (``last_run``: run
        id, status, version, queued / finished), runs since ``since``
        (``runs_24h``) and how many of those ended with errors, and how
        many are queued or running now (``active_runs``)."""

    @abstractmethod
    def usage(self, choreography_id: str, since=None) -> Dict[str, Any]: ...

    @abstractmethod
    def delete_for(self, choreography_id: str) -> int: ...

