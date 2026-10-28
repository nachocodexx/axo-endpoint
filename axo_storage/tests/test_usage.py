from axo_shared import wire
from axo_shared.protocol import Command

from axo_storage.auth import SERVICE, VE, Caller
from axo_storage.usage import STORAGE_SIZES_REPORTED, STORAGE_USAGE_REPORTED, UsageMeter, UsageReporter


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_meter_counts_per_minute_bucket_ve_and_user():
    clock = Clock()
    meter = UsageMeter(now_fn=clock)
    endpoint = Caller(kind=VE, user_id="ana", virtual_environment_id="ml")
    page = Caller(kind=SERVICE, user_id="ana")
    meter.record("bkt-1", endpoint, bytes_read=100)
    meter.record("bkt-1", endpoint, bytes_read=50)
    meter.record("bkt-1", page, bytes_written=10)
    assert meter.drain() == {}                                  # the minute isn't over
    clock.t += 60
    (rows,) = meter.drain().values()
    by_ve = {r["ve_id"]: r for r in rows}
    assert by_ve["ml"] == {"bucket": "bkt-1", "ve_id": "ml", "user_id": "ana", "requests": 2,
                           "bytes_read": 150, "bytes_written": 0}
    assert by_ve[None]["requests"] == 1 and by_ve[None]["bytes_written"] == 10
    assert meter.drain() == {}


def test_reporter_sends_minutes_and_hourly_sizes_with_fixed_ids():
    clock = Clock(3600 * 500_000.0)
    meter = UsageMeter(now_fn=clock)
    sent = []
    reporter = UsageReporter(meter, lambda: {"bkt-1": 42}, lambda t, d, m: sent.append((t, d, m)), "s1",
                             now_fn=clock)
    reporter.tick()
    assert [s[0] for s in sent] == [STORAGE_SIZES_REPORTED]
    assert sent[0][1]["sizes"] == {"bkt-1": 42} and sent[0][2] == f"storage-sizes:s1:{int(clock.t)}"
    meter.record("bkt-1", Caller(kind=VE, user_id="ana", virtual_environment_id="ml"))
    clock.t += 61
    reporter.tick()
    assert [s[0] for s in sent] == [STORAGE_SIZES_REPORTED, STORAGE_USAGE_REPORTED]   # same hour: no new sizes
    clock.t += 3600
    reporter.tick()
    assert sent[-1][0] == STORAGE_SIZES_REPORTED


def test_stop_sends_the_minute_in_progress():
    meter = UsageMeter()
    sent = []
    reporter = UsageReporter(meter, dict, lambda t, d, m: sent.append(t), "s1")
    meter.record("bkt-1", Caller(kind=SERVICE, user_id="ana"))
    reporter.stop()
    assert sent == [STORAGE_USAGE_REPORTED]


def test_successful_requests_are_metered_with_their_bytes(catalog):
    from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, VeCredentials
    from axo_storage.access import StaticAccessChecker
    from axo_storage.auth import Authenticator
    from axo_storage.chunk_store import MemoryChunkStore
    from axo_storage.server import StorageRouterServer
    from axo_storage.service import StorageService
    from tests.conftest import RULES, SERVICE_TOKEN, VE_TOKENS

    clock = Clock()
    meter = UsageMeter(now_fn=clock)
    service = StorageService(catalog=catalog, chunks=MemoryChunkStore(),
                             authenticator=Authenticator(VE_TOKENS, [SERVICE_TOKEN]),
                             access=StaticAccessChecker(RULES, bucket_owner_fn=lambda b: None),
                             now_fn=clock, meter=meter)
    server = StorageRouterServer("tcp://127.0.0.1:0", service, workers=2)
    server.start()
    admin = AxoStorageClient(server.bind_address, ServiceCredentials(SERVICE_TOKEN), timeout_seconds=5)
    job = AxoStorageClient(server.bind_address, VeCredentials("ml-prod", VE_TOKENS["ml-prod"]), timeout_seconds=5)
    try:
        assert admin.create_bucket("boss", "sales").is_ok                 # housekeeping: not counted
        assert job.upload("luis", "sales", "a.bin", b"x" * 300, chunk_bytes=100).is_ok   # 3 chunks
        assert job.download("luis", "sales", "a.bin").unwrap() == b"x" * 300
        assert job.download("luis", "sales", "missing.bin").is_err        # failed: not counted
        assert job.list("luis", "sales").is_ok
        clock.t += 60
        (rows,) = meter.drain().values()
        (row,) = rows
        assert (row["bucket"], row["ve_id"], row["user_id"]) == ("sales", "ml-prod", "luis")
        assert row["bytes_written"] == 300 and row["bytes_read"] == 300
        # One request per object operation (upload, download, list), not per chunk call.
        assert row["requests"] == 3
    finally:
        admin.close()
        job.close()
        server.stop()
