import dataclasses
from datetime import datetime, timezone

import pytest

from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.billing_views import BillingViews
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.metric_tiers import cp_tiers
from axo_control_plane.application.statistics import Statistics
from axo_control_plane.application.storage_metering import GIB, StorageMetering
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ForbiddenError, NotFoundError

SOURCE = "storage:axo-storage"


def _at(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _views(platform):
    bills = VeBills(platform.db, platform.history, close_after_s=3600, now_fn=platform.clock)
    storage = StorageMetering(platform.db, platform.catalog, platform.history, close_after_s=3600,
                              now_fn=platform.clock)
    stats = Statistics(platform.db, platform.catalog, cp_tiers(), now_fn=platform.clock)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog, storage=storage,
                       now_fn=platform.clock)
    views = BillingViews(platform.db, platform.authz, platform.teams, platform.ves, bills, storage, stats,
                         now_fn=platform.clock)
    views.storage = storage                               # for tests that close storage bills
    return views, intake, bills


def _run(intake, ve_id, job, finished, function="f"):
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": job, "attempt": 1, "trace_id": "t", "outcome": "completed",
        "owner_type": "user", "owner_id": "u", "started_by_type": "user", "started_by_id": "u",
        "function_id": function, "function_version": 1, "endpoint_cores": 2.0, "endpoint_ram_mb": 4096,
        "received_at": finished - 3, "finished_at": finished, "execution_ms": 3000.0})


def _share(platform, owner, ve_id, person, role):
    inv = platform.sharing.invite_to_ve(owner, ve_id, person.username, role)
    platform.sharing.answer(person, inv["id"], accept=True)


def test_ve_costs_are_for_the_ve_owner_and_admin_only(platform):
    views, intake, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    olga, adam, dina, vic = (platform.user(n) for n in ("olga", "adam", "dina", "vic"))
    ve_id = platform.buy(olga)["ve"]["ve_id"]
    _share(platform, olga, ve_id, adam, R.ADMIN)
    _share(platform, olga, ve_id, dina, R.DEVELOPER)
    _run(intake, ve_id, "a", _at(2026, 10, 21), function="train")
    _run(intake, ve_id, "b", _at(2026, 10, 21, 13), function="train")
    _run(intake, ve_id, "c", _at(2026, 10, 22), function="score")
    platform.clock.t = _at(2026, 10, 23)
    for who in (olga, adam):
        costs = views.ve_costs(who, ve_id)
        assert costs["current"]["fees_cents"] == 500 and costs["current"]["usage"]["attempts"] == 3
        assert [f["function_id"] for f in costs["functions"]] == ["train", "score"]
        assert costs["functions"][0]["cost_ucents"] == 2 * costs["functions"][1]["cost_ucents"]
    with pytest.raises(ForbiddenError):
        views.ve_costs(dina, ve_id)                       # Developers see no money
    with pytest.raises(NotFoundError):
        views.ve_costs(vic, ve_id)                        # no role: no hint the VE exists
    keyed = dataclasses.replace(olga, key_id="k1")
    with pytest.raises(ForbiddenError):
        views.ve_costs(keyed, ve_id)                      # SDK keys never see money


def test_my_billing_lists_my_ves_and_my_storage_and_nothing_of_others(platform):
    views, intake, bills = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana, luis = platform.user("ana"), platform.user("luis")
    mine = platform.buy(ana, name="ml")["ve"]["ve_id"]
    theirs = platform.buy(luis, name="etl")["ve"]["ve_id"]
    bucket = platform.buckets.create(ana, "sales")["bucket_id"]
    _run(intake, mine, "a", _at(2026, 10, 25))
    intake.handle("StorageSizesReported", SOURCE, {"hour_start": _at(2026, 10, 25, 0), "sizes": {bucket: GIB}})
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()
    assert views.storage.close_due() == [f"storage:user:{ana.user_id}:2026-10"]
    view = views.my_billing(ana)
    assert [v["name"] for v in view["ves"]] == ["ml"] and view["ves"][0]["current"]["period_index"] == 1
    assert [b["bill_id"] for b in view["ve_bills"]] == [f"{mine}:0"]
    assert view["ve_bills"][0]["usage"]["attempts"] == 1
    assert view["storage"]["current"]["month"] == "2026-11"
    assert [b["month"] for b in view["storage"]["bills"]] == ["2026-10"]
    assert view["open_total_cents"] == 500 + view["storage"]["current"]["total_cents"]
    october = views.my_storage_bill(ana, "2026-10")
    assert october["status"] == "closed"
    assert october["storage"]["buckets"][0]["bucket_id"] == bucket
    assert [b["bill_id"] for b in views.my_billing(luis)["ve_bills"]] == [f"{theirs}:0"]   # only his own
    assert views.my_billing(luis)["storage"]["bills"] == []


