from __future__ import annotations

import math
from typing import Any, Dict, Optional

from option import Ok, Result

from axo_shared.errors import AxoError
from axo_shared.metrics.sketch import LogSketch

SNAPSHOT_QUANTILES = {"median": 0.5, "p90": 0.9, "p95": 0.95, "p99": 0.99}


class StatSummary:
    """Everything needed to answer every statistic for one metric over one
    time bin, in a form that merges exactly across bins and components:

    - count, sum, sum of squares, min, max -> exact mean and standard
      deviation for any merged period (mean = sum/n,
      std = sqrt(sum_sq/n - mean^2)), plus exact min/max;
    - a LogSketch -> median and percentiles within the sketch's ±1%.

    The std formula is the population standard deviation. It can lose
    precision when values are huge and nearly identical; our metrics (times
    in ms, lengths, percentages) are nowhere near that range.
    """

    def __init__(self, relative_accuracy: float = 0.01) -> None:
        self.count = 0
        self.sum = 0.0
        self.sum_sq = 0.0
        self.min: Optional[float] = None
        self.max: Optional[float] = None
        self.sketch = LogSketch(relative_accuracy)

    def add(self, value: float) -> Result[None, AxoError]:
        added = self.sketch.add(value)
        if added.is_err:
            return added
        self.count += 1
        self.sum += value
        self.sum_sq += value * value
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)
        return Ok(None)

    def merge(self, other: "StatSummary") -> Result[None, AxoError]:
        merged = self.sketch.merge(other.sketch)
        if merged.is_err:
            return merged
        self.count += other.count
        self.sum += other.sum
        self.sum_sq += other.sum_sq
        if other.min is not None:
            self.min = other.min if self.min is None else min(self.min, other.min)
        if other.max is not None:
            self.max = other.max if self.max is None else max(self.max, other.max)
        return Ok(None)

    def mean(self) -> Optional[float]:
        return self.sum / self.count if self.count else None

    def std(self) -> Optional[float]:
        if not self.count:
            return None
        mean = self.sum / self.count
        return math.sqrt(max(0.0, self.sum_sq / self.count - mean * mean))

    def quantile(self, q: float) -> Optional[float]:
        return self.sketch.quantile(q)

    def snapshot(self) -> Dict[str, Optional[float]]:
        """The ready-to-show numbers: count, mean, std, min, max, median, p90, p95, p99."""
        values: Dict[str, Optional[float]] = {
            "count": self.count,
            "mean": self.mean(),
            "std": self.std(),
            "min": self.min,
            "max": self.max,
        }
        for name, q in SNAPSHOT_QUANTILES.items():
            values[name] = self.quantile(q)
        return values

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "sum": self.sum,
            "sum_sq": self.sum_sq,
            "min": self.min,
            "max": self.max,
            "sketch": self.sketch.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StatSummary":
        sketch = LogSketch.from_dict(data["sketch"])
        summary = cls(relative_accuracy=sketch.relative_accuracy)
        summary.sketch = sketch
        summary.count = int(data["count"])
        summary.sum = float(data["sum"])
        summary.sum_sq = float(data["sum_sq"])
        summary.min = data["min"]
        summary.max = data["max"]
        return summary
