from axo_shared.metrics import StatSummary

from axo_control_plane.application.metric_tiers import MetricTiers, cp_tiers, series_for

T0 = 1_800_000_000.0 - (1_800_000_000.0 % 86400)      # a UTC midnight
DAY = 86400.0


def _summary(*values):
    stats = StatSummary()
    for v in values:
        stats.add(v)
    return stats.to_dict()


def _minute(db, start, *values, ve="ml", metric="job.total_ms"):
    db["metric_minutes"].insert_one({
        "_id": f"{ve}:endpoint:ep-1:{start}", "ve_id": ve, "component_kind": "endpoint", "component_id": "ep-1",
        "bin_start": start, "bin_width_s": 60,
        "summaries": [{"metric": metric, "dims": {"function": "f"}, "stats": _summary(*values)}]})


def _stats(doc, metric="job.total_ms"):
    (s,) = [s for s in doc["summaries"] if s["metric"] == metric]
    return StatSummary.from_dict(s["stats"])


def _tiers(platform, now):
    platform.clock.t = now
    tiers = cp_tiers()
    return MetricTiers(platform.db, tiers, series_for(tiers), now_fn=platform.clock)


def test_minutes_roll_into_15_minutes_and_days_exactly(platform):
    db = platform.db
    for m in range(30):                                   # 30 minutes, values 1..30
        _minute(db, T0 + m * 60, float(m + 1))
    _tiers(platform, T0 + 3600).tick()
    quarters = list(db["metric_bins_900"].find().sort("bin_start", 1))
    assert [q["sources"] for q in quarters] == [15, 15]
    assert _stats(quarters[0]).count == 15 and _stats(quarters[0]).max == 15.0
    tiers = _tiers(platform, T0 + 3600)
    tiers.tick()                                          # the day is rebuilt from the quarters
    (day,) = list(db["metric_bins_86400"].find())
    stats = _stats(day)
    assert (stats.count, stats.min, stats.max, stats.mean()) == (30, 1.0, 30.0, 15.5)
    assert abs(stats.quantile(0.5) - 15.5) <= 0.5


def test_repeats_and_late_minutes_never_count_twice(platform):
    db = platform.db
    _minute(db, T0, 10.0)
    tiers = _tiers(platform, T0 + 600)
    tiers.tick()
    tiers.tick()
    tiers.tick()
    assert _stats(db["metric_bins_86400"].find_one()).count == 1
    _minute(db, T0 + 60, 20.0)                            # arrives late, same quarter
    tiers.tick()
    tiers.tick()
    assert _stats(db["metric_bins_900"].find_one()).count == 2
    assert _stats(db["metric_bins_86400"].find_one()).count == 2


def test_minutes_are_removed_after_7_days_only_once_merged(platform):
    db = platform.db
    _minute(db, T0, 1.0)
    tiers = _tiers(platform, T0 + 8 * DAY)
    db["metric_minutes"].update_one({}, {"$set": {"rolled": False}})
    # Arrived after its keep time: dropped, not merged (its siblings may be gone).
    tiers.tick()
    assert db["metric_bins_900"].count_documents({}) == 0
    _minute(db, T0 + 8 * DAY - 120, 2.0)                  # a fresh minute
    tiers.tick()
    assert db["metric_bins_900"].count_documents({}) == 1
    assert db["metric_minutes"].count_documents({}) == 1  # the old one is gone, the fresh one kept


def test_storage_usage_rows_are_summed_and_kept_until_billed(platform):
    db = platform.db
    for m in range(3):
        db["storage_usage"].insert_one({"_id": f"u{m}", "bin_start": T0 + m * 60, "ts": T0 + m * 60,
                                        "bucket_id": "bkt-1", "payer_key": "user:ana", "ve_id": "ml",
                                        "requests": 10, "bytes_read": 100, "bytes_written": 1, "bill_id": None})
    tiers = _tiers(platform, T0 + 600)
    tiers.tick()
    tiers.tick()
    (quarter,) = list(db["storage_usage_bins_900"].find())
    assert (quarter["requests"], quarter["bytes_read"], quarter["bytes_written"]) == (30, 300, 3)
    assert db["storage_usage_bins_86400"].find_one()["requests"] == 30
    _tiers(platform, T0 + 9 * DAY).tick()
    assert db["storage_usage"].count_documents({}) == 3   # not on a closed bill yet: kept
    db["storage_usage"].update_many({}, {"$set": {"bill_id": "storage:user:ana:x"}})
    _tiers(platform, T0 + 9 * DAY).tick()
    assert db["storage_usage"].count_documents({}) == 0
    assert db["storage_usage_bins_900"].count_documents({}) == 1     # 90 days


def test_custom_tier_spec():
    tiers = cp_tiers("1m:1d,1h:30d,1d:forever")
    names = [s.collections for s in series_for(tiers)]
    assert names[0] == ["metric_minutes", "metric_bins_3600", "metric_bins_86400"]
