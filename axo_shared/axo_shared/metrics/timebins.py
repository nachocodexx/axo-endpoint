from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from option import Err, Ok, Result

from axo_shared.errors import AxoError, InvalidTierSpecError
from axo_shared.metrics.summary import StatSummary

Dims = Tuple[Tuple[str, str], ...]


def freeze_dims(dims: Optional[Dict[str, str]]) -> Dims:
    """Dimensions as a sorted tuple, so equal dicts give equal (hashable) keys."""
    return tuple(sorted((str(k), str(v)) for k, v in (dims or {}).items()))


def bin_start(ts: float, width_s: int) -> float:
    """Start of the clock-aligned bin containing ts (epoch seconds, i.e. UTC):
    every component computes the same boundaries, so bins line up and merge."""
    return math.floor(ts / width_s) * width_s


@dataclass
class MetricRecord:
    """One metric's StatSummary for one dims combination over one time bin."""

    metric: str
    dims: Dims
    bin_start: float
    width_s: int
    stats: StatSummary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "dims": dict(self.dims),
            "bin_start": self.bin_start,
            "width_s": self.width_s,
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricRecord":
        return cls(
            metric=data["metric"],
            dims=freeze_dims(data["dims"]),
            bin_start=float(data["bin_start"]),
            width_s=int(data["width_s"]),
            stats=StatSummary.from_dict(data["stats"]),
        )


def rollup(records: Iterable[MetricRecord], to_width_s: int) -> Result[List[MetricRecord], AxoError]:
    """Merge finer bins into coarser ones (e.g. fifteen 1-min bins -> one
    15-min bin). Exact for every statistic, since StatSummary merges exactly.
    Each record's width must divide to_width_s, or bins would straddle edges."""
    merged: Dict[Tuple[str, Dims, float], MetricRecord] = {}
    for record in records:
        if to_width_s % record.width_s:
            return Err(InvalidTierSpecError(
                f"cannot roll {record.width_s}s bins into {to_width_s}s bins",
                context={"from_width_s": record.width_s, "to_width_s": to_width_s},
            ))
        key = (record.metric, record.dims, bin_start(record.bin_start, to_width_s))
        target = merged.get(key)
        if target is None:
            target = MetricRecord(record.metric, record.dims, key[2], to_width_s,
                                  StatSummary(record.stats.sketch.relative_accuracy))
            merged[key] = target
        result = target.stats.merge(record.stats)
        if result.is_err:
            return result
    return Ok(sorted(merged.values(), key=lambda r: (r.bin_start, r.metric, r.dims)))


# ── Tiers ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Tier:
    """One resolution level: bins of width_s, kept for retention_s (None = forever).
    By convention the first (finest) tier is the live one, kept in memory only."""

    width_s: int
    retention_s: Optional[int]


DEFAULT_TIER_SPEC = "10s:1h,1m:7d,15m:90d,1d:forever"

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_DURATION = re.compile(r"^(\d+)([smhd])$")


def parse_duration(text: str) -> Optional[int]:
    """"10s" / "1m" / "15m" / "1h" / "7d" -> seconds; None if malformed."""
    match = _DURATION.match(text.strip())
    if not match or int(match.group(1)) == 0:
        return None
    return int(match.group(1)) * _UNITS[match.group(2)]


def parse_tiers(spec: str = DEFAULT_TIER_SPEC) -> Result[List[Tier], InvalidTierSpecError]:
    """Parse "width:retention,..." (finest first), e.g. the default
    "10s:1h,1m:7d,15m:90d,1d:forever". Each width must be a multiple of the
    previous one so coarser tiers can be built by rollup."""
    tiers: List[Tier] = []
    for part in [p for p in spec.split(",") if p.strip()]:
        pieces = part.split(":")
        width = parse_duration(pieces[0]) if len(pieces) == 2 else None
        keep = pieces[1].strip() if len(pieces) == 2 else ""
        retention = None if keep == "forever" else parse_duration(keep)
        if width is None or (retention is None and keep != "forever"):
            return Err(InvalidTierSpecError(f"bad tier {part!r}", context={"spec": spec}))
        if tiers and width % tiers[-1].width_s:
            return Err(InvalidTierSpecError(
                f"tier width {pieces[0]} is not a multiple of the previous tier",
                context={"spec": spec},
            ))
        tiers.append(Tier(width_s=width, retention_s=retention))
    if not tiers:
        return Err(InvalidTierSpecError("no tiers given", context={"spec": spec}))
    return Ok(tiers)
