from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from axo_shared.metrics import MetricRecord, StatSummary, rollup
from axo_shared.metrics.timebins import freeze_dims

# What a policy (or the UI) can ask for over a window.
STATISTICS = ("avg", "median", "p90", "p95", "p99", "min", "max", "total", "count")


def statistic_of(stats: StatSummary, statistic: str) -> Optional[float]:
    if statistic == "avg":
        return stats.mean()
    if statistic == "median":
        return stats.quantile(0.5)
    if statistic in ("p90", "p95", "p99"):
        return stats.quantile(int(statistic[1:]) / 100)
    if statistic == "min":
        return stats.min if stats.count else None
    if statistic == "max":
        return stats.max if stats.count else None
    if statistic == "total":
        return stats.sum
    if statistic == "count":
        return float(stats.count)
    raise ValueError(f"unknown statistic {statistic!r}")


class MetricsStore:
    """The live tier: every endpoint's 10-second statistics bins
    (MetricSummaryReported) for the last ``keep_seconds``, in memory only.
    Bins merge exactly (sum, sum of squares, min/max, DDSketch), so a value
    over any window and across endpoints is exact -- what scaling policies
    evaluate, and what the 1-minute summaries for the Control Plane are
    rolled up from."""

    def __init__(self, keep_seconds: float = 3600.0, now_fn: Callable[[], float] = time.time) -> None:
        self._keep = keep_seconds
        self._now_fn = now_fn
        self._records: List[MetricRecord] = []
        self._lock = threading.Lock()

    def ingest(self, data: Dict[str, Any]) -> int:
        """One MetricSummaryReported (as a dict). Returns how many metrics it carried."""
        bin_start = float(data["bin_start"])
        width = int(data["bin_width_s"])
        component = data.get("component_id") or data.get("endpoint_id") or ""
        added = []
        for summary in data.get("summaries", []):
            dims = dict(summary.get("dims") or {})
            dims.setdefault("component", component)
            added.append(MetricRecord(
                metric=summary["metric"], dims=freeze_dims(dims), bin_start=bin_start, width_s=width,
                stats=StatSummary.from_dict(summary["stats"]),
            ))
        with self._lock:
            self._records.extend(added)
            self._prune_locked()
        return len(added)

    def _prune_locked(self) -> None:
        cutoff = self._now_fn() - self._keep
        if self._records and self._records[0].bin_start < cutoff:
            self._records = [r for r in self._records if r.bin_start >= cutoff]

    def summary(self, metric: str, window_s: float, dims: Optional[Dict[str, str]] = None) -> Optional[StatSummary]:
        """Every bin of ``metric`` from the last ``window_s`` whose dims include
        ``dims``, merged into one -- None if there was no data."""
        since = self._now_fn() - window_s
        wanted = set((dims or {}).items())
        merged: Optional[StatSummary] = None
        with self._lock:
            for record in self._records:
                if record.metric != metric or record.bin_start < since or not wanted <= set(record.dims):
                    continue
                if merged is None:
                    merged = StatSummary.from_dict(record.stats.to_dict())
                else:
                    merged.merge(record.stats)
        return merged

    def value(self, metric: str, statistic: str, window_s: float, dims: Optional[Dict[str, str]] = None) -> Optional[float]:
        stats = self.summary(metric, window_s, dims)
        return statistic_of(stats, statistic) if stats is not None and stats.count else None

    def minute_summaries(self, before: float) -> List[MetricRecord]:
        """1-minute rollups of every complete minute that ended at or before
        ``before`` (exact merges of the 10 s bins), endpoint dimension dropped
        -- what the Control Plane receives."""
        with self._lock:
            records = [r for r in self._records if r.bin_start + r.width_s <= before]
        without_component = [
            MetricRecord(metric=r.metric, dims=freeze_dims({k: v for k, v in r.dims if k != "component"}),
                         bin_start=r.bin_start, width_s=r.width_s, stats=r.stats)
            for r in records if 60 % r.width_s == 0
        ]
        rolled = rollup(without_component, 60)
        return rolled.unwrap() if rolled.is_ok else []
