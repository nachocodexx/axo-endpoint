"""Endpoint dataio against a real (in-process) axo_storage: the resolver acts
for the job's user with this VE's token; chunks are cached on the endpoint."""
import pytest

from axo_endpoint import dataio
from axo_endpoint.core.dataio import ChunkCache, DataIOResolver, IORef
from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, VeCredentials
from axo_storage.access import AccessChecker, StaticAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import MemoryChunkStore
from axo_storage.server import StorageRouterServer
from axo_storage.service import StorageService

RULES = {
    "buckets": {"sales": {"grants": {"user:luis": "read_write"}}},
    "virtual_environments": {"ml-prod": {"user:luis": "developer", "user:ana": "developer"}},
}


class Switchable(AccessChecker):
    """Static rules, plus a switch to revoke everything mid-test."""

    def __init__(self):
        self.inner = StaticAccessChecker(RULES)
        self.revoked = False

    def check(self, query):
        if self.revoked:
            return StaticAccessChecker({}).check(query)
        return self.inner.check(query)


@pytest.fixture
def storage():
    access = Switchable()
    service = StorageService(
        catalog=StorageCatalog(":memory:"), chunks=MemoryChunkStore(),
        authenticator=Authenticator({"ml-prod": "tok"}, ["svc"]), access=access,
    )
    server = StorageRouterServer("tcp://127.0.0.1:0", service, workers=4)
    server.start()
    admin = AxoStorageClient(server.bind_address, ServiceCredentials("svc"))
    admin.create_bucket("luis", "sales").unwrap()
    yield server, access
    admin.close()
    server.stop()


@pytest.fixture
def make_resolver(storage):
    server, _ = storage
    clients = []

    def build(users=None, recheck_s=5.0, now_fn=None, on_io=None, ve_id=None):
        client = AxoStorageClient(server.bind_address, VeCredentials("ml-prod", "tok"), timeout_seconds=5)
        clients.append(client)
        kwargs = {"now_fn": now_fn} if now_fn else {}
        return DataIOResolver(
            client=client, cache=ChunkCache(10_000_000),
            user_for_job=lambda job_id: ({"job": "luis"} if users is None else users).get(job_id),
            access_recheck_seconds=recheck_s, on_io=on_io, virtual_environment_id=ve_id, **kwargs,
        )

    yield build
    for c in clients:
        c.close()


class _Channel:
    """What a worker's PipeIOChannel does, minus the pipe."""

    def __init__(self, resolver, job_id="job"):
        self.resolver, self.job_id = resolver, job_id

    def request(self, op, ref, data):
        return self.resolver.resolve(self.job_id, op, ref, data).unwrap()


@pytest.fixture
def bound(make_resolver):
    resolver = make_resolver()
    token = dataio.bind_channel(_Channel(resolver))
    yield resolver
    dataio.reset_channel(token)


def test_write_creates_versions_and_read_gets_latest_or_pinned(bound):
    first = dataio.write({"a": 1}, IORef(bucket="sales", name="cfg.pkl", format="pickle"))
    second = dataio.write({"a": 2}, IORef(bucket="sales", name="cfg.pkl", format="pickle"))
    assert (first.version, second.version) == (1, 2)
    assert dataio.read(IORef(bucket="sales", name="cfg.pkl", format="pickle")) == {"a": 2}
    assert dataio.read(IORef(bucket="sales", name="cfg.pkl", version=1, format="pickle")) == {"a": 1}


def test_write_chunks_recuts_any_chunk_sizes_and_iter_chunks_reads_back(bound):
    pieces = [b"a" * 7, b"b" * 2, b"c" * 13]
    ref = dataio.write_chunks(IORef(bucket="sales", name="stream.bin"), pieces, chunk_bytes=5)
    got = list(dataio.iter_chunks(IORef(bucket="sales", name="stream.bin")))
    assert ref.version == 1
    assert [len(c) for c in got] == [5, 5, 5, 5, 2]
    assert b"".join(got) == b"".join(pieces)


def test_second_read_is_served_from_the_endpoint_cache(bound):
    dataio.write(b"x" * 600_000, IORef(bucket="sales", name="big.bin"))   # 3 chunks of 256 KiB
    dataio.read(IORef(bucket="sales", name="big.bin"))
    misses = bound._cache.stats()["misses"]
    assert dataio.read(IORef(bucket="sales", name="big.bin")) == b"x" * 600_000
    assert bound._cache.stats()["misses"] == misses and bound._cache.stats()["hits"] >= 3


