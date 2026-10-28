from datetime import datetime, timezone

import pytest
from axo_shared.metrics import StatSummary

from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.metric_tiers import MetricTiers, cp_tiers, series_for
from axo_control_plane.application.statistics import Statistics
from axo_control_plane.domain.errors import InvalidError

DAY = 86400.0
MIN = 60.0


def _setup(platform, plan_id="standard"):
    platform.clock.t = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 10 * 3600     # 10:00 UTC
    alice = platform.user("alice")
    ve_id = platform.buy(alice, plan_id=plan_id)["ve"]["ve_id"]
    tiers = cp_tiers()
    stats = Statistics(platform.db, platform.catalog, tiers, max_points=100, now_fn=platform.clock)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog,
                       now_fn=platform.clock)
    return ve_id, stats, intake, MetricTiers(platform.db, tiers, series_for(tiers), now_fn=platform.clock)


def _summary(*values):
    s = StatSummary()
    for v in values:
        s.add(v)
    return s.to_dict()


def _minute(intake, ve_id, start, summaries, part=None):
    intake.handle("MetricSummaryReported", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "component_kind": "vem", "component_id": ve_id,
        "bin_start": start, "bin_width_s": 60, "part": part,
        "summaries": [{"metric": metric, "dims": dims, "stats": _summary(*values)}
                      for metric, dims, values in summaries]})


def _attempt(intake, ve_id, job, finished, attempt=1, outcome="completed", function="f", by=("user", "u1"),
             waits=None, execution_ms=1000.0, **extra):
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": job, "attempt": attempt, "trace_id": f"t-{job}",
        "outcome": outcome, "owner_type": "user", "owner_id": "u", "started_by_type": by[0],
        "started_by_id": by[1], "function_id": function, "function_version": 1, "endpoint_cores": 1.0,
        "endpoint_ram_mb": 1024, "received_at": finished - 2, "finished_at": finished,
        "execution_ms": execution_ms, "layer_waits_ms": waits or {}, **extra})


def test_a_chart_merges_bins_per_step_and_splits_by_a_dimension(platform):
    ve_id, stats, intake, _ = _setup(platform)
    now = platform.clock()
    for m in range(10):
        _minute(intake, ve_id, now - 600 + m * MIN, [
            ("job.total_ms", {"function": "f", "endpoint": "ep-1"}, [m + 1]),
            ("job.total_ms", {"function": "g", "endpoint": "ep-2"}, [100]),
            ("requests.received", {"layer": "endpoint"}, [1, 1])])
    chart = stats.chart(ve_id, "job.total_ms", now - 600, now, statistics=("count", "max", "avg"),
                        group_by="function", step_s=300)
    assert chart["bin_width_s"] == 60 and chart["step_s"] == 300
    f_points = [p for p in chart["points"] if p["group"] == "f"]
    assert [p["values"] for p in f_points] == [{"count": 5.0, "max": 5.0, "avg": 3.0},
                                               {"count": 5.0, "max": 10.0, "avg": 8.0}]
    only_ep2 = stats.summary(ve_id, "job.total_ms", now - 600, now, dims={"endpoint": "ep-2"})
    (group,) = only_ep2["groups"]
    assert group["values"]["count"] == 10 and group["values"]["median"] == pytest.approx(100, rel=0.01)
    total = stats.summary(ve_id, "requests.received", now - 600, now, statistics=("total",))
    assert total["groups"][0]["values"]["total"] == 20


