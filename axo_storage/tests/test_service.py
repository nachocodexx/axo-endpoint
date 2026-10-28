import hashlib

import pytest

from axo_shared import wire
from axo_shared.protocol import Command

from tests.conftest import SERVICE_TOKEN, VE_TOKENS

SVC = {"service_token": SERVICE_TOKEN}
PROD = {"ve_id": "ml-prod", "token": VE_TOKENS["ml-prod"]}


def call(service, op, user="luis", auth=SVC, payload=b"", **fields):
    return service.handle(Command(op, "application/json", {**fields, "auth": auth, "user_id": user}, payload))


@pytest.fixture
def sales(service):
    assert call(service, wire.BUCKET_CREATE, user="boss", name="sales").ok
    return service


def upload(service, data, name="report.csv", user="luis", auth=SVC, chunk_bytes=4, **extra):
    reg = call(service, wire.OBJECT_REGISTER, user=user, auth=auth, bucket="sales", name=name,
               total_size=len(data), chunk_bytes=chunk_bytes, **extra)
    assert reg.ok, reg.error
    version = reg.metadata["version"]
    last = None
    for i in range(reg.metadata["total_chunks"]):
        last = call(service, wire.OBJECT_CHUNK_PUT, user=user, auth=auth, bucket="sales", name=name,
                    version=version, chunk_index=i, payload=data[i * chunk_bytes:(i + 1) * chunk_bytes])
        assert last.ok, last.error
    return version, last


def test_known_size_upload_completes_by_itself_and_reads_back(sales):
    version, last = upload(sales, b"hello world!")
    assert version == 1 and last.metadata["state"] == "complete"
    info = call(sales, wire.OBJECT_INFO, bucket="sales", name="report.csv")
    assert info.metadata["content_hash"] == hashlib.sha256(b"hello world!").hexdigest()
    chunks = [call(sales, wire.OBJECT_CHUNK_GET, bucket="sales", name="report.csv", version=1, chunk_index=i).payload
              for i in range(info.metadata["total_chunks"])]
    assert b"".join(chunks) == b"hello world!"


def test_writing_again_creates_the_next_version_and_reads_get_latest_complete(sales):
    upload(sales, b"v1-data")
    reg = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="report.csv", total_size=8, chunk_bytes=4)
    assert reg.metadata["version"] == 2                       # v2 still uploading...
    info = call(sales, wire.OBJECT_INFO, bucket="sales", name="report.csv")
    assert info.metadata["version"] == 1                      # ...so readers still get v1
    got = call(sales, wire.OBJECT_CHUNK_GET, bucket="sales", name="report.csv", version=2, chunk_index=0)
    assert got.error_name == "OBJECT_NOT_COMPLETE"


def test_an_existing_version_can_never_be_rewritten(sales):
    upload(sales, b"abc")
    again = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="report.csv", version=1, total_size=3, chunk_bytes=4)
    assert again.error_name == "OBJECT_VERSION_EXISTS"


def test_streamed_upload_needs_finalize_and_checks_order(sales):
    reg = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="out.bin", chunk_bytes=4)
    assert reg.metadata["total_chunks"] is None
    put = lambda i, data: call(sales, wire.OBJECT_CHUNK_PUT, bucket="sales", name="out.bin", version=1,
                               chunk_index=i, payload=data)
    assert put(0, b"abcd").ok
    assert put(2, b"ij").ok
    gap = call(sales, wire.OBJECT_FINALIZE, bucket="sales", name="out.bin", version=1)
    assert gap.error_name == "OBJECT_NOT_COMPLETE" and gap.metadata == {}
    assert put(1, b"efgh").ok
    done = call(sales, wire.OBJECT_FINALIZE, bucket="sales", name="out.bin", version=1)
    assert done.ok and done.metadata["total_size"] == 10 and done.metadata["total_chunks"] == 3
    assert put(3, b"zz").error_name == "OBJECT_ALREADY_COMPLETE"


def test_wrong_declared_hash_marks_the_version_failed(sales):
    reg = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="x", total_size=2, chunk_bytes=4, content_hash="nope")
    put = call(sales, wire.OBJECT_CHUNK_PUT, bucket="sales", name="x", version=reg.metadata["version"],
               chunk_index=0, payload=b"hi")
    assert put.error_name == "OBJECT_HASH_MISMATCH"
    assert call(sales, wire.OBJECT_STATUS, bucket="sales", name="x", version=1).metadata["state"] == "failed"


def test_chunk_size_and_range_are_validated(sales):
    call(sales, wire.OBJECT_REGISTER, bucket="sales", name="x", total_size=6, chunk_bytes=4)
    short = call(sales, wire.OBJECT_CHUNK_PUT, bucket="sales", name="x", version=1, chunk_index=0, payload=b"ab")
    assert short.error_name == "STORAGE_CHUNK_SIZE_MISMATCH"
    far = call(sales, wire.OBJECT_CHUNK_PUT, bucket="sales", name="x", version=1, chunk_index=5, payload=b"ab")
    assert far.error_name == "STORAGE_CHUNK_OUT_OF_RANGE"
    status = call(sales, wire.OBJECT_STATUS, bucket="sales", name="x", version=1)
    assert status.metadata["missing_chunks"] == [0, 1]


