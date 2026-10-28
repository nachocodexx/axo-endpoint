from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from axo_shared.events.models import is_billable

from axo_control_plane.domain.plans import PriceList

UCENTS_PER_CENT = 1_000_000


@dataclass(frozen=True)
class AttemptCost:
    """What one job attempt costs, worked out once when it reaches the
    Control Plane and saved on its record (upgrade.md 13.1).

    Amounts are micro-cents (integers); ``prices_from`` is when the price
    list used took effect. A non-billable attempt (platform failure, timed
    out in queue) costs 0 but still shows its quantities."""

    billable: bool
    requests: int
    core_seconds: float
    gb_seconds: float
    request_ucents: int
    compute_ucents: int
    memory_ucents: int
    total_ucents: int
    prices_from: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def attempt_cost(record: Dict[str, Any], prices: PriceList, prices_from: float) -> AttemptCost:
    """1 request + execution time x endpoint size (core-seconds, GB-seconds).
    A cancelled attempt is billed for the time it ran (its ``execution_ms``)."""
    seconds = max(float(record.get("execution_ms") or 0.0), 0.0) / 1000.0
    core_seconds = seconds * float(record.get("endpoint_cores") or 0.0)
    gb_seconds = seconds * float(record.get("endpoint_ram_mb") or 0) / 1024.0
    billable = is_billable(str(record.get("outcome", "")))
    if billable:
        request = prices.per_request_ucents
        compute = round(core_seconds * prices.per_core_second_ucents)
        memory = round(gb_seconds * prices.per_gb_second_ucents)
    else:
        request = compute = memory = 0
    return AttemptCost(billable=billable, requests=1, core_seconds=round(core_seconds, 6),
                       gb_seconds=round(gb_seconds, 6), request_ucents=request, compute_ucents=compute,
                       memory_ucents=memory, total_ucents=request + compute + memory, prices_from=prices_from)


def choreography_lines(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A choreography step job's place in every choreography it counts for:
    its own run's step, then -- when another choreography's step called that
    run -- each calling run's step, outermost last
    ({choreography_id, run_id, step, version, caller}; ``caller`` = the
    choreography that called that level, None when started by hand / a
    trigger). The Costs tabs read one line per choreography."""
    if record.get("started_by_type") != "choreography" or not record.get("started_by_id"):
        return []
    calls = [c for c in record.get("choreography_calls") or [] if isinstance(c, dict)]
    lines = [{"choreography_id": record["started_by_id"], "run_id": record.get("choreography_run_id"),
              "step": record.get("choreography_step"), "version": record.get("choreography_version"),
              "caller": calls[-1].get("choreography_id") if calls else None}]
    for index in range(len(calls) - 1, -1, -1):
        call = calls[index]
        lines.append({"choreography_id": call.get("choreography_id"), "run_id": call.get("run_id"),
                      "step": call.get("step"), "version": call.get("version"),
                      "caller": calls[index - 1].get("choreography_id") if index else None})
    return lines
