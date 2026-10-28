from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Dict, List, Optional

from axo_shared.runtime.results import KEEP_RESULT_CHOICES, NEVER, longer as keep_longer


def stream_name(virtual_environment_id: str) -> str:
    """The one stream holding every settings change of this VE."""
    return f"ve-settings-{virtual_environment_id}"


@dataclass(frozen=True)
class PlanLimits:
    """Hard limits the Control Plane sets when it launches the VEM (from
    the VE's plan and the platform admin's caps). Settings can never go past
    them."""

    max_endpoints: int = 1
    policies_allowed: bool = False
    max_retention_days: Optional[int] = None   # None = no plan ceiling
    max_queue_limit: Optional[int] = None      # platform cap per queue; None = none
    max_wait_seconds: Optional[float] = None   # platform cap for max_wait N; None = none
    allow_wait_forever: bool = True            # whether max_wait -1 is allowed
    # The VE quota (bought with the VE): every endpoint's size together must
    # fit. None = no quota (local dev without a Control Plane).
    quota_cpu_cores: Optional[float] = None
    quota_memory_mb: Optional[int] = None
    # Active objects (per plan): the largest state of one object (None = no
    # cap), JSON values up to this size kept in memory (hot), and the past
    # revisions kept per object: the default and the most an object may ask.
    object_max_state_bytes: Optional[int] = None
    object_hot_max_bytes: int = 4096
    object_keep_revisions: int = 10
    object_max_keep_revisions: Optional[int] = None
    # History past the retention is hidden at once and deleted for good this
    # many days later (platform-wide, set by the Control Plane).
    history_grace_days: int = 7
    # Container functions' runners (per plan): the size a function gets when
    # it doesn't ask for one, and the most it may ask. None = no plan rule
    # (local dev without a Control Plane).
    runner_cpu_cores: Optional[float] = None
    runner_memory_mb: Optional[int] = None
    runner_max_cpu_cores: Optional[float] = None
    runner_max_memory_mb: Optional[int] = None

    def state_limits(self) -> Dict[str, Any]:
        """What each object operation carries to the endpoints."""
        return {"max_state_bytes": self.object_max_state_bytes, "hot_max_bytes": self.object_hot_max_bytes,
                "keep_revisions": self.object_keep_revisions, "max_keep_revisions": self.object_max_keep_revisions}

    def keep_revisions_problem(self, keep: int) -> Optional[str]:
        if keep < 0:
            return "keep_revisions can't be negative"
        if self.object_max_keep_revisions is not None and keep > self.object_max_keep_revisions:
            return f"the plan keeps at most {self.object_max_keep_revisions} past revisions per object"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanLimits":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def quota_problem(self, cpu_cores: float, memory_mb: int) -> Optional[str]:
        """Why this much reserved size doesn't fit the quota (None = it fits)."""
        if self.quota_cpu_cores is not None and cpu_cores > self.quota_cpu_cores + 1e-9:
            return f"needs {cpu_cores:g} CPU, the VE quota is {self.quota_cpu_cores:g} CPU"
        if self.quota_memory_mb is not None and memory_mb > self.quota_memory_mb:
            return f"needs {memory_mb / 1024:g} GB, the VE quota is {self.quota_memory_mb / 1024:g} GB"
        return None


@dataclass(frozen=True)
class VeSettings:
    """What the VE's owner/admins (or policies) control. The VEM acts on
    these all the time: reconciling endpoints, waking sleepers, hiding old
    history."""

    target_endpoints: int = 1
    min_endpoints: int = 1
    auto_replace_failed: bool = True
    # Size of endpoints created to reach the target (explicit numbers --
    # the Control Plane translates size presets like "medium").
    new_endpoint_cpu_cores: float = 1.0
    new_endpoint_memory_mb: int = 1024
    # None = endpoints never sleep when idle.
    endpoint_idle_seconds: Optional[float] = None
    # Jobs waiting longer than this wake one sleeping endpoint.
    wake_wait_seconds: float = 2.0
    # max_wait for a job that doesn't set its own: -1 forever, 0 don't wait, N seconds.
    default_max_wait_seconds: float = -1.0
    # Queue limits per layer; None = the platform default.
    vem_queue_limit: Optional[int] = None
    endpoint_queue_limit: Optional[int] = None
    runner_queue_limit: Optional[int] = None
    # How long history is shown; None = up to the plan maximum.
    retention_days: Optional[int] = None
    # How long job results are kept (axo_shared.runtime.results choices):
    # the longest a job may ask for, and what a job that doesn't choose gets.
    result_keep_max: str = NEVER
    result_keep_default: str = "7d"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def with_changes(self, changes: Dict[str, Any]) -> "VeSettings":
        return replace(self, **changes)

    def effective_retention_days(self, limits: PlanLimits) -> Optional[int]:
        """What retention actually applies: the chosen value, else the plan maximum."""
        return self.retention_days if self.retention_days is not None else limits.max_retention_days