def test_one_bill_by_id_for_its_payer_only(platform):
    views, intake, bills = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana, luis, adam = platform.user("ana"), platform.user("luis"), platform.user("adam")
    mine = platform.buy(ana, name="ml")["ve"]["ve_id"]
    bucket = platform.buckets.create(ana, "sales")["bucket_id"]
    intake.handle("StorageSizesReported", SOURCE, {"hour_start": _at(2026, 10, 25, 0), "sizes": {bucket: GIB}})
    team = platform.teams.create(ana, "lab")
    platform.teams.add_member(team["team_id"], adam.user_id, R.ADMIN, by=ana.user_id)
    shared = platform.buy(ana, name="shared", team_id=team["team_id"])["ve"]["ve_id"]
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()
    views.storage.close_due()
    ve_bill = views.bill(ana, f"{mine}:0")
    assert ve_bill["type"] == "ve" and ve_bill["ve_name"] == "ml" and ve_bill["ve_deleted"] is False
    storage = views.bill(ana, f"storage:user:{ana.user_id}:2026-10")
    assert storage["type"] == "storage" and storage["status"] == "closed"
    assert storage["storage"]["buckets"][0]["name"] == "ana/sales"          # the name, not only the key
    assert views.bill(ana, f"storage:user:{ana.user_id}:2026-11")["status"] == "open"   # this month = live
    assert views.bill(adam, f"{shared}:0")["ve_name"] == "shared"            # Team Admin sees the team's
    for bill_id in (f"{mine}:0", f"storage:user:{ana.user_id}:2026-10", f"{shared}:0"):
        with pytest.raises(NotFoundError):
            views.bill(luis, bill_id)                                         # not the payer: no hint
    with pytest.raises(NotFoundError):
        views.bill(adam, f"{mine}:0")                                         # Ana's own, not the team's
    with pytest.raises(ForbiddenError):
        views.bill(dataclasses.replace(ana, key_id="k1"), f"{mine}:0")        # never for an SDK key


def test_team_billing_is_for_team_owner_and_admins(platform):
    views, _, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 5)
    olga, adam, mia = (platform.user(n) for n in ("olga", "adam", "mia"))
    team = platform.teams.create(olga, "lab")
    platform.teams.add_member(team["team_id"], adam.user_id, R.ADMIN, by=olga.user_id)
    platform.teams.add_member(team["team_id"], mia.user_id, R.MEMBER, by=olga.user_id)
    platform.buy(olga, name="shared", team_id=team["team_id"])
    for who in (olga, adam):
        view = views.team_billing(who, team["team_id"])
        assert [v["name"] for v in view["ves"]] == ["shared"]
        assert view["spending"]["team_cents"] == 500
    with pytest.raises(ForbiddenError):
        views.team_billing(mia, team["team_id"])
    with pytest.raises(ForbiddenError):
        views.team_storage_bill(mia, team["team_id"], "2026-10")


def test_admin_revenue_adds_fees_usage_and_storage_per_payer(platform):
    views, intake, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana, luis = platform.user("ana"), platform.user("luis")
    platform.buy(ana, name="ml", plan_id="standard")
    etl = platform.buy(luis, name="etl")["ve"]["ve_id"]
    _run(intake, etl, "a", _at(2026, 10, 21))
    bucket = platform.buckets.create(ana, "sales")["bucket_id"]
    intake.handle("StorageSizesReported", SOURCE, {"hour_start": _at(2026, 10, 21, 0), "sizes": {bucket: GIB}})
    with pytest.raises(ForbiddenError):
        views.admin_revenue(ana, _at(2026, 10, 1), _at(2026, 11, 1))
    admin = dataclasses.replace(ana, platform_role=R.PLATFORM_ADMIN)
    report = views.admin_revenue(admin, _at(2026, 10, 1), _at(2026, 11, 1))
    plans = {p["plan_id"]: p for p in report["fees_by_plan"]}
    assert plans["standard"]["amount_cents"] == 2500 and plans["basic"]["amount_cents"] == 500
    assert plans["basic"]["ves"] == 1
    (usage,) = report["usage_by_ve"]
    assert usage["ve_id"] == etl and usage["owner"] == f"user:{luis.user_id}" and usage["attempts"] == 1
    top = report["top_spenders"][0]
    assert top["payer_key"] == f"user:{ana.user_id}" and top["fees_ucents"] == 2500 * 1_000_000
    assert top["storage_ucents"] > 0
    assert report["totals"]["total_ucents"] == sum(s["total_ucents"] for s in report["top_spenders"])


def _step(intake, ve_id, job, finished, choreography, run, step, version=1, seconds=3.0, calls=None):
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": job, "attempt": 1, "trace_id": "t", "outcome": "completed",
        "owner_type": "user", "owner_id": "u", "started_by_type": "choreography", "started_by_id": choreography,
        "function_id": "f", "function_version": 1, "endpoint_cores": 2.0, "endpoint_ram_mb": 4096,
        "received_at": finished - seconds, "finished_at": finished, "execution_ms": seconds * 1000,
        "choreography_run_id": run, "choreography_step": step, "choreography_version": version,
        "choreography_calls": calls or []})