def test_bytes_per_bucket_and_cache_hits_are_reported_for_the_job(make_resolver):
    seen = []
    resolver = make_resolver(on_io=lambda job_id, bucket, **io: seen.append((job_id, bucket, io)))
    token = dataio.bind_channel(_Channel(resolver))
    try:
        dataio.write(b"x" * 600_000, IORef(bucket="sales", name="big.bin"))
        dataio.read(IORef(bucket="sales", name="big.bin"))
        dataio.read(IORef(bucket="sales", name="big.bin"))
    finally:
        dataio.reset_channel(token)
    assert all(job == "job" and bucket == "sales" for job, bucket, _ in seen)
    assert sum(io.get("bytes_written", 0) for *_, io in seen) == 600_000
    assert sum(io.get("bytes_read", 0) for *_, io in seen) == 600_000        # the second read came from cache
    assert [io["cache_hit"] for *_, io in seen if "cache_hit" in io] == [False] * 3 + [True] * 3


def test_user_without_bucket_access_is_refused(make_resolver):
    resolver = make_resolver(users={"job": "eve"})
    result = resolver.resolve("job", "read", IORef(bucket="sales", name="x"), None)
    assert "STORAGE_ACCESS_DENIED" in str(result.unwrap_err())


def test_revoked_access_stops_cached_reads_after_the_recheck_window(storage, make_resolver):
    _, access = storage
    now = [0.0]
    resolver = make_resolver(recheck_s=5.0, now_fn=lambda: now[0])
    _Channel(resolver).request("write", IORef(bucket="sales", name="k"), b"secret")
    assert resolver.resolve("job", "read", IORef(bucket="sales", name="k"), None).unwrap() == b"secret"

    access.revoked = True
    chunk = IORef(bucket="sales", name="k", version=1, chunk_index=0)
    assert resolver.resolve("job", "read_chunk", chunk, None).is_ok     # still within the window
    now[0] = 6.0
    # past the window the endpoint asks axo_storage again (this test's
    # storage has no answer cache of its own), which now refuses
    assert "STORAGE_ACCESS_DENIED" in str(resolver.resolve("job", "read_chunk", chunk, None).unwrap_err())


def test_without_storage_or_without_a_user_dataio_is_unavailable(make_resolver):
    no_storage = DataIOResolver(client=None, cache=None, user_for_job=lambda j: "luis")
    assert no_storage.resolve("job", "read", IORef(bucket="b", name="x"), None).unwrap_err().name == "DATAIO_UNAVAILABLE"
    resolver = make_resolver(users={})
    assert resolver.resolve("job", "read", IORef(bucket="b", name="x"), None).unwrap_err().name == "DATAIO_UNAVAILABLE"


def test_write_async_returns_at_once_and_flush_waits_for_it(bound):
    dataio.write_async({"rows": 3}, IORef(bucket="sales", name="summary.json", format="json"))
    assert bound.flush("job") == []
    assert dataio.read(IORef(bucket="sales", name="summary.json", format="json")) == {"rows": 3}
    assert bound.flush("job") == []                         # nothing pending any more


def test_a_failed_background_write_is_a_warning_not_an_error(bound):
    dataio.write_async(b"x", IORef(bucket="nope", name="a.bin"))
    warnings = bound.flush("job")
    assert len(warnings) == 1 and "nope/a.bin failed" in warnings[0]


def test_every_write_says_which_job_made_it(storage, make_resolver):
    from axo_endpoint.core.dataio.protocol import OPEN_STREAM, WRITE
    server, _ = storage
    resolver = make_resolver(ve_id="ml-prod")
    resolver.resolve("job", WRITE, IORef(bucket="sales", name="a.csv"), b"x").unwrap()
    resolver.resolve("job", OPEN_STREAM, IORef(bucket="sales", name="b.bin"), None).unwrap()
    admin = AxoStorageClient(server.bind_address, ServiceCredentials("svc"))
    try:
        rows = admin.list("luis", "sales").unwrap()
    finally:
        admin.close()
    assert {r["name"]: r["source"] for r in rows} == {"a.csv": "job:ml-prod:job", "b.bin": "job:ml-prod:job"}
