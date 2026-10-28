from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Optional

from axo_control_plane.domain.plans import Resources

# What the VE SHOULD be (set by people / the sleep timer); the provisioner
# makes reality match it, one safe-to-repeat step at a time.
DESIRED_RUNNING = "running"
DESIRED_STOPPED = "stopped"
DESIRED_SLEEPING = "sleeping"
DESIRED_DELETED = "deleted"
DESIRED_STATES = (DESIRED_RUNNING, DESIRED_STOPPED, DESIRED_SLEEPING, DESIRED_DELETED)

# lifecycle_reason while the VEM sends no heartbeats; cleared by the next one.
VEM_DOWN_REASON = "the VE manager is not responding"

# What the VE IS doing (the shared vocabulary of upgrade.md section 2).
CREATING = "Creating"
RUNNING = "Running"
SCALING = "Scaling"
STOPPING = "Stopping"
STOPPED = "Stopped"
STARTING = "Starting"
RESTORING = "Restoring"
SLEEPING = "Sleeping"
WAKING = "Waking"
DELETING = "Deleting"
DELETED = "Deleted"
FAILED = "Failed"

OWNER_USER = "user"
OWNER_TEAM = "team"


@dataclass(frozen=True)
class Owner:
    kind: str           # user | team
    id: str             # Xolo user key / team id
    name: str           # the shared user/team name (bucket prefix)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Owner":
        return cls(kind=data["kind"], id=data["id"], name=data["name"])


@dataclass(frozen=True)
class VirtualEnvironment:
    """The Control Plane's record of one VE. ``reserved`` = quota + plan base,
    taken from platform capacity at purchase and released at deletion."""

    ve_id: str
    name: str
    owner: Owner
    plan_id: str
    quota: Resources
    endpoint_size_name: str
    endpoint_size: Resources
    target_endpoints: int
    min_endpoints: int
    reserved: Resources
    created_by: str
    created_at: float
    desired: str = DESIRED_RUNNING
    lifecycle: str = CREATING
    health: Optional[str] = None
    lifecycle_reason: str = ""
    # Provisioning progress (the last step done / failing step).
    step: str = ""
    step_error: str = ""
    # Latest VEM heartbeat (only the latest is kept; changes go to history).
    heartbeat: Dict[str, Any] = field(default_factory=dict)
    heartbeat_at: Optional[float] = None
    vem_down_since: Optional[float] = None
    idle_since: Optional[float] = None
    wake_requested_at: Optional[float] = None
    stopped_at: Optional[float] = None
    deleted_at: Optional[float] = None
    purge_after: Optional[float] = None
    # Rental (upgrade.md 13.2): monthly periods from the purchase moment, the
    # full plan fee each period. ``period_fee_cents`` = what this period was
    # paid at (an upgrade pays the difference up to the new fee).
    period_anchor: Optional[float] = None
    period_anchor_index: int = 0
    period_index: int = 0
    period_start: Optional[float] = None
    period_end: Optional[float] = None
    period_fee_cents: int = 0
    cancel_at_period_end: bool = False
    # A downgrade waits for the next renewal: {"plan_id", "quota", "by", "at"}.
    pending_change: Optional[Dict[str, Any]] = None
    # Set when a cancelled rental ran out (the VE is stopped, data kept until
    # ``rental_delete_after``).
    rental_ended_at: Optional[float] = None
    rental_delete_after: Optional[float] = None
    # Per-period budget the owner set (warnings only, at 80 % and 100 %).
    budget_cents: Optional[int] = None
    version: int = 0

    @property
    def network(self) -> str:
        return f"ve-{self.ve_id}-net"

    def container(self, role: str) -> str:
        return f"ve-{self.ve_id}-{role}"

    @property
    def visible(self) -> bool:
        return self.desired != DESIRED_DELETED

    def to_doc(self) -> Dict[str, Any]:
        out = asdict(self)
        out["_id"] = self.ve_id
        out["owner"] = self.owner.to_dict()
        out["quota"] = self.quota.to_dict()
        out["endpoint_size"] = self.endpoint_size.to_dict()
        out["reserved"] = self.reserved.to_dict()
        return out

    @classmethod
    def from_doc(cls, doc: Dict[str, Any]) -> "VirtualEnvironment":
        data = {k: v for k, v in doc.items() if k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data["owner"] = Owner.from_dict(doc["owner"])
        data["quota"] = Resources.from_dict(doc["quota"])
        data["endpoint_size"] = Resources.from_dict(doc["endpoint_size"])
        data["reserved"] = Resources.from_dict(doc["reserved"])
        return cls(**data)

    def with_changes(self, **changes: Any) -> "VirtualEnvironment":
        return replace(self, **changes)

    def public(self) -> Dict[str, Any]:
        """What the UI sees (no internal provisioning detail)."""
        hb = self.heartbeat or {}
        return {
            "ve_id": self.ve_id,
            "name": self.name,
            "owner": self.owner.to_dict(),
            "plan_id": self.plan_id,
            "quota": self.quota.to_dict(),
            "endpoint_size": {"name": self.endpoint_size_name, **self.endpoint_size.to_dict()},
            "target_endpoints": hb.get("target_endpoints", self.target_endpoints),
            "min_endpoints": hb.get("min_endpoints", self.min_endpoints),
            "endpoints_running": hb.get("running", 0),
            "lifecycle": self.lifecycle,
            "health": self.health,
            "lifecycle_reason": self.lifecycle_reason,
            "desired": self.desired,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "idle_since": self.idle_since,
            "heartbeat_at": self.heartbeat_at,
            "rental": {
                "period_start": self.period_start,
                "period_end": self.period_end,
                "period_fee_cents": self.period_fee_cents,
                "cancel_at_period_end": self.cancel_at_period_end,
                "pending_change": self.pending_change,
                "ended_at": self.rental_ended_at,
                "delete_after": self.rental_delete_after,
                "budget_cents": self.budget_cents,
            },
        }
