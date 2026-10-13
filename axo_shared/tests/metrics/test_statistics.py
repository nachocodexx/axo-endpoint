import math
import random
import statistics
import threading

import pytest

from axo_shared.metrics import (
    LogSketch,
    MetricAggregator,
    MetricRecord,
    StatSummary,
    Tier,
    bin_start,
    freeze_dims,
    parse_duration,
    parse_tiers,
    rollup,
)

QUANTILES = (0.0, 0.5, 0.9, 0.95, 0.99, 1.0)


def _exact(values, q):
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def _datasets():
    rng = random.Random(42)
    return {
        "uniform": [rng.uniform(1, 1000) for _ in range(20_000)],
        "exponential": [rng.expovariate(1 / 120) for _ in range(20_000)],
        "long_tail": [rng.paretovariate(1.2) * 5 for _ in range(20_000)],
        "wide_range": [10 ** rng.uniform(-3, 6) for _ in range(20_000)],
    }


@pytest.mark.parametrize("name", list(_datasets()))
def test_sketch_quantiles_within_one_percent(name):
    values = _datasets()[name]
    sketch = LogSketch(0.01)
    for v in values:
        sketch.add(v).unwrap()
    for q in QUANTILES:
        exact = _exact(values, q)
        assert abs(sketch.quantile(q) - exact) <= 0.01 * exact + 1e-12, (name, q)


def test_sketch_handles_zeros_and_rejects_bad_values():
    sketch = LogSketch()
    for v in (0, 0, 0, 5.0):
        sketch.add(v).unwrap()
    assert sketch.quantile(0.5) == 0.0
    assert sketch.quantile(1.0) == pytest.approx(5.0, rel=0.01)
    assert sketch.add(-1).is_err
    assert sketch.add(math.inf).is_err
    assert sketch.add(math.nan).is_err
    assert LogSketch().quantile(0.5) is None


def test_sketch_merge_equals_sketch_of_all_data_and_round_trips():
    data = _datasets()["exponential"]
    left, right, whole = LogSketch(), LogSketch(), LogSketch()
    for v in data[:7000]:
        left.add(v)
    for v in data[7000:]:
        right.add(v)
    for v in data:
        whole.add(v)
    left.merge(right).unwrap()
    assert left.bins == whole.bins and left.count == whole.count

    restored = LogSketch.from_dict(left.to_dict())
    assert restored.bins == left.bins and restored.count == left.count
    assert all(isinstance(k, str) for k in left.to_dict()["bins"])  # JSON / Mongo-safe keys


def test_sketch_merge_rejects_different_accuracy():
    assert LogSketch(0.01).merge(LogSketch(0.02)).is_err


def test_summary_mean_and_std_are_exact_after_merge():
    # The worked example from the design: two minutes merged into one window.
    minute_1, minute_2 = StatSummary(), StatSummary()
    for v in (10, 20, 30):
        minute_1.add(v)
    for v in (40, 50):
        minute_2.add(v)
    minute_1.merge(minute_2).unwrap()
    assert minute_1.mean() == 30
    assert minute_1.std() == pytest.approx(math.sqrt(200))
    assert (minute_1.min, minute_1.max, minute_1.count) == (10, 50, 5)


def test_summary_matches_statistics_module_on_random_data():
    values = _datasets()["uniform"]
    parts = [StatSummary() for _ in range(4)]
    for i, v in enumerate(values):
        parts[i % 4].add(v)
    total = parts[0]
    for part in parts[1:]:
        total.merge(part)
    assert total.mean() == pytest.approx(statistics.fmean(values), rel=1e-9)
    assert total.std() == pytest.approx(statistics.pstdev(values), rel=1e-6)

    snapshot = StatSummary.from_dict(total.to_dict()).snapshot()
    assert snapshot["count"] == len(values)
    assert snapshot["median"] == pytest.approx(_exact(values, 0.5), rel=0.01)
    assert set(snapshot) == {"count", "mean", "std", "min", "max", "median", "p90", "p95", "p99"}


def test_empty_summary_snapshot_is_all_none_but_count():
    snapshot = StatSummary().snapshot()
    assert snapshot.pop("count") == 0
    assert set(snapshot.values()) == {None}


def test_bin_start_is_clock_aligned():
    assert bin_start(1_000_000_007.5, 10) == 1_000_000_000
    assert bin_start(905.0, 900) == 900
    assert bin_start(899.999, 900) == 0


def test_rollup_of_fifteen_minutes_equals_one_quarter_hour_bin():
    rng = random.Random(7)
    minute_records, quarter = [], StatSummary()
    for minute in range(15):
        stats = StatSummary()
        for _ in range(100):
            v = rng.expovariate(1 / 50)
            stats.add(v)
            quarter.add(v)
        minute_records.append(MetricRecord("job.exec_ms", freeze_dims({"ve": "a"}), 900 + minute * 60, 60, stats))

    (rolled,) = rollup(minute_records, 900).unwrap()
    assert (rolled.bin_start, rolled.width_s) == (900, 900)
    assert rolled.stats.count == quarter.count
    assert rolled.stats.mean() == pytest.approx(quarter.mean())
    assert rolled.stats.std() == pytest.approx(quarter.std())
    assert rolled.stats.sketch.bins == quarter.sketch.bins

    assert MetricRecord.from_dict(rolled.to_dict()).stats.count == quarter.count
    assert rollup(minute_records, 90).is_err  # 60 s bins don't fit 90 s


def test_parse_tiers_default_and_errors():
    tiers = parse_tiers().unwrap()
    assert tiers == [Tier(10, 3600), Tier(60, 7 * 86400), Tier(900, 90 * 86400), Tier(86400, None)]
    assert parse_duration("15m") == 900 and parse_duration("0s") is None
    assert parse_tiers("10s:1h,25s:1d").is_err  # 25 is not a multiple of 10
    assert parse_tiers("10s").is_err
    assert parse_tiers("10x:1h").is_err
    assert parse_tiers("").is_err


def test_aggregator_drains_only_closed_bins():
    now = [1000.0]
    aggregator = MetricAggregator(width_s=10, clock=lambda: now[0])
    aggregator.observe("queue.length", 3, {"endpoint": "ep-0"})
    aggregator.observe("queue.length", 5, {"endpoint": "ep-0"})
    now[0] = 1012.0
    aggregator.observe("queue.length", 9, {"endpoint": "ep-0"})

    (closed,) = aggregator.drain_closed()
    assert (closed.bin_start, closed.stats.count, closed.stats.max) == (1000, 2, 5)
    assert aggregator.drain_closed() == []
    (still_open,) = aggregator.drain_all()
    assert still_open.bin_start == 1010
    assert aggregator.observe("queue.length", -1).is_err


def test_aggregator_is_thread_safe():
    aggregator = MetricAggregator(width_s=10, clock=lambda: 1000.0)

    def worker():
        for i in range(2000):
            aggregator.observe("job.exec_ms", i % 50 + 1, {"function": "f"})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    (record,) = aggregator.drain_all()
    assert record.stats.count == 16_000
    assert record.stats.sketch.count == 16_000
