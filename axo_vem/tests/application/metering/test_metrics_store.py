from axo_shared.metrics import StatSummary
from axo_shared.metrics import names as m

from axo_vem.application.metering.metrics_store import MetricsStore


def bin_of(component, bin_start, metric, values, dims=None, width=10):
    stats = StatSummary()
    for v in values:
        stats.add(v)
    return {"component_id": component, "bin_start": bin_start, "bin_width_s": width,
            "summaries": [{"metric": metric, "dims": dims or {}, "stats": stats.to_dict()}]}


def test_values_merge_exactly_across_endpoints_and_bins():
    now = [1000.0]
    store = MetricsStore(now_fn=lambda: now[0])
    store.ingest(bin_of("ep-1", 980, m.JOB_TOTAL_MS, [100, 200]))
    store.ingest(bin_of("ep-2", 990, m.JOB_TOTAL_MS, [300]))
    assert store.value(m.JOB_TOTAL_MS, "avg", 60) == 200
    assert store.value(m.JOB_TOTAL_MS, "max", 60) == 300
    assert store.value(m.JOB_TOTAL_MS, "count", 60) == 3
    assert store.value(m.JOB_TOTAL_MS, "avg", 15) == 300           # only the newest bin is in the window
    assert store.value(m.QUEUE_LENGTH, "avg", 60) is None           # no data


def test_dims_filter_and_old_bins_are_dropped():
    now = [5000.0]
    store = MetricsStore(keep_seconds=600, now_fn=lambda: now[0])
    store.ingest(bin_of("ep-1", 4990, m.JOB_WAIT_MS, [10], dims={m.DIM_FUNCTION: "f1"}))
    store.ingest(bin_of("ep-1", 4990, m.JOB_WAIT_MS, [99], dims={m.DIM_FUNCTION: "f2"}))
    assert store.value(m.JOB_WAIT_MS, "avg", 60, {m.DIM_FUNCTION: "f1"}) == 10
    now[0] = 6000.0
    store.ingest(bin_of("ep-1", 5990, m.JOB_WAIT_MS, [1]))
    assert store.value(m.JOB_WAIT_MS, "count", 10_000) == 1


def test_minute_summaries_roll_ten_second_bins_up_per_endpoint_without_users():
    store = MetricsStore(now_fn=lambda: 200.0)
    for i, start in enumerate(range(60, 120, 10)):
        store.ingest(bin_of(f"ep-{i % 2}", float(start), m.REQUESTS_RECEIVED, [1, 1],
                            dims={m.DIM_PRINCIPAL: f"user-{i}"}))
    minutes = store.minute_summaries(before=120.0)
    assert [(mi.bin_start, mi.width_s, mi.stats.sum) for mi in minutes] == [(60.0, 60, 6), (60.0, 60, 6)]
    assert sorted(dict(mi.dims)[m.DIM_ENDPOINT] for mi in minutes) == ["ep-0", "ep-1"]
    assert all(m.DIM_PRINCIPAL not in dict(mi.dims) for mi in minutes)