def test_choreography_step_costs_for_the_ve_owner_and_admin(platform):
    views, intake, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    olga, dina = platform.user("olga"), platform.user("dina")
    ve_id = platform.buy(olga)["ve"]["ve_id"]
    _share(platform, olga, ve_id, dina, R.DEVELOPER)
    _step(intake, ve_id, "a", _at(2026, 10, 21), "etl", "r1", "load")
    _step(intake, ve_id, "b", _at(2026, 10, 21, 13), "etl", "r1", "train", seconds=9.0)
    _step(intake, ve_id, "c", _at(2026, 10, 22), "etl", "r2", "load", version=2)
    _step(intake, ve_id, "d", _at(2026, 10, 22), "other", "r3", "x")
    _run(intake, ve_id, "e", _at(2026, 10, 22))                               # not a choreography's
    platform.clock.t = _at(2026, 10, 23)

    every = views.choreography_costs(olga, ve_id)
    assert [r["choreography_id"] for r in every["choreographies"]] == ["etl", "other"]
    assert every["total"]["attempts"] == 4
    etl = views.choreography_costs(olga, ve_id, "etl")
    assert [r["step"] for r in etl["by_step"]] == ["train", "load"]           # most expensive first
    assert etl["by_step"][0]["execution_ms"] == 9000 and etl["by_step"][1]["attempts"] == 2
    assert {r["version"]: r["attempts"] for r in etl["by_version"]} == {1: 2, 2: 1}
    assert [r["run_id"] for r in etl["runs"]] == ["r2", "r1"]                 # newest first
    assert etl["total"]["cost_ucents"] == sum(r["cost_ucents"] for r in etl["runs"])
    one = views.choreography_costs(olga, ve_id, "etl", run_id="r1")
    assert [r["step"] for r in one["by_step"]] == ["train", "load"] and "runs" not in one
    with pytest.raises(ForbiddenError):
        views.choreography_costs(dina, ve_id)                                # Developers see no money


def test_a_called_runs_jobs_count_on_the_callers_step_and_split_by_caller(platform):
    views, intake, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    olga = platform.user("olga")
    ve_id = platform.buy(olga)["ve"]["ve_id"]
    called = [{"choreography_id": "daily", "run_id": "d1", "step": "clean", "version": 4}]
    _step(intake, ve_id, "a", _at(2026, 10, 21), "daily", "d1", "count", version=4)
    _step(intake, ve_id, "b", _at(2026, 10, 21), "clean-visits", "c1", "dedupe", seconds=5.0, calls=called)
    _step(intake, ve_id, "c", _at(2026, 10, 21), "clean-visits", "c1", "save", seconds=2.0, calls=called)
    _step(intake, ve_id, "d", _at(2026, 10, 22), "clean-visits", "c2", "dedupe")          # run by hand
    platform.clock.t = _at(2026, 10, 23)

    daily = views.choreography_costs(olga, ve_id, "daily")
    assert {r["step"]: r["attempts"] for r in daily["by_step"]} == {"clean": 2, "count": 1}
    assert daily["by_version"][0] == {**daily["by_version"][0], "version": 4, "attempts": 3}
    assert [r["run_id"] for r in daily["runs"]] == ["d1"] and daily["total"]["attempts"] == 3
    clean = views.choreography_costs(olga, ve_id, "clean-visits")
    assert {r["called_by"]: r["attempts"] for r in clean["by_caller"]} == {"daily": 2, None: 1}
    assert {r["step"]: r["attempts"] for r in clean["by_step"]} == {"dedupe": 2, "save": 1}
    one = views.choreography_costs(olga, ve_id, "daily", run_id="d1")
    assert {r["step"] for r in one["by_step"]} == {"clean", "count"}
    # each job is billed once: the list of every choreography counts it under its own
    every = views.choreography_costs(olga, ve_id)
    assert every["total"]["attempts"] == 4


def test_job_costs_for_the_jobs_list_and_one_jobs_attempts(platform):
    views, intake, _ = _views(platform)
    platform.clock.t = _at(2026, 10, 20)
    olga, dina = platform.user("olga"), platform.user("dina")
    ve_id = platform.buy(olga)["ve"]["ve_id"]
    _share(platform, olga, ve_id, dina, R.DEVELOPER)
    _step(intake, ve_id, "a", _at(2026, 10, 21), "etl", "r1", "load")
    _step(intake, ve_id, "b", _at(2026, 10, 21, 13), "etl", "r1", "train", seconds=9.0)
    platform.clock.t = _at(2026, 10, 23)
    both = views.job_costs(olga, ve_id, ["a", "b", "nope"])
    assert set(both["jobs"]) == {"a", "b"} and both["jobs"]["b"]["execution_ms"] == 9000
    assert "attempts" not in both
    assert set(views.job_costs(olga, ve_id, ["a"])["attempts"]) == {"1"}
    with pytest.raises(ForbiddenError):
        views.job_costs(dina, ve_id, ["a"])
