from __future__ import annotations

from axo_vem.domain.choreography.run_repository import ChoreographyRunRepository
from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository


class PurgeChoreographyUseCase:
    """Removes a deleted choreography for good: its versions, its runs and its
    activity rows (never the Kurrent event log). Its steps' jobs stay in the
    jobs list until their results' keep time is over."""

    def __init__(self, repository: MongoChoreographyRepository, runs: ChoreographyRunRepository,
                 activity_repository: MongoActivityRepository) -> None:
        self._repository = repository
        self._runs = runs
        self._activity_repository = activity_repository

    def execute(self, *, choreography_id: str) -> None:
        doc = self._repository.raw(choreography_id)
        if doc is None:
            raise NotFoundError("No such choreography", error_name="CHOREOGRAPHY_NOT_FOUND")
        if doc.get("deleted_at") is None:
            raise ConflictError("Delete the choreography first", error_name="VEM_CONFLICT")
        self._runs.delete_for(choreography_id)
        self._repository.remove(choreography_id)
        self._activity_repository.purge(choreography_id=choreography_id)
