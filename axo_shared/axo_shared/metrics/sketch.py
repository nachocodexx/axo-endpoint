from __future__ import annotations

import math
from typing import Any, Dict, Optional

from option import Err, Ok, Result

from axo_shared.errors import IncompatibleSketchError, InvalidMetricValueError

# Values at or below this are counted in the zero bucket -- exact zeros are
# common (an empty queue, a cache hit with no wait) and log(0) is undefined.
_MIN_INDEXABLE = 1e-9


class LogSketch:
    """Quantile sketch with a relative-error guarantee -- our own small
    implementation of DDSketch (Masson, Rim & Lee, "DDSketch: A fast and
    fully-mergeable quantile sketch with relative-error guarantees", VLDB 2019).

    Bins grow geometrically: bin ``i`` holds values in (γ^(i-1), γ^i] with
    γ = (1+α)/(1-α), so any quantile is answered within ±α (relative) of the
    true value, whatever its magnitude. Only non-empty bins are stored (a
    sparse dict), two sketches with the same α merge exactly by adding
    counts, and to_dict() is plain JSON/Mongo-safe data.

    Accepts finite values >= 0 only -- every metric we sketch (times,
    lengths, percentages) is non-negative.
    """

    def __init__(self, relative_accuracy: float = 0.01) -> None:
        if not 0.0 < relative_accuracy < 1.0:
            raise ValueError("relative_accuracy must be in (0, 1)")
        self.relative_accuracy = relative_accuracy
        self._gamma = (1.0 + relative_accuracy) / (1.0 - relative_accuracy)
        self._log_gamma = math.log(self._gamma)
        self.bins: Dict[int, int] = {}
        self.zero_count = 0
        self.count = 0

    def add(self, value: float, count: int = 1) -> Result[None, InvalidMetricValueError]:
        if not math.isfinite(value) or value < 0:
            return Err(InvalidMetricValueError(
                "sketch values must be finite and >= 0", context={"value": value},
            ))
        if value <= _MIN_INDEXABLE:
            self.zero_count += count
        else:
            index = math.ceil(math.log(value) / self._log_gamma)
            self.bins[index] = self.bins.get(index, 0) + count
        self.count += count
        return Ok(None)

    def _value_of(self, index: int) -> float:
        # Midpoint (in relative terms) of (γ^(i-1), γ^i]: within α of both edges.
        return 2.0 * self._gamma ** index / (self._gamma + 1.0)

    def quantile(self, q: float) -> Optional[float]:
        """Estimated q-quantile (0 <= q <= 1), or None if empty. Uses the
        "lower" rank convention: the item at sorted position floor(q·(n-1))."""
        if self.count == 0:
            return None
        if not 0.0 <= q <= 1.0:
            raise ValueError("q must be in [0, 1]")
        rank = q * (self.count - 1)
        cumulative = self.zero_count
        if cumulative > rank:
            return 0.0
        for index in sorted(self.bins):
            cumulative += self.bins[index]
            if cumulative > rank:
                return self._value_of(index)
        return self._value_of(max(self.bins))  # only reachable through float rounding

    def merge(self, other: "LogSketch") -> Result[None, IncompatibleSketchError]:
        """Add other's counts into this sketch. Exact: the result equals a
        sketch built from both data sets together."""
        if not math.isclose(self.relative_accuracy, other.relative_accuracy):
            return Err(IncompatibleSketchError(
                "cannot merge sketches with different relative accuracies",
                context={"left": self.relative_accuracy, "right": other.relative_accuracy},
            ))
        for index, count in other.bins.items():
            self.bins[index] = self.bins.get(index, 0) + count
        self.zero_count += other.zero_count
        self.count += other.count
        return Ok(None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relative_accuracy": self.relative_accuracy,
            "zero_count": self.zero_count,
            "bins": {str(index): count for index, count in self.bins.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LogSketch":
        sketch = cls(relative_accuracy=data["relative_accuracy"])
        sketch.zero_count = int(data["zero_count"])
        sketch.bins = {int(index): int(count) for index, count in data["bins"].items()}
        sketch.count = sketch.zero_count + sum(sketch.bins.values())
        return sketch
