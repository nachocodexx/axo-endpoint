from datetime import datetime, timezone

import pytest

from axo_control_plane.application import notifications as N
from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.billing_notices import BillingNotices
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.storage_metering import GIB, StorageMetering
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import InvalidError

DAY = 86400.0


def _at(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _setup(platform):
    bills = VeBills(platform.db, platform.history, close_after_s=3600, now_fn=platform.clock)
    storage = StorageMetering(platform.db, platform.catalog, platform.history, close_after_s=3600,
                              now_fn=platform.clock)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog, storage=storage,
                       now_fn=platform.clock)
    notices = BillingNotices(platform.db, platform.notifications, platform.catalog, bills, grace_s=30 * DAY,
                             now_fn=platform.clock)
    return bills, storage, intake, notices


def _texts(platform, user, kind=None):
    return [n["text"] for n in platform.notifications.list(user.user_id) if kind is None or n["kind"] == kind]


def _run(intake, ve_id, job, finished, seconds=3600.0):
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", {
        "virtual_environment_id": ve_id, "job_id": job, "attempt": 1, "trace_id": "t", "outcome": "completed",
        "owner_type": "user", "owner_id": "u", "started_by_type": "user", "started_by_id": "u",
        "function_id": "f", "function_version": 1, "endpoint_cores": 4.0, "endpoint_ram_mb": 8192,
        "received_at": finished - seconds, "finished_at": finished, "execution_ms": seconds * 1000})