def test_long_windows_use_coarser_bins_and_the_plan_retention_hides_older_data(platform):
    ve_id, stats, intake, tiers = _setup(platform)              # standard: 90 days kept
    now = platform.clock()
    _minute(intake, ve_id, now - DAY, [("job.total_ms", {}, [1])])
    tiers.tick()                                                  # -> its 15-min and day bins
    # Older values, as the merge leaves them: day 20 in 15-min and day bins
    # (15-min bins are kept 90 days), day 200 only in day bins.
    for day, widths in ((20, (900, 86400)), (200, (86400,))):
        for width in widths:
            start = (now - day * DAY) // width * width
            platform.db[f"metric_bins_{width}"].insert_one({
                "_id": f"{ve_id}:vem:{ve_id}:{int(start)}", "ve_id": ve_id, "component_kind": "vem",
                "component_id": ve_id, "bin_start": start, "bin_width_s": width, "rolled": True,
                "summaries": [{"metric": "job.total_ms", "dims": {}, "stats": _summary(day)}]})
    day = stats.chart(ve_id, "job.total_ms", now - DAY - MIN, now, statistics=("count",))
    assert day["bin_width_s"] == 900                              # 1-min bins would be > 100 points
    assert [p["values"]["count"] for p in day["points"]] == [1.0]
    # A point may merge a few bins before a coarser tier is used: 4 days =
    # 384 15-min bins, merged 4 to a point (not 4 daily points).
    days4 = stats.chart(ve_id, "job.total_ms", now - 4 * DAY, now, statistics=("count",))
    assert days4["bin_width_s"] == 900 and days4["step_s"] == 3600
    assert [p["values"]["count"] for p in days4["points"]] == [1.0]
    month = stats.chart(ve_id, "job.total_ms", now - 30 * DAY, now, statistics=("count",))
    assert month["bin_width_s"] == 86400                          # 15-min bins too: day bins
    assert [p["values"]["count"] for p in month["points"]] == [1.0, 1.0]
    year = stats.summary(ve_id, "job.total_ms", now - 365 * DAY, now, statistics=("count", "max"))
    assert year["start"] == now - 90 * DAY                        # day 200 is past the plan's 90 days
    assert year["groups"][0]["values"] == {"count": 2.0, "max": 20.0}


def test_top_lists_come_from_attempt_records_and_hide_money_unless_asked(platform):
    ve_id, stats, intake, _ = _setup(platform)
    now = platform.clock()
    for n in range(3):
        _attempt(intake, ve_id, f"a{n}", now - 100 - n, function="f", by=("user", "ana"))
    _attempt(intake, ve_id, "b", now - 50, function="g", by=("api_key", "key-1"), outcome="failed_user",
             execution_ms=9000.0, bytes_read={"sales": 500}, bytes_written={"reports": 40})
    top = stats.top(ve_id, "function", now - DAY, now)
    assert [(r["function_id"], r["attempts"]) for r in top["rows"]] == [("f", 3), ("g", 1)]
    assert "cost_ucents" not in top["rows"][0]
    by_time = stats.top(ve_id, "function", now - DAY, now, sort="execution_ms")
    assert by_time["rows"][0]["function_id"] == "g" and by_time["rows"][0]["failed"] == 1
    with_money = stats.top(ve_id, "function", now - DAY, now, sort="cost", money=True)
    assert all(r["cost_ucents"] > 0 for r in with_money["rows"])
    assert stats.top(ve_id, "user", now - DAY, now)["rows"] == [
        {"user_id": "ana", "attempts": 3, "billable_attempts": 3, "failed": 0, "execution_ms": 3000.0,
         "avg_execution_ms": 1000.0}]
    assert [r["api_key_id"] for r in stats.top(ve_id, "api_key", now - DAY, now)["rows"]] == ["key-1"]
    assert stats.top(ve_id, "bucket", now - DAY, now)["rows"] == [
        {"bucket": "sales", "bytes_read": 500, "bytes_written": 0, "attempts": 1},
        {"bucket": "reports", "bytes_read": 0, "bytes_written": 40, "attempts": 1}]
    with pytest.raises(InvalidError):
        stats.top(ve_id, "function", now - 91 * DAY, now)        # records: 90 days at most
    with pytest.raises(InvalidError):
        stats.top(ve_id, "function", now - DAY, now, sort="cost")  # no money asked


