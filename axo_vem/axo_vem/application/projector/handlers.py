from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from axo_vem.domain.choreography.repository import ChoreographyRepository
from axo_vem.domain.compute.consensus_recorder import ConsensusRecorder
from axo_vem.domain.compute.repository import EndpointRepository, FunctionRepository
from axo_vem.domain.events.activity_recorder import ActivityRecorder
from axo_vem.domain.execution.repository import JobRepository
from axo_vem.domain.objects.repository import ActiveObjectRepository, ObjectClassRepository


@dataclass(frozen=True)
class ProjectorHandlers:
    """Bundles every abstract port the projector's dispatch needs -- one per
    read-model aggregate, matching the migration plan's per-aggregate
    application/projector/*_handler.py split. Replaces
    projector/upserts.py's former ProjectorCollections (which bundled raw
    pymongo Collections instead of domain-typed repositories)."""

    activity_recorder: ActivityRecorder
    endpoint_repository: EndpointRepository
    function_repository: FunctionRepository
    consensus_recorder: ConsensusRecorder
    job_repository: JobRepository
    choreography_repository: ChoreographyRepository
    object_class_repository: Optional[ObjectClassRepository] = None
    active_object_repository: Optional[ActiveObjectRepository] = None