SETTING_NAMES = frozenset(f.name for f in fields(VeSettings))


def validate_changes(current: VeSettings, changes: Dict[str, Any], limits: PlanLimits) -> List[str]:
    """Every reason ``changes`` can't be applied (empty = fine), checked on
    the settings as they'd be after the change."""
    unknown = sorted(set(changes) - SETTING_NAMES)
    if unknown:
        return [f"unknown setting(s): {', '.join(unknown)}"]
    errors: List[str] = []
    try:
        after = current.with_changes(changes)
    except TypeError as exc:  # pragma: no cover - replace() only fails on unknown names
        return [str(exc)]

    def is_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    def is_number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if not is_int(after.min_endpoints) or after.min_endpoints < 0:
        errors.append("min_endpoints must be a whole number ≥ 0")
    if not is_int(after.target_endpoints) or after.target_endpoints < 0:
        errors.append("target_endpoints must be a whole number ≥ 0")
    if not errors:
        if after.target_endpoints < after.min_endpoints:
            errors.append(f"target_endpoints ({after.target_endpoints}) is below min_endpoints ({after.min_endpoints})")
        if after.target_endpoints > limits.max_endpoints:
            errors.append(f"target_endpoints ({after.target_endpoints}) is above the plan maximum ({limits.max_endpoints})")
    if not is_number(after.new_endpoint_cpu_cores) or after.new_endpoint_cpu_cores <= 0:
        errors.append("new_endpoint_cpu_cores must be > 0")
    if not is_int(after.new_endpoint_memory_mb) or after.new_endpoint_memory_mb < 128:
        errors.append("new_endpoint_memory_mb must be a whole number ≥ 128")
    if not errors and is_int(after.target_endpoints):
        problem = limits.quota_problem(after.target_endpoints * after.new_endpoint_cpu_cores,
                                       after.target_endpoints * after.new_endpoint_memory_mb)
        if problem:
            errors.append(f"{after.target_endpoints} endpoint(s) of {after.new_endpoint_cpu_cores:g} CPU / "
                          f"{after.new_endpoint_memory_mb / 1024:g} GB: {problem}")
    if not isinstance(after.auto_replace_failed, bool):
        errors.append("auto_replace_failed must be true or false")
    if after.endpoint_idle_seconds is not None and (not is_number(after.endpoint_idle_seconds) or after.endpoint_idle_seconds <= 0):
        errors.append("endpoint_idle_seconds must be > 0, or null for never")
    if not is_number(after.wake_wait_seconds) or after.wake_wait_seconds <= 0:
        errors.append("wake_wait_seconds must be > 0")

    wait = after.default_max_wait_seconds
    if not is_number(wait) or (wait < 0 and wait != -1):
        errors.append("default_max_wait_seconds must be -1 (forever), 0 (don't wait) or a number of seconds")
    elif wait == -1 and not limits.allow_wait_forever:
        errors.append("waiting forever (-1) is not allowed on this platform")
    elif wait > 0 and limits.max_wait_seconds is not None and wait > limits.max_wait_seconds:
        errors.append(f"default_max_wait_seconds is above the platform maximum ({limits.max_wait_seconds:g} s)")

    for name in ("vem_queue_limit", "endpoint_queue_limit", "runner_queue_limit"):
        value = getattr(after, name)
        if value is None:
            continue
        if not is_int(value) or value < 1:
            errors.append(f"{name} must be a whole number ≥ 1, or null for the platform default")
        elif limits.max_queue_limit is not None and value > limits.max_queue_limit:
            errors.append(f"{name} is above the platform maximum ({limits.max_queue_limit})")

    if after.retention_days is not None:
        if not is_int(after.retention_days) or after.retention_days < 1:
            errors.append("retention_days must be a whole number ≥ 1, or null for the plan maximum")
        elif limits.max_retention_days is not None and after.retention_days > limits.max_retention_days:
            errors.append(f"retention_days is above the plan maximum ({limits.max_retention_days})")

    choices = ", ".join(KEEP_RESULT_CHOICES)
    for name in ("result_keep_max", "result_keep_default"):
        if getattr(after, name) not in KEEP_RESULT_CHOICES:
            errors.append(f"{name} must be one of {choices}")
    if not errors and keep_longer(after.result_keep_default, after.result_keep_max):
        errors.append(f"result_keep_default ({after.result_keep_default}) is longer than "
                      f"result_keep_max ({after.result_keep_max})")
    return errors
