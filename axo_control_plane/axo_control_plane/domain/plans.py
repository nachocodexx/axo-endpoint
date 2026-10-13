from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional

from axo_control_plane.domain.errors import InvalidError


@dataclass(frozen=True)
class Resources:
    """CPU cores + memory. Everything the platform hands out is counted in
    these two numbers (plan bases, VE quotas, endpoint sizes)."""

    cpu_cores: float = 0.0
    memory_mb: int = 0

    def __add__(self, other: "Resources") -> "Resources":
        return Resources(round(self.cpu_cores + other.cpu_cores, 3), self.memory_mb + other.memory_mb)

    def __sub__(self, other: "Resources") -> "Resources":
        return Resources(round(self.cpu_cores - other.cpu_cores, 3), self.memory_mb - other.memory_mb)

    def times(self, n: int) -> "Resources":
        return Resources(round(self.cpu_cores * n, 3), self.memory_mb * n)

    def fits_in(self, other: "Resources") -> bool:
        return self.cpu_cores <= other.cpu_cores + 1e-9 and self.memory_mb <= other.memory_mb

    def describe(self) -> str:
        return f"{self.cpu_cores:g} CPU / {self.memory_mb / 1024:g} GB"

    def to_dict(self) -> Dict[str, Any]:
        return {"cpu_cores": self.cpu_cores, "memory_mb": self.memory_mb}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Resources":
        data = data or {}
        return cls(float(data.get("cpu_cores", 0.0)), int(data.get("memory_mb", 0)))


# Endpoint size presets (plus "custom" within the Platform Admin's limits).
ENDPOINT_SIZES: Dict[str, Resources] = {
    "small": Resources(1.0, 1024),
    "medium": Resources(2.0, 4096),
    "large": Resources(4.0, 8192),
}

# Base stack sizes (VEM + Kurrent + Mongo), outside the VE quota.
BASE_SIZES: Dict[str, Resources] = {
    "small": Resources(1.0, 2048),
    "medium": Resources(2.0, 4096),
    "large": Resources(4.0, 8192),
}


def endpoint_size(name: Optional[str], cpu_cores: Optional[float] = None, memory_mb: Optional[int] = None,
                  max_custom: Optional[Resources] = None) -> Resources:
    """A preset by name, or a custom size (name "custom" or None + numbers)."""
    if name and name != "custom":
        if name not in ENDPOINT_SIZES:
            raise InvalidError(f"unknown endpoint size {name!r}; use one of {sorted(ENDPOINT_SIZES)} or custom")
        return ENDPOINT_SIZES[name]
    if cpu_cores is None or memory_mb is None:
        raise InvalidError("a custom endpoint size needs cpu_cores and memory_mb")
    size = Resources(float(cpu_cores), int(memory_mb))
    if size.cpu_cores < 0.25 or size.memory_mb < 256:
        raise InvalidError("an endpoint needs at least 0.25 CPU and 256 MB")
    if max_custom is not None and not size.fits_in(max_custom):
        raise InvalidError(f"a custom endpoint can be at most {max_custom.describe()}")
    return size


@dataclass(frozen=True)
class Plan:
    """What a VE is bought with. Prices are in cents (integers, no float
    money). ``request_limit`` is per month (Free only); ``sleeps`` = the whole
    VE sleeps after the idle time (Free only, not configurable)."""

    plan_id: str
    name: str
    max_endpoints: int
    base_size: str
    max_retention_days: int
    monthly_fee_cents: int = 0
    policies_allowed: bool = True
    sleeps: bool = False
    request_limit: Optional[int] = None
    personal_only: bool = False
    one_per_user: bool = False
    fixed_endpoint_size: Optional[str] = None
    active: bool = True
    order: int = 0

    @property
    def base(self) -> Resources:
        return BASE_SIZES[self.base_size]

    @property
    def is_free(self) -> bool:
        return self.monthly_fee_cents == 0

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["base"] = self.base.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def with_changes(self, **changes: Any) -> "Plan":
        return replace(self, **changes)


DEFAULT_PLANS: List[Plan] = [
    Plan("free", "Free", max_endpoints=1, base_size="small", max_retention_days=7,
         monthly_fee_cents=0, policies_allowed=False, sleeps=True, request_limit=10_000,
         personal_only=True, one_per_user=True, fixed_endpoint_size="small", order=0),
    Plan("basic", "Basic", max_endpoints=3, base_size="small", max_retention_days=30,
         monthly_fee_cents=500, order=1),
    Plan("standard", "Standard", max_endpoints=10, base_size="medium", max_retention_days=90,
         monthly_fee_cents=2500, order=2),
    Plan("pro", "Pro", max_endpoints=50, base_size="large", max_retention_days=180,
         monthly_fee_cents=9900, order=3),
    Plan("adaptive", "Adaptive", max_endpoints=100, base_size="large", max_retention_days=365,
         monthly_fee_cents=19900, order=4),
]


@dataclass(frozen=True)
class PriceList:
    """Usage prices, in micro-cents per unit so small per-request prices stay
    integers. A change applies from the moment it is saved."""

    per_request_ucents: int = 10            # $0.0000001 per request
    per_core_second_ucents: int = 1_000     # $0.00001
    per_gb_second_ucents: int = 150         # $0.0000015
    storage_per_gb_month_ucents: int = 2_300_000   # $0.023
    transfer_per_gb_ucents: int = 9_000_000        # $0.09

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceList":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: int(v) for k, v in data.items() if k in known})


@dataclass(frozen=True)
class VeShape:
    """What a buyer asks for: quota + endpoint size + target endpoints."""

    quota: Resources
    endpoint_size: Resources
    endpoint_size_name: str
    target_endpoints: int
    min_endpoints: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)


def check_shape(plan: Plan, shape: VeShape) -> None:
    """The buyer's choices against the plan (capacity is checked elsewhere)."""
    if shape.target_endpoints < 0:
        raise InvalidError("target endpoints can't be negative")
    if shape.min_endpoints < 0 or shape.min_endpoints > shape.target_endpoints:
        raise InvalidError("min endpoints must be between 0 and the target")
    if shape.target_endpoints > plan.max_endpoints:
        raise InvalidError(
            f"the {plan.name} plan allows at most {plan.max_endpoints} endpoint(s)",
            code="PLAN_LIMIT", details={"max_endpoints": plan.max_endpoints},
        )
    if plan.fixed_endpoint_size and shape.endpoint_size_name != plan.fixed_endpoint_size:
        raise InvalidError(f"the {plan.name} plan uses {plan.fixed_endpoint_size} endpoints only")
    needed = shape.endpoint_size.times(shape.target_endpoints)
    if not needed.fits_in(shape.quota):
        raise InvalidError(
            f"{shape.target_endpoints} endpoint(s) of {shape.endpoint_size.describe()} need {needed.describe()}, "
            f"the quota is {shape.quota.describe()}",
            code="QUOTA_TOO_SMALL", details={"needed": needed.to_dict(), "quota": shape.quota.to_dict()},
        )
    if not shape.endpoint_size.fits_in(shape.quota):
        raise InvalidError("one endpoint is bigger than the whole quota", code="QUOTA_TOO_SMALL")
