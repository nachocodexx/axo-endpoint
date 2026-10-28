"""Who sees runs and results.

Everyone with a role in the VE sees every function, object and
choreography (the VE is the privacy boundary), but the *runs and results*
are only for:

- the VE's Admins and Owner (always),
- who started them (the job's / run's ``acting_user_id``: the person, also
  when they used an SDK key; a trigger's creator for a triggered run),
- whoever the setting of the thing that ran shares them with: ``runner``
  (only who runs it, the default), ``ve`` (everyone in the VE) or ``teams``
  (members of the teams picked).

A job on its own follows its function's setting (an object call: its
object's); a job inside a choreography run follows the choreography's.
The Control Plane passes the caller's role and team ids with every call
(it reads them from Xolo, cached), so nothing here asks Xolo anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

RUNNER = "runner"
VE = "ve"
TEAMS = "teams"
MODES = (RUNNER, VE, TEAMS)
KINDS = ("function", "choreography", "object")
ALL_SEEING_ROLES = ("admin", "owner")


@dataclass(frozen=True)
class Viewer:
    """Who is looking. ``role`` None = not a person through the front door
    (the Control Plane itself, a test): sees everything."""

    user_id: str
    role: Optional[str] = None
    teams: FrozenSet[str] = frozenset()

    @property
    def sees_all(self) -> bool:
        return self.role is None or self.role in ALL_SEEING_ROLES


@dataclass
class Visibility:
    """One function's / choreography's / object's setting."""

    kind: str
    ref: str                                   # function id, choreography id or object name
    mode: str = RUNNER
    teams: List[str] = field(default_factory=list)
    set_by: Optional[str] = None
    set_at: Optional[str] = None

    def shares_with(self, viewer: Viewer) -> bool:
        if self.mode == VE:
            return True
        return self.mode == TEAMS and bool(viewer.teams & set(self.teams))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Visibility":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def resource_of(job: Dict[str, Any]) -> Tuple[str, str]:
    """Whose setting a job follows: its run's choreography, else its object
    (an object call), else its function."""
    choreography = job.get("choreography") or {}
    if choreography.get("choreography_id"):
        return "choreography", choreography["choreography_id"]
    if job.get("kind") == "object" and job.get("object_name"):
        return "object", job["object_name"]
    return "function", job.get("function_id") or ""


@dataclass(frozen=True)
class Shared:
    """What a viewer sees besides their own: the functions, choreographies
    and objects whose setting shares runs with them (for list queries)."""

    user_id: str
    function_ids: FrozenSet[str] = frozenset()
    choreography_ids: FrozenSet[str] = frozenset()
    object_names: FrozenSet[str] = frozenset()