def test_closed_ve_and_storage_bills_are_announced_once(platform):
    bills, storage, intake, notices = _setup(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana = platform.user("ana")
    platform.buy(ana, name="ml-prod")
    bucket = platform.buckets.create(ana, "sales")["bucket_id"]
    intake.handle("StorageSizesReported", "storage:axo-storage",
                  {"hour_start": _at(2026, 10, 21, 0), "sizes": {bucket: 100 * GIB}})
    platform.clock.t = _at(2026, 11, 20, 14)
    platform.ves.renew_due()
    bills.close_due()
    storage.close_due()
    notices.tick()
    notices.tick()                                           # repeats send nothing
    closed = sorted(_texts(platform, ana, N.BILL_CLOSED))
    storage_bill = storage.get(f"storage:user:{ana.user_id}:2026-10")
    assert closed == [f"Storage bill October 2026: ${storage_bill['total_cents'] / 100:.2f}",
                      "ml-prod bill 10/20/2026–11/20/2026: $5.00"]


def test_renewal_and_stop_reminders_come_3_days_before(platform):
    _, _, _, notices = _setup(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana = platform.user("ana")
    paid = platform.buy(ana, name="ml-prod")["ve"]["ve_id"]
    stopping = platform.buy(ana, name="old", plan_id="standard")["ve"]["ve_id"]
    platform.buy(ana, name="free", plan_id="free")
    platform.ves.cancel(ana, stopping)
    assert _texts(platform, ana, N.RENTAL_ENDING) == ["old stops on 11/20/2026; data kept until 12/20/2026"]  # at once
    platform.clock.t = _at(2026, 11, 16)                     # 4 days before: nothing yet
    notices.reminders()
    assert _texts(platform, ana, N.RENEWAL_SOON) == []
    platform.clock.t = _at(2026, 11, 17, 13)
    notices.reminders()
    notices.reminders()
    assert _texts(platform, ana, N.RENEWAL_SOON) == ["ml-prod renews on 11/20/2026 for $5.00"]   # Free: none
    assert len(_texts(platform, ana, N.RENTAL_ENDING)) == 2   # cancel notice + 3 days before
    assert platform.ves.record(paid).cancel_at_period_end is False


def test_a_scheduled_downgrade_says_what_will_be_lost(platform):
    platform.clock.t = _at(2026, 10, 20)
    ana = platform.user("ana")
    ve_id = platform.buy(ana, name="ml-prod", plan_id="standard", cpu=16.0, mem=32768)["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"target_endpoints": 5}})
    platform.ves.change_plan(ana, ve_id, plan_id="basic", quota_cpu_cores=4.0, quota_memory_mb=8192)
    (text,) = _texts(platform, ana, N.PLAN_CHANGE_SCHEDULED)
    assert text == ("ml-prod moves to Basic on 11/20/2026: 2 endpoints will be removed, "
                    "history older than 30 days will no longer be shown, "
                    "an object may hold at most 100 MB of state (calls that would make it bigger fail), "
                    "objects keep at most 10 past revisions")


def test_budget_warnings_at_80_and_100_percent_never_stop_anything(platform):
    _, _, intake, notices = _setup(platform)
    platform.clock.t = _at(2026, 10, 20)
    ana, bob = platform.user("ana"), platform.user("bob")
    ve_id = platform.buy(ana, name="ml-prod")["ve"]["ve_id"]
    with pytest.raises(InvalidError):
        platform.ves.set_budget(ana, ve_id, 0)
    inv = platform.sharing.invite_to_ve(ana, ve_id, "bob", R.ADMIN)
    platform.sharing.answer(bob, inv["id"], accept=True)
    with pytest.raises(Exception):
        platform.ves.set_budget(bob, ve_id, 1000)            # the Owner sets it
    assert platform.ves.set_budget(ana, ve_id, 600)["rental"]["budget_cents"] == 600   # $5 fee = 83 %
    notices.budgets()
    assert _texts(platform, ana, N.BUDGET_WARNING) == ["ml-prod reached 80% of its $6.00 budget this period "
                                                       "($5.00 so far)"]
    n = 0
    while platform.db["job_attempts"].count_documents({}) < 200 and \
            not _texts(platform, ana, N.BUDGET_WARNING)[1:]:
        _run(intake, ve_id, f"j{n}", _at(2026, 10, 21) + n)
        n += 1
        notices.budgets()
    assert sorted(t.split(" of ")[0] for t in _texts(platform, ana, N.BUDGET_WARNING)) == [
        "ml-prod reached 100%", "ml-prod reached 80%"]
    notices.budgets()
    assert len(_texts(platform, ana, N.BUDGET_WARNING)) == 2


def test_team_bills_notify_the_team_owner_and_admins_only(platform):
    bills, _, _, notices = _setup(platform)
    platform.clock.t = _at(2026, 10, 5)
    olga, adam, mia = (platform.user(n) for n in ("olga", "adam", "mia"))
    team = platform.teams.create(olga, "lab")
    platform.teams.add_member(team["team_id"], adam.user_id, R.ADMIN, by=olga.user_id)
    platform.teams.add_member(team["team_id"], mia.user_id, R.MEMBER, by=olga.user_id)
    platform.buy(olga, name="shared", team_id=team["team_id"])
    platform.clock.t = _at(2026, 11, 5, 14)
    platform.ves.renew_due()
    bills.close_due()
    notices.closed_bills()
    assert _texts(platform, olga, N.BILL_CLOSED) and _texts(platform, adam, N.BILL_CLOSED)
    assert _texts(platform, mia, N.BILL_CLOSED) == []


def test_a_notice_is_written_in_each_persons_settings_and_keeps_its_values(platform):
    platform.clock.t = _at(2026, 10, 20, hour=3)                 # 03:00 UTC = 19 Oct, 21:00 in Mexico City
    ana, bea = platform.user("ana"), platform.user("bea")
    platform.notifications._settings.update(bea.user_id, {"time_zone": "Europe/Madrid", "date_format": "DD/MM/YYYY",
                                                          "number_format": "1.234,56"})
    platform.notifications.notify([ana.user_id, bea.user_id], N.RENEWAL_SOON, "x renews on {at} for {fee}",
                                  fields={"at": {"type": "date", "value": platform.clock()},
                                          "fee": {"type": "money", "value": 123456}})
    (a,) = platform.notifications.list(ana.user_id)
    (b,) = platform.notifications.list(bea.user_id)
    assert a["text"] == "x renews on 10/19/2026 for $1,234.56"     # the defaults: Mexico City, US formats
    assert b["text"] == "x renews on 20/10/2026 for $1.234,56"
    assert a["template"] == "x renews on {at} for {fee}" and a["fields"]["at"]["value"] == platform.clock()