def test_quota_counts_declared_sizes_and_streamed_chunks(sales):
    too_big = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="big", total_size=1001, chunk_bytes=512)
    assert too_big.error_name == "STORAGE_QUOTA_EXCEEDED"
    assert call(sales, wire.OBJECT_REGISTER, bucket="sales", name="a", total_size=998, chunk_bytes=512).ok
    call(sales, wire.OBJECT_REGISTER, bucket="sales", name="s", chunk_bytes=4)
    over = call(sales, wire.OBJECT_CHUNK_PUT, bucket="sales", name="s", version=1, chunk_index=0, payload=b"abc")
    assert over.error_name == "STORAGE_QUOTA_EXCEEDED"
    info = call(sales, wire.BUCKET_INFO, name="sales")
    assert info.metadata["used_bytes"] == 998 and info.metadata["quota_bytes"] == 1000


def test_jobs_need_bucket_access_and_developer_role_in_their_ve(sales):
    upload(sales, b"data")
    read = lambda user: call(sales, wire.OBJECT_CHUNK_GET, user=user, auth=PROD,
                             bucket="sales", name="report.csv", version=1, chunk_index=0)
    assert read("luis").ok                                    # read grant + developer in ml-prod
    assert read("ana").error_name == "STORAGE_ACCESS_DENIED"  # bucket access, but only viewer in ml-prod
    assert read("eve").error_name == "STORAGE_ACCESS_DENIED"  # no bucket access


def test_bucket_pages_check_only_bucket_access(sales):
    upload(sales, b"data", user="ana")                        # ana writes via team, no VE involved
    denied = call(sales, wire.OBJECT_DELETE, user="eve", bucket="sales", name="report.csv")
    assert denied.error_name == "STORAGE_ACCESS_DENIED"


def test_credentials_are_required_and_only_services_create_buckets(service):
    assert call(service, wire.BUCKET_CREATE, auth={"service_token": "wrong"}, name="b").error_name == "STORAGE_UNAUTHENTICATED"
    assert call(service, wire.BUCKET_CREATE, auth={"ve_id": "ml-prod", "token": "wrong"}, name="b").error_name == "STORAGE_UNAUTHENTICATED"
    assert call(service, wire.BUCKET_CREATE, auth=PROD, name="b").error_name == "STORAGE_UNAUTHENTICATED"
    assert call(service, wire.BUCKET_CREATE, user="", name="b").error_name == "STORAGE_INVALID_REQUEST"
    assert call(service, wire.BUCKET_CREATE, name="b").ok
    assert call(service, wire.BUCKET_CREATE, name="b").error_name == "BUCKET_ALREADY_EXISTS"
    assert service.handle(Command(wire.PING, "application/json", {})).ok   # health check, no credentials


def test_names_cannot_escape_their_bucket(sales):
    for bad in ("../other/x", "a/../../x", "/abs", "a//b", ""):
        r = call(sales, wire.OBJECT_REGISTER, bucket="sales", name=bad, total_size=1, chunk_bytes=4)
        assert r.error_name == "STORAGE_INVALID_REQUEST", bad
    assert call(sales, wire.OBJECT_REGISTER, bucket="Sales!", name="x", total_size=1, chunk_bytes=4).error_name == "STORAGE_INVALID_REQUEST"


def test_delete_one_version_or_all(sales):
    upload(sales, b"one")
    upload(sales, b"two")
    assert call(sales, wire.OBJECT_DELETE, bucket="sales", name="report.csv", version=1).metadata["versions"] == [1]
    assert [d["version"] for d in call(sales, wire.OBJECT_LIST, bucket="sales").metadata["objects"]] == [2]
    assert call(sales, wire.OBJECT_DELETE, bucket="sales", name="report.csv").metadata["versions"] == [2]
    assert call(sales, wire.OBJECT_INFO, bucket="sales", name="report.csv").error_name == "OBJECT_NOT_FOUND"
    assert call(sales, wire.BUCKET_INFO, name="sales").metadata["used_bytes"] == 0


def test_zero_byte_object_is_complete_at_once(sales):
    reg = call(sales, wire.OBJECT_REGISTER, bucket="sales", name="empty", total_size=0, chunk_bytes=4)
    assert reg.metadata["state"] == "complete" and reg.metadata["total_chunks"] == 0


