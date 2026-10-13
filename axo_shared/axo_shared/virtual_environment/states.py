from __future__ import annotations

from typing import Literal, Optional, Tuple

# What a VE is doing (its lifecycle). Restoring = after a start, the VEM
# replays its events to re-register functions/data into the endpoints;
# Sleeping = like Stopped, but the next request wakes it up automatically.
VeLifecycle = Literal[
    "Creating",
    "Running",
    "Scaling",
    "Stopping",
    "Stopped",
    "Starting",
    "Restoring",
    "Sleeping",
    "Deleting",
    "Deleted",
    "Failed",
]
VE_LIFECYCLES: Tuple[str, ...] = VeLifecycle.__args__  # type: ignore[attr-defined]

# How well a VE works. Not reported (None) while it is Stopped or Sleeping.
VeHealth = Literal["Healthy", "Degraded", "Unavailable"]
VE_HEALTHS: Tuple[str, ...] = VeHealth.__args__  # type: ignore[attr-defined]

# One endpoint's state. Draining = no new jobs, finishing running ones, then removed.
EndpointState = Literal["Creating", "Running", "Draining", "Removing", "Removed", "Stopped", "Failed"]
ENDPOINT_STATES: Tuple[str, ...] = EndpointState.__args__  # type: ignore[attr-defined]

_NO_HEALTH: Tuple[str, ...] = ("Stopped", "Sleeping")


def compute_health(
    lifecycle: str,
    *,
    vem_up: bool,
    has_leader: bool,
    running: int,
    failed: int,
    min_endpoints: int,
) -> Optional[VeHealth]:
    """The agreed health rules, in one place:

    - None while the VE is Stopped or Sleeping (health isn't shown);
    - Unavailable: fewer running endpoints than ``min_endpoints``, or the VEM
      is down, or there is no leader -- jobs can't run;
    - Degraded: at least one endpoint failed unexpectedly -- jobs still run;
    - Healthy otherwise.

    Only unplanned failures count: endpoints that are starting (scale-up) or
    leaving (Draining/Removing on scale-down) are neutral, which is why they
    are not even inputs here.
    """
    if lifecycle in _NO_HEALTH:
        return None
    if not vem_up or not has_leader or running < min_endpoints:
        return "Unavailable"
    if failed > 0:
        return "Degraded"
    return "Healthy"
