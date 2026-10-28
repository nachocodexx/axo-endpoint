from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from axo_vem.domain.events.models import ChoreographyGraph, ChoreographySettings


def stream_name(choreography_id: str) -> str:
    return f"choreographies-{choreography_id}"


@dataclass
class Choreography:
    """A reusable blueprint: the newest version's graph and settings (every
    version is kept too, see ChoreographyRepository.get_version). Its name
    is unique in the VE. Who may see / edit / run it is the VE roles' job
    (checked by the Control Plane); ``owner_user_id`` is who created it."""

    choreography_id: str
    name: str
    owner_user_id: str
    version: int
    graph: ChoreographyGraph
    settings: ChoreographySettings = field(default_factory=ChoreographySettings)
    description: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    updated_by: Optional[str] = None
    updated_by_name: Optional[str] = None
    # bucket triggers from before triggers were saved with each version
    # (ChoreographyService.migrate_legacy_triggers moves them into a version)
    legacy_triggers: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def triggers(self) -> List[Dict[str, Any]]:
        """The active triggers: the newest version's (kept on the record too,
        so a file / event finds its choreographies with one query)."""
        if self.graph.triggers:
            return [t.model_dump(mode="json") for t in self.graph.triggers]
        return list(self.legacy_triggers)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "choreography_id": self.choreography_id,
            "name": self.name,
            "description": self.description,
            "owner_user_id": self.owner_user_id,
            "version": self.version,
            "graph": self.graph.model_dump(mode="json"),
            "settings": self.settings.model_dump(mode="json"),
            "triggers": list(self.triggers),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "updated_by_name": self.updated_by_name,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Choreography":
        return cls(
            choreography_id=d["choreography_id"], name=d["name"], owner_user_id=d.get("owner_user_id") or "",
            version=int(d.get("version") or 1), graph=ChoreographyGraph.model_validate(d["graph"]),
            settings=ChoreographySettings.model_validate(d.get("settings") or {}),
            description=d.get("description"),
            legacy_triggers=[t for t in d.get("triggers") or [] if "event_type" not in t],
            created_at=d.get("created_at") or "", updated_at=d.get("updated_at") or "",
            updated_by=d.get("updated_by"), updated_by_name=d.get("updated_by_name"),
        )