def test_ve_tokens_can_be_derived_from_a_shared_secret(catalog):
    from axo_shared.axo_storage import derive_ve_token
    from axo_storage.access import AllowAllAccessChecker
    from axo_storage.auth import Authenticator
    from axo_storage.chunk_store import MemoryChunkStore
    from axo_storage.service import StorageService

    service = StorageService(
        catalog=catalog, chunks=MemoryChunkStore(),
        authenticator=Authenticator({}, ["svc"], ve_token_secret="s3cret"),
        access=AllowAllAccessChecker(),
    )
    call(service, wire.BUCKET_CREATE, auth={"service_token": "svc"}, name="b")
    good = {"ve_id": "new-ve", "token": derive_ve_token("s3cret", "new-ve")}
    bad = {"ve_id": "new-ve", "token": derive_ve_token("s3cret", "other-ve")}
    assert call(service, wire.OBJECT_LIST, auth=good, bucket="b").ok
    assert call(service, wire.OBJECT_LIST, auth=bad, bucket="b").error_name == "STORAGE_UNAUTHENTICATED"


def test_each_complete_version_is_reported_once_with_its_writer_and_source(catalog, clock):
    from axo_storage.access import AllowAllAccessChecker
    from axo_storage.auth import Authenticator
    from axo_storage.chunk_store import MemoryChunkStore
    from axo_storage.service import StorageService
    from axo_storage.usage import object_written

    reports = []
    service = StorageService(
        catalog=catalog, chunks=MemoryChunkStore(), authenticator=Authenticator(VE_TOKENS, [SERVICE_TOKEN]),
        access=AllowAllAccessChecker(), now_fn=clock,
        on_written=lambda row, caller: reports.append(object_written(row, caller)),
    )
    assert call(service, wire.BUCKET_CREATE, user="boss", name="sales").ok
    upload(service, b"hello world!", auth=PROD, source="choreography:ch-1")
    reg = call(service, wire.OBJECT_REGISTER, bucket="sales", name="report.csv", total_size=3, chunk_bytes=4,
               content_hash="not-the-hash")
    bad = call(service, wire.OBJECT_CHUNK_PUT, bucket="sales", name="report.csv", version=reg.metadata["version"],
               chunk_index=0, payload=b"bad")
    assert bad.error_name == "OBJECT_HASH_MISMATCH"                   # failed version: not reported

    [(data, message_id)] = reports
    assert message_id == "written:sales:report.csv:1"
    assert (data["bucket_key"], data["name"], data["version"], data["size"]) == ("sales", "report.csv", 1, 12)
    assert (data["user_id"], data["writer_ve_id"], data["source"]) == ("luis", "ml-prod", "choreography:ch-1")


def test_file_pages_show_one_row_per_file_with_folders_first(sales):
    for name in ["b.csv", "a.csv", "2024/march/x.csv", "2024/april.csv", "2023/y.csv"]:
        upload(sales, b"data", name=name)
    upload(sales, b"again", name="a.csv")
    top = call(sales, wire.OBJECT_LIST, bucket="sales", files=True).metadata
    assert [(e["kind"], e["name"]) for e in top["entries"]] == [
        ("folder", "2023/"), ("folder", "2024/"), ("file", "a.csv"), ("file", "b.csv")]
    a = top["entries"][2]
    assert (a["versions"], a["version"], a["total_size"], a["state"]) == (2, 2, 5, "complete")
    assert top["entries"][1]["files"] == 2 and top["next"] is None
    inside = call(sales, wire.OBJECT_LIST, bucket="sales", files=True, prefix="2024/").metadata["entries"]
    assert [e["name"] for e in inside] == ["2024/march/", "2024/april.csv"]


def test_file_pages_continue_after_the_last_entry_shown(sales):
    for name in ["d/1", "a", "b", "c"]:
        upload(sales, b"x", name=name)
    first = call(sales, wire.OBJECT_LIST, bucket="sales", files=True, limit=2).metadata
    assert [e["name"] for e in first["entries"]] == ["d/", "a"] and first["next"] == "a"
    second = call(sales, wire.OBJECT_LIST, bucket="sales", files=True, limit=2, after="a").metadata
    assert [e["name"] for e in second["entries"]] == ["b", "c"] and second["next"] is None


def test_bucket_figures_come_for_every_bucket_in_one_call(sales):
    assert call(sales, wire.BUCKET_CREATE, user="boss", name="empty").ok
    upload(sales, b"1234", name="a")
    upload(sales, b"12", name="a")
    upload(sales, b"1", name="b")
    stats = call(sales, wire.BUCKET_STATS, names=["sales", "empty"]).metadata["buckets"]
    assert (stats["sales"]["used_bytes"], stats["sales"]["files"]) == (7, 2)
    assert stats["sales"]["last_change"] is not None
    assert stats["empty"] == {"used_bytes": 0, "files": 0, "last_change": None}
    assert call(sales, wire.BUCKET_STATS, auth=PROD, names=["sales"]).error_name == "STORAGE_UNAUTHENTICATED"
