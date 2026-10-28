from __future__ import annotations

from typing import Any, Dict, Optional

from axo_vem.domain.errors import InvalidRequestError
from axo_vem.domain.virtual_environment.settings import PlanLimits

MB = 1024 * 1024

# What an endpoint keeps for itself out of its size (axo_endpoint's
# AXO_ENDPOINT_RESERVED_CPU_CORES / _MEMORY_MB defaults): its runners share
# the rest.
ENDPOINT_RESERVED_CPU_CORES = 0.25
ENDPOINT_RESERVED_MEMORY_MB = 256


def _cores(value: float) -> str:
    return f"{value:g} CPU"


def _memory(mb: int) -> str:
    return f"{mb / 1024:g} GB" if mb >= 1024 else f"{mb} MB"


def with_runner_size(spec: Dict[str, Any], limits: PlanLimits, endpoint_cpu_cores: float,
                     endpoint_memory_mb: int) -> Dict[str, Any]:
    """A container function's runtime_spec with its runner size settled:
    the size it asks for (``cpu_limit`` / ``memory_limit_bytes``) or else the
    plan's default (no bigger than one endpoint's room) -- kept on the
    function, so a later plan change never resizes it silently. Refused
    (RUNNER_SIZE_INVALID) when bigger than the plan allows or than one
    endpoint's room for runners. A process function is returned as it is."""
    if (spec or {}).get("type") != "container":
        return spec
    out = dict(spec)
    # The default never makes a function unusable: it shrinks to what one
    # endpoint has room for. Only a size asked for explicitly is refused.
    room_cpu = endpoint_cpu_cores - ENDPOINT_RESERVED_CPU_CORES
    room_mb = endpoint_memory_mb - ENDPOINT_RESERVED_MEMORY_MB
    if out.get("cpu_limit") is None and limits.runner_cpu_cores is not None:
        out["cpu_limit"] = min(limits.runner_cpu_cores, room_cpu)
    if out.get("memory_limit_bytes") is None and limits.runner_memory_mb is not None:
        out["memory_limit_bytes"] = min(limits.runner_memory_mb, room_mb) * MB
    problem = runner_size_problem(out.get("cpu_limit"), out.get("memory_limit_bytes"), limits,
                                  endpoint_cpu_cores, endpoint_memory_mb)
    if problem:
        raise InvalidRequestError(problem, error_name="RUNNER_SIZE_INVALID")
    return out


def runner_size_problem(cpu: Optional[float], memory_bytes: Optional[int], limits: PlanLimits,
                        endpoint_cpu_cores: float, endpoint_memory_mb: int) -> Optional[str]:
    """Why this runner size can't be used (None = it can)."""
    if cpu is not None and cpu <= 0:
        return "a runner needs more than 0 CPU"
    if memory_bytes is not None and memory_bytes < 64 * MB:
        return "a runner needs at least 64 MB of memory"
    if cpu is not None and limits.runner_max_cpu_cores is not None and cpu > limits.runner_max_cpu_cores + 1e-9:
        return f"the plan allows runners of up to {_cores(limits.runner_max_cpu_cores)}"
    if (memory_bytes is not None and limits.runner_max_memory_mb is not None
            and memory_bytes > limits.runner_max_memory_mb * MB):
        return f"the plan allows runners of up to {_memory(limits.runner_max_memory_mb)} of memory"
    room_cpu = endpoint_cpu_cores - ENDPOINT_RESERVED_CPU_CORES
    room_mb = endpoint_memory_mb - ENDPOINT_RESERVED_MEMORY_MB
    if cpu is not None and cpu > room_cpu + 1e-9:
        return (f"a runner of {_cores(cpu)} doesn't fit in this VE's endpoints "
                f"({_cores(endpoint_cpu_cores)}, {_cores(room_cpu)} left for runners)")
    if memory_bytes is not None and memory_bytes > room_mb * MB:
        return (f"a runner with {_memory(memory_bytes // MB)} doesn't fit in this VE's endpoints "
                f"({_memory(endpoint_memory_mb)}, {_memory(room_mb)} left for runners)")
    return None