def test_heatmap_counts_attempts_per_weekday_and_hour_in_the_viewers_zone(platform):
    ve_id, stats, intake, _ = _setup(platform)
    now = platform.clock()                                        # a Friday, 10:00 UTC
    _attempt(intake, ve_id, "a", now - 30 * MIN)                  # 09:30 UTC
    _attempt(intake, ve_id, "b", now - 20 * MIN)
    heat = stats.heatmap(ve_id, now - DAY, now)
    friday = heat["weekdays"].index("Fri")
    assert heat["attempts"][friday][9] == 2 and sum(map(sum, heat["attempts"])) == 2
    mexico = stats.heatmap(ve_id, now - DAY, now, time_zone="America/Mexico_City")
    assert mexico["attempts"][friday][3] == 2 and mexico["time_zone"] == "America/Mexico_City"
    with pytest.raises(InvalidError):
        stats.heatmap(ve_id, now - DAY, now, time_zone="Mars/Olympus")


def test_the_heatmap_follows_daylight_saving_inside_the_window(platform):
    ve_id, stats, intake, _ = _setup(platform)
    # Madrid moves from UTC+1 to UTC+2 on Sunday 2027-03-28 at 01:00 UTC.
    before = datetime(2027, 3, 27, 10, 0, tzinfo=timezone.utc).timestamp()    # Sat 11:00 in Madrid
    after = datetime(2027, 3, 29, 10, 0, tzinfo=timezone.utc).timestamp()     # Mon 12:00 in Madrid
    platform.clock.t = after + 3600
    _attempt(intake, ve_id, "a", before)
    _attempt(intake, ve_id, "b", after)
    heat = stats.heatmap(ve_id, before - DAY, after + 60, time_zone="Europe/Madrid")
    days = heat["weekdays"]
    assert heat["attempts"][days.index("Sat")][11] == 1 and heat["attempts"][days.index("Mon")][12] == 1


def test_the_job_time_bar_lists_each_layer_wait_then_the_execution(platform):
    ve_id, stats, intake, _ = _setup(platform)
    now = platform.clock()
    _attempt(intake, ve_id, "j", now - 10, attempt=1, outcome="failed_platform", execution_ms=0.0,
             waits={"endpoint": 120.0, "vem": 3.0, "control_plane": 5.0})
    _attempt(intake, ve_id, "j", now, attempt=2, waits={"runner": 40.0, "endpoint": 10.0}, execution_ms=900.0,
             bytes_read={"sales": 10}, cache_hits=2)
    bar = stats.job_times(ve_id, "j")
    first, second = bar["attempts"]
    assert [p["part"] for p in first["parts"]] == ["control_plane", "vem", "endpoint", "execution"]
    assert first["total_ms"] == 128.0 and first["outcome"] == "failed_platform"
    assert [(p["part"], p["ms"]) for p in second["parts"]] == [("endpoint", 10.0), ("runner", 40.0),
                                                                ("execution", 900.0)]
    assert second["bytes_read"] == {"sales": 10} and second["cache_hits"] == 2 and "cost_ucents" not in second
    assert stats.job_times(ve_id, "j", money=True)["attempts"][1]["cost_ucents"] > 0


def test_a_minute_sent_in_two_parts_counts_both_and_a_resend_once(platform):
    ve_id, stats, intake, tiers = _setup(platform)
    minute = platform.clock() - 120
    _minute(intake, ve_id, minute, [("job.total_ms", {"outcome": "completed"}, [10, 20])], part="before-stop")
    _minute(intake, ve_id, minute, [("job.total_ms", {"outcome": "completed"}, [10, 20])], part="before-stop")
    _minute(intake, ve_id, minute, [("job.total_ms", {"outcome": "completed"}, [30])], part="after-start")
    answer = stats.summary(ve_id, "job.total_ms", minute, minute + 60, statistics=("count", "max"))
    assert answer["groups"][0]["values"] == {"count": 3.0, "max": 30.0}
    platform.clock.advance(20 * MIN)
    tiers.tick()                                                   # rolled up into 15-min bins: still 3
    platform.clock.advance(8 * DAY)                                # minutes gone, the 15-min tier answers
    answer = stats.summary(ve_id, "job.total_ms", minute - 900, minute + 900, statistics=("count",))
    assert answer["groups"][0]["values"]["count"] == 3.0
