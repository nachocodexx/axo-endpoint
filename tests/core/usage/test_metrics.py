from datetime import datetime, timezone

from axo_shared.events import models as event_models
from axo_shared.metrics import MetricAggregator, StatSummary
from axo_shared.metrics import names as m

from axo_endpoint.core.usage import EndpointMetrics


def _record(**overrides):
    base = dict(
        job_id="j", attempt=1, trace_id="t", outcome="completed", owner_type="user", owner_id="u",
        started_by_type="user", started_by_id="u-1", function_id="add", function_version=2,
        endpoint_cores=1.0, endpoint_ram_mb=1024,
        received_at=datetime.fromtimestamp(100.0, tz=timezone.utc),
        started_at=datetime.fromtimestamp(100.5, tz=timezone.utc),
        finished_at=datetime.fromtimestamp(102.5, tz=timezone.utc),
        layer_waits_ms={"vem": 3.0, "endpoint": 500.0}, execution_ms=2000.0,
    )
    return event_models.JobAttemptRecorded(**{**base, **overrides})


def _metrics(published, clock, dispatcher=None, outbox=None):
    return EndpointMetrics(
        aggregator=MetricAggregator(width_s=10, clock=lambda: clock[0]),
        endpoint_id="ep-1", virtual_environment_id="ve-a", bin_width_s=10,
        publish=lambda event_type, data: published.append((event_type, data)),
        dispatcher_metrics_fn=dispatcher, outbox_stats_fn=outbox,
    )


def _by_metric(event, metric, **dims):
    for summary in event.summaries:
        if summary.metric == metric and all(summary.dims.get(k) == v for k, v in dims.items()):
            return StatSummary.from_dict(summary.stats)
    raise AssertionError(f"{metric} {dims} not found")


def test_job_and_request_metrics_are_published_per_finished_bin():
    published, clock = [], [1000.0]
    metrics = _metrics(published, clock)
    metrics.observe_job(_record())
    metrics.observe_job(_record(attempt=2, outcome="failed_user"))
    metrics.on_request()
    metrics.on_request()

    assert metrics.flush(now=1005.0) == []   # bin 1000-1010 still open
    (event,) = metrics.flush(now=1010.0)

    assert published[0][0] == event_models.METRIC_SUMMARY_REPORTED
    assert (event.component_kind, event.component_id, event.virtual_environment_id) == ("endpoint", "ep-1", "ve-a")
    assert (event.bin_start, event.bin_width_s) == (1000.0, 10)
    assert _by_metric(event, m.JOB_EXEC_MS, outcome="completed").mean() == 2000.0
    assert _by_metric(event, m.JOB_TOTAL_MS, outcome="completed").mean() == 2500.0
    assert _by_metric(event, m.JOB_WAIT_MS, layer="endpoint", outcome="completed").mean() == 500.0
    assert _by_metric(event, m.REQUESTS_RECEIVED).sum == 2
    assert _by_metric(event, m.REQUESTS_RETRIED).sum == 1
    rebuilt = event_models.MetricSummaryReported(**published[0][1])
    assert rebuilt == event


def test_queue_samples_and_rejected_delta():
    published, clock = [], [1000.0]
    dispatcher_stats = {"queue_depth": 5, "queue_capacity": 20, "rejected": 3}
    metrics = _metrics(published, clock, dispatcher=lambda: dict(dispatcher_stats),
                       outbox=lambda: {"pending": 7})
    metrics.sample()
    dispatcher_stats.update(queue_depth=15, rejected=4)
    metrics.sample()
    (event,) = metrics.flush(now=1010.0)

    length = _by_metric(event, m.QUEUE_LENGTH, queue="dispatcher")
    assert (length.count, length.min, length.max) == (2, 5, 15)
    assert _by_metric(event, m.QUEUE_FULLNESS_PCT, queue="dispatcher").max == 75.0
    assert _by_metric(event, m.REQUESTS_REJECTED).sum == 4   # 3 then +1
    assert _by_metric(event, m.QUEUE_LENGTH, queue="outbox").max == 7


def test_flush_with_infinite_now_drains_open_bins():
    published, clock = [], [1000.0]
    metrics = _metrics(published, clock)
    metrics.on_request()
    assert len(metrics.flush(now=float("inf"))) == 1
