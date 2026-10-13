import httpx
from option import Ok

from axo_shared import wire
from axo_shared.protocol import Command
from axo_storage.access import AccessChecker, AccessDecision, AccessQuery, ControlPlaneAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import MemoryChunkStore
from axo_storage.service import StorageService

from tests.conftest import SERVICE_TOKEN, VE_TOKENS

SVC = {"service_token": SERVICE_TOKEN}
PROD = {"ve_id": "ml-prod", "token": VE_TOKENS["ml-prod"]}


class Resolving(AccessChecker):
    """Like the Control Plane: names / aliases -> one fixed storage key."""

    names = {"alice/reports": "bkt-1", "ds/reports": "bkt-1", "alice/old-name": "bkt-1"}

    def check(self, query: AccessQuery):
        key = self.names.get(query.bucket)
        if key is None:
            return Ok(AccessDecision(False, "no such bucket"))
        return Ok(AccessDecision(True, "ok", quota_bytes=100, bucket_key=key))


def call(service, op, auth=SVC, payload=b"", user="alice", **fields):
    return service.handle(Command(op, "application/json", {**fields, "auth": auth, "user_id": user}, payload))


def _service():
    return StorageService(catalog=StorageCatalog(":memory:"), chunks=MemoryChunkStore(),
                          authenticator=Authenticator(VE_TOKENS, [SERVICE_TOKEN]), access=Resolving(),
                          now_fn=lambda: 1.0)


def test_names_and_aliases_reach_the_same_stored_bucket():
    service = _service()
    assert call(service, wire.BUCKET_CREATE, name="bkt-1").ok
    reg = call(service, wire.OBJECT_REGISTER, auth=PROD, bucket="alice/reports", name="a.txt", total_size=3,
               chunk_bytes=4)
    assert reg.ok, reg.error
    assert call(service, wire.OBJECT_CHUNK_PUT, auth=PROD, bucket="alice/reports", name="a.txt", version=1,
                chunk_index=0, payload=b"abc").ok
    got = call(service, wire.OBJECT_CHUNK_GET, auth=PROD, bucket="ds/reports", name="a.txt", version=1, chunk_index=0)
    assert got.payload == b"abc"
    listed = call(service, wire.OBJECT_LIST, auth=PROD, bucket="alice/old-name")
    assert [o["name"] for o in listed.metadata["objects"]] == ["a.txt"]
    over = call(service, wire.OBJECT_REGISTER, auth=PROD, bucket="ds/reports", name="b", total_size=500,
                chunk_bytes=4)
    assert over.error_name == "STORAGE_QUOTA_EXCEEDED"           # the CP's quota applies
    refused = call(service, wire.OBJECT_LIST, auth=PROD, bucket="bob/secret")
    assert refused.error_name == "STORAGE_ACCESS_DENIED"         # refused before anything is looked up


def test_bucket_delete_is_for_platform_services_only():
    service = _service()
    assert call(service, wire.BUCKET_CREATE, name="bkt-1").ok
    call(service, wire.OBJECT_REGISTER, auth=PROD, bucket="alice/reports", name="a", total_size=0, chunk_bytes=4)
    assert call(service, wire.BUCKET_DELETE, auth=PROD, name="bkt-1").error_name == "STORAGE_UNAUTHENTICATED"
    deleted = call(service, wire.BUCKET_DELETE, name="bkt-1")
    assert deleted.ok and deleted.metadata["deleted_versions"] == 1
    assert call(service, wire.BUCKET_INFO, name="alice/reports").error_name == "BUCKET_NOT_FOUND"


def test_owner_slash_names_are_valid_bucket_keys():
    service = StorageService(catalog=StorageCatalog(":memory:"), chunks=MemoryChunkStore(),
                             authenticator=Authenticator(VE_TOKENS, [SERVICE_TOKEN]),
                             access=Resolving(), now_fn=lambda: 1.0)
    assert call(service, wire.BUCKET_CREATE, name="axo/ve-ml-prod-7f3k-functions").ok
    assert call(service, wire.BUCKET_CREATE, name="a/b/c").error_name == "STORAGE_INVALID_REQUEST"


def test_control_plane_checker_asks_and_fails_closed():
    asked = []

    def cp(request: httpx.Request):
        asked.append((request.headers["authorization"], request.read()))
        if b"down" in request.content:
            return httpx.Response(503)
        return httpx.Response(200, json={"allowed": True, "reason": "granted", "bucket_key": "bkt-9",
                                         "quota_bytes": 10})

    checker = ControlPlaneAccessChecker("http://cp:9000", "check-token", transport=httpx.MockTransport(cp))
    answer = checker.check(AccessQuery("alice", "alice/reports", "read", "ml-prod"))
    assert answer.unwrap() == AccessDecision(True, "granted", 10, "bkt-9")
    assert asked[0][0] == "Bearer check-token" and b'"virtual_environment_id":"ml-prod"' in asked[0][1].replace(b" ", b"")
    assert checker.check(AccessQuery("alice", "down/x", "read")).is_err


def test_unreachable_control_plane_is_an_error_not_a_yes():
    def boom(request):
        raise httpx.ConnectError("no route")

    checker = ControlPlaneAccessChecker("http://cp:9000", "t", transport=httpx.MockTransport(boom))
    assert checker.check(AccessQuery("alice", "alice/reports", "read")).is_err
