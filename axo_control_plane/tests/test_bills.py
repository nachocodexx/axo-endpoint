from datetime import datetime, timezone

import pytest

from axo_control_plane.application.billing import VeBills, ucents_to_cents
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.domain.errors import LimitReachedError

HOUR = 3600.0


def _at(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _setup(platform, plan_id="basic"):
    platform.clock.t = _at(2026, 10, 20)
    alice = platform.user("alice")
    ve_id = platform.buy(alice, plan_id=plan_id)["ve"]["ve_id"]
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog,
                       now_fn=platform.clock)
    bills = VeBills(platform.db, platform.history, close_after_s=HOUR, now_fn=platform.clock)
    return alice, ve_id, intake, bills


def _run(intake, ve_id, job, finished, outcome="completed", seconds=2.4, cores=2.0, ram_mb=4096):
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": job, "attempt": 1, "trace_id": "t", "outcome": outcome,
        "owner_type": "user", "owner_id": "u", "started_by_type": "user", "started_by_id": "u",
        "function_id": "f", "function_version": 1, "endpoint_cores": cores, "endpoint_ram_mb": ram_mb,
        "received_at": finished - seconds, "finished_at": finished, "execution_ms": seconds * 1000})


def _cost(platform, job):
    return platform.db["job_attempts"].find_one({"job_id": job})["cost"]["total_ucents"]


def test_the_open_bill_is_fee_plus_usage_so_far(platform):
    _, ve_id, intake, bills = _setup(platform)
    _run(intake, ve_id, "a", _at(2026, 10, 25))
    _run(intake, ve_id, "b", _at(2026, 10, 26), outcome="failed_platform")      # never billed
    bill = bills.current(ve_id)
    assert bill["status"] == "open" and bill["fees_cents"] == 500
    assert bill["usage"]["attempts"] == 2 and bill["usage"]["billable_attempts"] == 1
    assert bill["usage_ucents"] == _cost(platform, "a")
    assert bill["total_cents"] == 500 + ucents_to_cents(_cost(platform, "a"))


def test_a_bill_closes_one_hour_after_its_period_and_never_changes(platform):
    _, ve_id, intake, bills = _setup(platform)
    _run(intake, ve_id, "a", _at(2026, 10, 25))
    platform.clock.t = _at(2026, 11, 20, 12) + 30 * 60                # period over, not yet 1 h
    platform.ves.renew_due()
    assert bills.close_due() == []
    platform.clock.t = _at(2026, 11, 20, 13)
    assert bills.close_due() == [f"{ve_id}:0"]
    assert bills.close_due() == []                                      # safe to repeat
    closed = bills.get(ve_id, 0)
    assert closed["status"] == "closed" and closed["usage"]["attempts"] == 1
    assert [f["kind"] for f in closed["fees"]] == ["plan_fee"]
    # A job of the new period doesn't touch the closed bill.
    _run(intake, ve_id, "b", _at(2026, 11, 21))
    assert bills.get(ve_id, 0)["usage"]["attempts"] == 1
    assert bills.current(ve_id)["usage"]["attempts"] == 1 and bills.current(ve_id)["period_index"] == 1


def test_late_usage_goes_on_the_next_bill_labelled(platform):
    _, ve_id, intake, bills = _setup(platform)
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()                                                   # period 0 closed, empty
    _run(intake, ve_id, "late", _at(2026, 11, 20, 11))                 # finished in period 0, arrived now
    _run(intake, ve_id, "now", _at(2026, 11, 22))
    platform.clock.t = _at(2026, 12, 20, 13)
    platform.ves.renew_due()
    bills.close_due()
    assert bills.get(ve_id, 0)["usage"]["attempts"] == 0
    second = bills.get(ve_id, 1)
    assert second["usage"]["attempts"] == 1
    (late,) = second["late_usage"]
    assert late["from_period_index"] == 0 and late["attempts"] == 1
    assert second["usage_ucents"] == _cost(platform, "late") + _cost(platform, "now")


def test_upgrade_difference_is_on_the_bill(platform):
    alice, ve_id, intake, bills = _setup(platform)
    platform.ves.change_plan(alice, ve_id, plan_id="standard")
    assert bills.current(ve_id)["fees_cents"] == 2500


def test_tiny_jobs_add_up_before_rounding(platform):
    _, ve_id, intake, bills = _setup(platform)
    for n in range(50):
        _run(intake, ve_id, f"j{n}", _at(2026, 10, 25) + n, seconds=0.01, cores=1.0, ram_mb=1024)
    bill = bills.current(ve_id)
    assert bill["usage_ucents"] == sum(_cost(platform, f"j{n}") for n in range(50))
    assert bill["usage_cents"] == ucents_to_cents(bill["usage_ucents"])


def test_free_ves_get_a_zero_fee_bill_with_their_usage(platform):
    _, ve_id, intake, bills = _setup(platform, plan_id="free")
    _run(intake, ve_id, "a", _at(2026, 10, 25), cores=1.0, ram_mb=1024)
    bill = bills.current(ve_id)
    assert bill["fees_cents"] == 0 and bill["usage"]["attempts"] == 1


