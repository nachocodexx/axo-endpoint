from datetime import datetime, timezone

import pytest

from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, NotFoundError
from axo_control_plane.domain.periods import add_months, rental_period

DAY = 86400.0


def _at(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y")


def _fees(platform, ve_id):
    return [(c["period_index"], c["kind"], c["plan_id"], c["amount_cents"]) for c in platform.ves.charges(ve_id)]


def _buy_on(platform, when, plan_id="basic", name="ml", **kw):
    platform.clock.t = when
    alice = platform.user("alice")
    ve = platform.buy(alice, name=name, plan_id=plan_id, **kw)["ve"]
    return alice, ve["ve_id"]


def test_month_end_days_follow_the_anchor():
    jan31 = _at(2026, 1, 31)
    assert [_date(add_months(jan31, n)) for n in range(4)] == ["31/01/2026", "28/02/2026", "31/03/2026",
                                                               "30/04/2026"]
    start, end = rental_period(_at(2026, 10, 20), 0, 1)
    assert (_date(start), _date(end)) == ("20/11/2026", "20/12/2026")


def test_buying_charges_the_full_fee_whatever_the_day(platform):
    _, ve_id = _buy_on(platform, _at(2026, 10, 20))
    ve = platform.ves.record(ve_id)
    assert (_date(ve.period_start), _date(ve.period_end)) == ("20/10/2026", "20/11/2026")
    assert _fees(platform, ve_id) == [(0, "plan_fee", "basic", 500)]


def test_renewal_charges_each_new_period_once(platform):
    _, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    platform.ves.renew_due()                                    # safe to repeat
    ve = platform.ves.record(ve_id)
    assert (ve.period_index, _date(ve.period_start), _date(ve.period_end)) == (1, "20/11/2026", "20/12/2026")
    assert _fees(platform, ve_id) == [(0, "plan_fee", "basic", 500), (1, "plan_fee", "basic", 500)]
    # A CP that was down for two periods catches up: each period is charged.
    platform.clock.t = _at(2027, 1, 21)
    platform.ves.renew_due()
    assert platform.ves.record(ve_id).period_index == 3


def test_a_stopped_ve_is_still_rented(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.ves.stop(alice, ve_id)
    platform.clock.t = _at(2026, 11, 21)
    platform.ves.renew_due()
    assert len(_fees(platform, ve_id)) == 2 and platform.ves.record(ve_id).desired == V.DESIRED_STOPPED


def test_upgrade_applies_at_once_and_pays_the_difference(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.clock.t = _at(2026, 11, 5)
    out = platform.ves.change_plan(alice, ve_id, plan_id="standard")
    assert out["applies"] == "now" and out["plan_id"] == "standard"
    ve = platform.ves.record(ve_id)
    assert (_date(ve.period_start), _date(ve.period_end), ve.period_fee_cents) == ("20/10/2026", "20/11/2026",
                                                                                    2500)
    assert _fees(platform, ve_id) == [(0, "plan_fee", "basic", 500), (0, "upgrade", "standard", 2000)]
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    assert _fees(platform, ve_id)[-1] == (1, "plan_fee", "standard", 2500)


def test_two_upgrades_in_one_period_pay_up_to_the_top_fee(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.ves.change_plan(alice, ve_id, plan_id="standard")
    platform.ves.change_plan(alice, ve_id, plan_id="pro", quota_cpu_cores=8, quota_memory_mb=16384)
    assert sum(f[3] for f in _fees(platform, ve_id)) == 9900


def test_downgrade_waits_for_the_next_renewal(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20), plan_id="standard", target_endpoints=4)
    out = platform.ves.change_plan(alice, ve_id, plan_id="basic")
    assert out["applies"] == "at_renewal" and out["plan_id"] == "standard"
    assert out["rental"]["pending_change"]["plan_id"] == "basic"
    reserved_before = platform.capacity.reserved()
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    ve = platform.ves.record(ve_id)
    assert ve.plan_id == "basic" and ve.pending_change is None and ve.period_fee_cents == 500
    assert _fees(platform, ve_id)[-1] == (1, "plan_fee", "basic", 500)
    assert platform.capacity.reserved().cpu_cores < reserved_before.cpu_cores    # smaller base released


def test_an_upgrade_cancels_a_scheduled_downgrade(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20), plan_id="standard")
    platform.ves.change_plan(alice, ve_id, plan_id="basic")
    platform.ves.change_plan(alice, ve_id, plan_id="pro", quota_cpu_cores=8, quota_memory_mb=16384)
    assert platform.ves.record(ve_id).pending_change is None


def test_cancel_runs_to_the_period_end_then_stops_then_deletes(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    bob = platform.user("bob")
    with pytest.raises(NotFoundError):
        platform.ves.cancel(bob, ve_id)
    platform.clock.t = _at(2026, 11, 5)
    platform.ves.cancel(alice, ve_id)
    platform.ves.renew_due()
    assert platform.ves.record(ve_id).desired == V.DESIRED_RUNNING          # still usable
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    ve = platform.ves.record(ve_id)
    assert ve.desired == V.DESIRED_STOPPED and ve.rental_ended_at == _at(2026, 11, 20)
    assert len(_fees(platform, ve_id)) == 1                                  # no renewal
    with pytest.raises(ConflictError):
        platform.ves.start(alice, ve_id)
    platform.clock.t = ve.rental_delete_after + 1
    platform.ves.renew_due()
    assert platform.ves.record(ve_id).desired == V.DESIRED_DELETED


def test_resume_undoes_a_cancel(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.ves.cancel(alice, ve_id)
    platform.ves.resume(alice, ve_id)
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    assert platform.ves.record(ve_id).period_index == 1 and len(_fees(platform, ve_id)) == 2


def test_renting_an_ended_ve_again_starts_a_new_period_now(platform):
    alice, ve_id = _buy_on(platform, _at(2026, 10, 20))
    platform.ves.cancel(alice, ve_id)
    platform.clock.t = _at(2026, 11, 20, 13)
    platform.ves.renew_due()
    platform.clock.t = _at(2026, 12, 3, 9)
    out = platform.ves.resume(alice, ve_id)
    assert out["desired"] == V.DESIRED_RUNNING and out["lifecycle"] == V.STARTING
    ve = platform.ves.record(ve_id)
    assert (_date(ve.period_start), _date(ve.period_end), ve.rental_ended_at) == ("03/12/2026", "03/01/2027", None)
    assert _fees(platform, ve_id)[-1] == (1, "plan_fee", "basic", 500)
    platform.clock.t = _at(2027, 1, 3, 10)
    platform.ves.renew_due()
    assert _date(platform.ves.record(ve_id).period_end) == "03/02/2027"


def test_free_ves_get_zero_fee_periods(platform):
    _, ve_id = _buy_on(platform, _at(2026, 10, 20), plan_id="free")
    platform.clock.t = _at(2026, 11, 21)
    platform.ves.renew_due()
    assert _fees(platform, ve_id) == [(0, "plan_fee", "free", 0), (1, "plan_fee", "free", 0)]
