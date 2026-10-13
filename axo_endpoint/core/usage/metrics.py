from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from axo_shared.events import models as event_models
from axo_shared.metrics import MetricAggregator, MetricRecord
from axo_shared.metrics import names as m


class EndpointMetrics:
    """This endpoint's side of metering statistics: what it observes goes into
    a MetricAggregator (10 s live bins by default), and every finished bin is
    published as one MetricSummaryReported through the reliable publisher.

    Sources:
    - finished job attempts (from UsageRecorder.on_record): total, per-layer
      wait and execution times, by function / version / outcome / principal;
    - every inbound request (RouterServer's on_request_fn);
    - a periodic sample of the dispatcher queue (length, fullness, rejected)
      and of the outbox backlog (events not yet acked by the VEM).

    Counter-style metrics (requests.*) are observed as counts per event or
    per sample, so their per-bin total is StatSummary.sum.
    """

    def __init__(
        self,
        aggregator: MetricAggregator,
        endpoint_id: str,
        virtual_environment_id: Optional[str],
        bin_width_s: int,
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        dispatcher_metrics_fn: Optional[Callable[[], Dict[str, int]]] = None,
        outbox_stats_fn: Optional[Callable[[], Dict[str, int]]] = None,
    ) -> None:
        self._aggregator = aggregator
        self._endpoint_id = endpoint_id
        self._virtual_environment_id = virtual_environment_id
        self._bin_width_s = bin_width_s
        self._publish = publish
        self._dispatcher_metrics_fn = dispatcher_metrics_fn
        self._outbox_stats_fn = outbox_stats_fn
        self._last_rejected = 0
        self._lock = threading.Lock()

    # ── sources ──────────────────────────────────────────────────────────────
    def observe_job(self, record: event_models.JobAttemptRecorded) -> None:
        dims = {
            m.DIM_FUNCTION: record.function_id,
            m.DIM_FUNCTION_VERSION: str(record.function_version),
            m.DIM_OUTCOME: record.outcome,
            m.DIM_PRINCIPAL: record.started_by_id,
        }
        if record.finished_at is not None:
            total_ms = (record.finished_at - record.received_at).total_seconds() * 1000
            self._aggregator.observe(m.JOB_TOTAL_MS, max(0.0, total_ms), dims)
        if record.started_at is not None:
            self._aggregator.observe(m.JOB_EXEC_MS, record.execution_ms, dims)
        for layer, wait_ms in record.layer_waits_ms.items():
            self._aggregator.observe(m.JOB_WAIT_MS, max(0.0, wait_ms), {**dims, m.DIM_LAYER: layer})
        if record.attempt > 1:
            self._aggregator.observe(m.REQUESTS_RETRIED, 1, {m.DIM_FUNCTION: record.function_id})
        if record.outcome == "timed_out_in_queue":
            self._aggregator.observe(m.REQUESTS_TIMED_OUT, 1, {m.DIM_FUNCTION: record.function_id})

    def on_request(self) -> None:
        self._aggregator.observe(m.REQUESTS_RECEIVED, 1, {m.DIM_LAYER: m.LAYER_ENDPOINT})

    def sample(self) -> None:
        """Called on every heartbeat tick: point-in-time queue readings."""
        if self._dispatcher_metrics_fn is not None:
            stats = self._dispatcher_metrics_fn()
            queue_dims = {m.DIM_LAYER: m.LAYER_ENDPOINT, m.DIM_QUEUE: "dispatcher"}
            depth = stats.get("queue_depth", 0)
            self._aggregator.observe(m.QUEUE_LENGTH, depth, queue_dims)
            capacity = stats.get("queue_capacity", 0)
            if capacity > 0:
                self._aggregator.observe(m.QUEUE_FULLNESS_PCT, 100.0 * depth / capacity, queue_dims)
            rejected = stats.get("rejected", 0)
            with self._lock:
                delta, self._last_rejected = rejected - self._last_rejected, rejected
            if delta > 0:
                self._aggregator.observe(m.REQUESTS_REJECTED, delta, queue_dims)
        if self._outbox_stats_fn is not None:
            pending = self._outbox_stats_fn().get("pending", 0)
            if pending >= 0:
                self._aggregator.observe(
                    m.QUEUE_LENGTH, pending, {m.DIM_LAYER: m.LAYER_ENDPOINT, m.DIM_QUEUE: "outbox"},
                )

    # ── publishing ───────────────────────────────────────────────────────────
    def flush(self, now: Optional[float] = None) -> List[event_models.MetricSummaryReported]:
        """Publish every finished bin (one event per bin) and return them."""
        by_bin: Dict[Tuple[float, int], List[MetricRecord]] = defaultdict(list)
        for record in self._aggregator.drain_closed(now):
            by_bin[(record.bin_start, record.width_s)].append(record)
        events = []
        for (bin_start, width_s), records in sorted(by_bin.items()):
            event = event_models.MetricSummaryReported(
                endpoint_id=self._endpoint_id,
                virtual_environment_id=self._virtual_environment_id,
                component_kind=m.LAYER_ENDPOINT,
                component_id=self._endpoint_id,
                bin_start=bin_start,
                bin_width_s=width_s,
                summaries=[
                    event_models.MetricSummary(metric=r.metric, dims=dict(r.dims), stats=r.stats.to_dict())
                    for r in records
                ],
            )
            if self._publish is not None:
                self._publish(event_models.METRIC_SUMMARY_REPORTED, event.model_dump(mode="json"))
            events.append(event)
        return events

    def run_flush_loop(self, stop_event: threading.Event) -> None:
        """Background loop: flush finished bins once per bin width."""
        while not stop_event.wait(self._bin_width_s):
            self.flush()
