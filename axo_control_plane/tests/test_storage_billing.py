from datetime import datetime, timezone

from axo_control_plane.application.billing import ucents_to_cents
from axo_control_plane.application.buckets import CP_PRINCIPAL
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.storage_metering import GIB, StorageMetering, hours_in_month

SOURCE = "storage:axo-storage"


def _at(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _setup(platform):
    platform.clock.t = _at(2026, 10, 1, 9)
    ana, luis = platform.user("ana"), platform.user("luis")
    bucket_id = platform.buckets.create(ana, "sales")["bucket_id"]
    ve_id = platform.buy(luis, name="etl")["ve"]["ve_id"]
    metering = StorageMetering(platform.db, platform.catalog, platform.history, close_after_s=3600,
                               now_fn=platform.clock)
    intake = VemIntake(platform.db, platform.history, platform.notifications, platform.catalog,
                       storage=metering, now_fn=platform.clock)
    return ana, luis, bucket_id, ve_id, metering, intake


def _usage(intake, when, *rows):
    assert intake.handle("StorageUsageReported", SOURCE, {"bin_start": when, "bin_width_s": 60, "rows": list(rows)})


def _sizes(intake, when, sizes):
    assert intake.handle("StorageSizesReported", SOURCE, {"hour_start": when, "sizes": sizes})


def test_the_reader_pays_requests_and_transfer_the_owner_pays_storage(platform):
    ana, luis, bucket_id, ve_id, metering, intake = _setup(platform)
    prices = platform.catalog.prices()
    # Luis's job reads 2 GiB in 40 reads + uploads 1 GiB in 10 writes.
    _usage(intake, _at(2026, 10, 5),
           {"bucket": bucket_id, "ve_id": ve_id, "user_id": luis.user_id, "requests": 50,
            "bytes_read": 2 * GIB, "bytes_written": GIB})
    # Ana downloads 1 GiB from the bucket page.
    _usage(intake, _at(2026, 10, 6),
           {"bucket": bucket_id, "ve_id": None, "user_id": ana.user_id, "requests": 1, "bytes_read": GIB,
            "bytes_written": 0})
    # 10 GiB kept the whole month.
    for hour in range(hours_in_month(_at(2026, 10, 1))):
        _sizes(intake, _at(2026, 10, 1) + hour * 3600, {bucket_id: 10 * GIB})

    luis_bill = metering.current("user", luis.user_id)
    assert luis_bill["requests"]["requests"] == 50 and luis_bill["requests"]["bytes_written"] == GIB
    assert luis_bill["requests"]["request_ucents"] == 50 * prices.storage_per_request_ucents
    assert luis_bill["requests"]["transfer_ucents"] == 2 * prices.transfer_per_gb_ucents    # uploads free
    assert luis_bill["storage"]["ucents"] == 0                                              # not his bucket

    ana_bill = metering.current("user", ana.user_id)
    assert round(ana_bill["storage"]["gb_months"], 6) == 10.0
    assert abs(ana_bill["storage"]["ucents"] - 10 * prices.storage_per_gb_month_ucents) <= 744
    assert ana_bill["requests"]["requests"] == 1
    assert ana_bill["total_cents"] == ucents_to_cents(ana_bill["total_ucents"])


def test_platform_buckets_and_the_cps_own_requests_are_free(platform):
    _, _, bucket_id, ve_id, metering, intake = _setup(platform)
    _usage(intake, _at(2026, 10, 5),
           {"bucket": "axo/ve-x-functions", "ve_id": ve_id, "user_id": "u", "requests": 9, "bytes_read": GIB,
            "bytes_written": 0},
           {"bucket": bucket_id, "ve_id": None, "user_id": CP_PRINCIPAL, "requests": 3, "bytes_read": 0,
            "bytes_written": 0})
    _sizes(intake, _at(2026, 10, 5), {"axo/ve-x-functions": GIB})
    assert platform.db["storage_usage"].count_documents({}) == 0
    assert platform.db["storage_size_hours"].count_documents({}) == 0


def test_a_ves_results_bucket_is_billed_to_the_ves_owner(platform):
    _, luis, _, ve_id, metering, intake = _setup(platform)
    prices = platform.catalog.prices()
    runs = f"axo/ve-{ve_id}-results"
    _usage(intake, _at(2026, 10, 5),
           {"bucket": runs, "ve_id": ve_id, "user_id": f"vem:{ve_id}", "requests": 4, "bytes_read": GIB,
            "bytes_written": GIB})
    for hour in range(hours_in_month(_at(2026, 10, 1))):
        _sizes(intake, _at(2026, 10, 1) + hour * 3600, {runs: 2 * GIB})
    _sizes(intake, _at(2026, 10, 2), {"axo/ve-gone-results": GIB})           # a VE that doesn't exist: dropped
    bill = metering.current("user", luis.user_id)
    assert bill["requests"]["requests"] == 4
    assert bill["requests"]["transfer_ucents"] == prices.transfer_per_gb_ucents
    assert round(bill["storage"]["gb_months"], 6) == 2.0


def test_resends_are_kept_once(platform):
    _, luis, bucket_id, ve_id, metering, intake = _setup(platform)
    row = {"bucket": bucket_id, "ve_id": ve_id, "user_id": luis.user_id, "requests": 5, "bytes_read": 0,
           "bytes_written": 0}
    _usage(intake, _at(2026, 10, 5), row)
    _usage(intake, _at(2026, 10, 5), row)
    _sizes(intake, _at(2026, 10, 5), {bucket_id: GIB})
    _sizes(intake, _at(2026, 10, 5), {bucket_id: GIB})
    assert metering.current("user", luis.user_id)["requests"]["requests"] == 5
    assert len(list(platform.db["storage_size_hours"].find())) == 1


def test_monthly_bill_closes_one_hour_after_the_month_and_late_items_move_on(platform):
    ana, _, bucket_id, _, metering, intake = _setup(platform)
    _sizes(intake, _at(2026, 10, 31, 23), {bucket_id: GIB})
    platform.clock.t = _at(2026, 11, 1, 0) + 1800
    assert metering.close_due() == []                                     # not 1 h yet
    platform.clock.t = _at(2026, 11, 1, 1) + 1
    (bill_id,) = metering.close_due()
    assert bill_id == f"storage:user:{ana.user_id}:2026-10"
    assert metering.close_due() == []                                     # safe to repeat
    october = metering.get(bill_id)
    assert october["status"] == "closed" and october["storage"]["buckets"][0]["bucket_id"] == bucket_id
    # An October sample that arrives now lands on November's bill.
    _sizes(intake, _at(2026, 10, 31, 22), {bucket_id: GIB})
    _sizes(intake, _at(2026, 11, 2), {bucket_id: GIB})
    assert metering.get(bill_id)["storage"] == october["storage"]           # closed: unchanged
    platform.clock.t = _at(2026, 12, 1, 2)
    metering.close_due()
    november = metering.get(f"storage:user:{ana.user_id}:2026-11")
    assert [line["from_month"] for line in november["late"]] == ["2026-10"]
    assert november["storage"]["ucents"] > 0


def test_team_buckets_are_billed_to_the_team(platform):
    platform.clock.t = _at(2026, 10, 1, 9)
    olga = platform.user("olga")
    team = platform.teams.create(olga, "lab")
    bucket_id = platform.buckets.create(olga, "data", owner_name="lab")["bucket_id"]
    metering = StorageMetering(platform.db, platform.catalog, platform.history, now_fn=platform.clock)
    metering.sizes(SOURCE, {"hour_start": _at(2026, 10, 2), "sizes": {bucket_id: GIB}})
    assert metering.current("team", team["team_id"])["storage"]["gb_months"] > 0
    assert metering.current("user", olga.user_id)["storage"]["gb_months"] == 0