def test_late_usage_after_the_rental_ended_gets_its_own_bill(platform):
    alice, ve_id, intake, bills = _setup(platform)
    platform.ves.cancel(alice, ve_id)
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()
    _run(intake, ve_id, "late", _at(2026, 11, 20, 11))
    platform.clock.t += 2 * HOUR
    (bill_id,) = bills.close_due()
    assert ":late:" in bill_id
    assert platform.db["ve_bills"].find_one({"_id": bill_id})["late_usage"][0]["attempts"] == 1


def test_free_request_limit_is_per_rental_period(platform):
    from tests.test_front_door import _door
    door, _ = _door(platform)
    platform.clock.t = _at(2026, 10, 20)
    alice = platform.user("alice")
    ve_id = platform.buy(alice, plan_id="free")["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"lifecycle": "Running", "heartbeat_at": platform.clock()}})
    platform.catalog.save_plan(platform.catalog.get("free").with_changes(request_limit=2), by="admin")
    for _ in range(2):
        door.decide(alice, ve_id, "POST", "/functions/f/1/jobs", {})
    assert platform.ves.get(alice, ve_id)["requests"] == {"used": 2, "limit": 2}     # shown by the run button
    platform.clock.t = _at(2026, 11, 2)                  # new calendar month, same period: still full
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"heartbeat_at": platform.clock()}})
    with pytest.raises(LimitReachedError):
        door.decide(alice, ve_id, "POST", "/functions/f/1/jobs", {})
    platform.clock.t = _at(2026, 11, 20, 13)             # new period: counts again
    platform.ves.renew_due()
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"heartbeat_at": platform.clock()}})
    assert door.decide(alice, ve_id, "POST", "/functions/f/1/jobs", {}).action == "forward"


def test_team_spending_counts_renewals_and_upgrades(platform):
    platform.clock.t = _at(2026, 10, 5)
    owner = platform.user("olga")
    team = platform.teams.create(owner, "lab")
    ve_id = platform.buy(owner, name="a", team_id=team["team_id"])["ve"]["ve_id"]
    platform.ves.change_plan(owner, ve_id, plan_id="standard")
    assert platform.ves.spending(owner, team["team_id"])["team_cents"] == 2500
    platform.clock.t = _at(2026, 11, 5, 13)
    platform.ves.renew_due()
    november = platform.ves.spending(owner, team["team_id"])
    assert november["month"] == "2026-11" and november["team_cents"] == 2500
    assert november["by_buyer_cents"] == {"renewal": 2500}


def test_a_repeat_after_a_crash_never_stamps_onto_a_saved_bill(platform):
    _, ve_id, intake, bills = _setup(platform)
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()
    # As if the CP died after saving the bill, before marking its fee line.
    platform.db["ve_charges"].update_one({"_id": f"{ve_id}:0:fee"}, {"$set": {"bill_closed": False}})
    _run(intake, ve_id, "late", _at(2026, 11, 20, 11))
    bills.close_due()
    assert platform.db["job_attempts"].find_one({"job_id": "late"}).get("bill_id") is None   # waits for bill 1


def _object_hour(intake, ve_id, hour, size):
    assert intake.handle("ObjectStateHourReported", f"vem:{ve_id}",
                         {"virtual_environment_id": ve_id, "hour_start": hour, "stored_bytes": size})


def test_object_state_is_on_the_ve_bill_by_the_hour(platform):
    from axo_control_plane.application.storage_metering import GIB, hours_in_month

    _, ve_id, intake, bills = _setup(platform)
    prices = platform.catalog.prices()
    start = _at(2026, 10, 21, 0)
    for h in range(24):
        _object_hour(intake, ve_id, start + h * HOUR, 3 * GIB)
    _object_hour(intake, ve_id, start, 3 * GIB)                                   # a resend: counted once
    intake.handle("ObjectStateHourReported", "vem:other-ve", {"virtual_environment_id": ve_id,
                                                              "hour_start": start + 99 * HOUR, "stored_bytes": GIB})
    bill = bills.current(ve_id)
    expected_gb_months = 3 * 24 / hours_in_month(start)
    assert bill["object_state"]["hours"] == 24 and bill["object_state"]["max_bytes"] == 3 * GIB
    assert bill["object_state"]["gb_months"] == pytest.approx(expected_gb_months, abs=1e-6)
    assert bill["object_state"]["ucents"] == pytest.approx(expected_gb_months * prices.object_state_per_gb_month_ucents,
                                                           abs=24)
    assert bill["usage_ucents"] == bill["object_state"]["ucents"]
    assert bill["total_cents"] == 500 + ucents_to_cents(bill["object_state"]["ucents"])

    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    assert bills.close_due() == [f"{ve_id}:0"]
    assert bills.get(ve_id, 0)["object_state"]["hours"] == 24
    _object_hour(intake, ve_id, _at(2026, 11, 21, 0), GIB)                        # next period
    assert bills.get(ve_id, 0)["object_state"]["hours"] == 24
    assert bills.current(ve_id)["object_state"]["hours"] == 1
