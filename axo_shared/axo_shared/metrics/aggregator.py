from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from option import Result

from axo_shared.errors import AxoError
from axo_shared.metrics.summary import StatSummary
from axo_shared.metrics.timebins import Dims, MetricRecord, bin_start, freeze_dims


class MetricAggregator:
    """Accumulates observations into clock-aligned bins of ``width_s`` --
    the finest (live) tier. Producers call observe() from any thread; a
    periodic job calls drain_closed() to take every finished bin out, e.g. to
    publish it as a MetricSummaryReported event.

    Memory is bounded by (metrics x dims combinations x open bins); draining
    regularly keeps open bins to the current one or two.
    """

    def __init__(
        self,
        width_s: int = 10,
        relative_accuracy: float = 0.01,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._width_s = width_s
        self._relative_accuracy = relative_accuracy
        self._clock = clock
        self._lock = threading.Lock()
        self._bins: Dict[Tuple[str, Dims, float], StatSummary] = {}

    def observe(
        self,
        metric: str,
        value: float,
        dims: Optional[Dict[str, str]] = None,
        ts: Optional[float] = None,
    ) -> Result[None, AxoError]:
        key = (metric, freeze_dims(dims), bin_start(self._clock() if ts is None else ts, self._width_s))
        with self._lock:
            summary = self._bins.get(key)
            if summary is None:
                summary = StatSummary(self._relative_accuracy)
                result = summary.add(value)
                if result.is_ok:
                    self._bins[key] = summary
                return result
            return summary.add(value)

    def drain_closed(self, now: Optional[float] = None) -> List[MetricRecord]:
        """Remove and return every bin that has ended (bin_start + width <= now)."""
        now = self._clock() if now is None else now
        with self._lock:
            closed = [key for key in self._bins if key[2] + self._width_s <= now]
            drained = [(key, self._bins.pop(key)) for key in closed]
        return self._records(drained)

    def drain_all(self) -> List[MetricRecord]:
        """Remove and return every bin, finished or not (for shutdown)."""
        with self._lock:
            drained = list(self._bins.items())
            self._bins.clear()
        return self._records(drained)

    def _records(self, drained) -> List[MetricRecord]:
        records = [
            MetricRecord(metric=metric, dims=dims, bin_start=start, width_s=self._width_s, stats=stats)
            for (metric, dims, start), stats in drained
        ]
        records.sort(key=lambda r: (r.bin_start, r.metric, r.dims))
        return records

