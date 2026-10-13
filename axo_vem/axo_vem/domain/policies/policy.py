from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional

from axo_shared.metrics import names as m

ENDPOINT = "endpoint"   # changes the VE's target_endpoints
RUNNER = "runner"       # changes one function's max_concurrency

COMPARATORS = {
    ">": lambda value, threshold: value > threshold,
    "<": lambda value, threshold: value < threshold,
    ">=": lambda value, threshold: value >= threshold,
    "<=": lambda value, threshold: value <= threshold,
}
STATISTICS = ("avg", "median", "p90", "p95", "p99", "min", "max", "total", "count")
METRICS = (
    m.JOB_TOTAL_MS, m.JOB_WAIT_MS, m.JOB_EXEC_MS, m.JOB_COLD_START_MS,
    m.QUEUE_LENGTH, m.QUEUE_FULLNESS_PCT,
    m.REQUESTS_RECEIVED, m.REQUESTS_REJECTED, m.REQUESTS_TIMED_OUT, m.REQUESTS_RETRIED,
    m.RESOURCE_CPU_PCT, m.RESOURCE_RAM_PCT,
)


def stream_name(virtual_environment_id: str) -> str:
    return f"ve-policies-{virtual_environment_id}"


@dataclass(frozen=True)
class ScalingPolicy:
    """A rule that changes a target for you, through the same path as a
    manual change:

        WHEN    <statistic> of <metric> <comparator> <threshold>  during <duration_s>
        THEN    target <delta> (endpoint: new endpoints of this size)
        LIMITS  min · max · cooldown
    """

    name: str
    kind: str                       # ENDPOINT | RUNNER
    metric: str
    statistic: str
    comparator: str
    threshold: float
    duration_s: float               # how long the condition must hold
    delta: int                      # +1 = one more endpoint / runner, -1 = one fewer
    min_value: int                  # never below (endpoints, or the function's runners)
    max_value: int                  # never above
    cooldown_s: float = 300.0       # time after acting before this policy acts again
    window_s: float = 60.0          # the statistic is computed over this trailing window
    dims: Dict[str, str] = field(default_factory=dict)  # e.g. {"layer": "endpoint"}
    function_id: Optional[str] = None       # runner policies
    function_version: Optional[int] = None
    new_endpoint_cpu_cores: Optional[float] = None   # endpoint policies scaling up
    new_endpoint_memory_mb: Optional[int] = None
    dry_run: bool = False
    enabled: bool = True
    policy_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScalingPolicy":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def with_changes(self, changes: Dict[str, Any]) -> "ScalingPolicy":
        return replace(self, **{k: v for k, v in changes.items() if k != "policy_id"})

    def metric_dims(self) -> Dict[str, str]:
        dims = dict(self.dims)
        if self.kind == RUNNER and self.function_id:
            dims.setdefault(m.DIM_FUNCTION, self.function_id)
        return dims

    def describe(self) -> str:
        return f"{self.statistic} {self.metric} {self.comparator} {self.threshold:g}"


def validate(policy: ScalingPolicy) -> List[str]:
    errors: List[str] = []
    if not policy.name:
        errors.append("name is required")
    if policy.kind not in (ENDPOINT, RUNNER):
        errors.append("kind must be 'endpoint' or 'runner'")
    if policy.kind == RUNNER and (not policy.function_id or policy.function_version is None):
        errors.append("runner policies need function_id and function_version")
    if policy.metric not in METRICS:
        errors.append(f"unknown metric {policy.metric!r}")
    if policy.statistic not in STATISTICS:
        errors.append(f"statistic must be one of {', '.join(STATISTICS)}")
    if policy.comparator not in COMPARATORS:
        errors.append("comparator must be one of > < >= <=")
    if not isinstance(policy.delta, int) or policy.delta == 0:
        errors.append("delta must be a non-zero whole number")
    if policy.min_value < 0 or policy.max_value < policy.min_value:
        errors.append("need 0 ≤ min_value ≤ max_value")
    if policy.kind == RUNNER and policy.min_value < 1:
        errors.append("a function needs at least 1 runner (min_value ≥ 1)")
    if policy.duration_s < 0 or policy.cooldown_s < 0 or policy.window_s <= 0:
        errors.append("duration_s and cooldown_s must be ≥ 0, window_s > 0")
    return errors


# Built-in templates: a starting point the user adjusts in a form.
TEMPLATES: Dict[str, Dict[str, Any]] = {
    "keep-response-time-under": {
        "description": "Add an endpoint while p95 response time stays above a limit.",
        "policy": {"kind": ENDPOINT, "metric": m.JOB_TOTAL_MS, "statistic": "p95", "comparator": ">",
                   "threshold": 500, "duration_s": 120, "delta": 1, "min_value": 1, "max_value": 4},
    },
    "keep-queue-short": {
        "description": "Add an endpoint while the endpoints' job queues stay long.",
        "policy": {"kind": ENDPOINT, "metric": m.QUEUE_LENGTH, "statistic": "avg", "comparator": ">",
                   "threshold": 10, "duration_s": 60, "delta": 1, "min_value": 1, "max_value": 4,
                   "dims": {m.DIM_QUEUE: "dispatcher"}},
    },
    "keep-cpu-under": {
        "description": "Add an endpoint while CPU stays above a percentage.",
        "policy": {"kind": ENDPOINT, "metric": m.RESOURCE_CPU_PCT, "statistic": "avg", "comparator": ">",
                   "threshold": 80, "duration_s": 300, "delta": 1, "min_value": 1, "max_value": 4},
    },
    "remove-when-quiet": {
        "description": "Remove an endpoint while there are almost no requests.",
        "policy": {"kind": ENDPOINT, "metric": m.REQUESTS_RECEIVED, "statistic": "total", "comparator": "<",
                   "threshold": 10, "duration_s": 600, "window_s": 600, "delta": -1, "min_value": 1,
                   "max_value": 4},
    },
    "function-waits-too-long": {
        "description": "Allow one more runner of a function while its jobs wait too long.",
        "policy": {"kind": RUNNER, "metric": m.JOB_WAIT_MS, "statistic": "avg", "comparator": ">",
                   "threshold": 1000, "duration_s": 60, "delta": 1, "min_value": 1, "max_value": 8},
    },
}
