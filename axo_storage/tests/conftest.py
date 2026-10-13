import itertools

import pytest

from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, VeCredentials
from axo_storage.access import StaticAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import MemoryChunkStore
from axo_storage.server import StorageRouterServer
from axo_storage.service import StorageService

SERVICE_TOKEN = "svc-secret"
VE_TOKENS = {"ml-prod": "prod-secret", "ml-test": "test-secret"}

RULES = {
    "teams": {"ml-lab": ["luis", "ana"]},
    "buckets": {
        "sales": {"quota_bytes": 1000, "grants": {"user:luis": "read", "team:ml-lab": "read_write"}},
        "private": {"grants": {"user:boss": "owner"}},
        "bulk": {"grants": {"user:luis": "read_write"}},
    },
    "virtual_environments": {
        "ml-prod": {"user:luis": "developer", "user:ana": "viewer"},
        "ml-test": {"team:ml-lab": "admin"},
    },
}


@pytest.fixture
def clock():
    ticks = itertools.count(1000)
    return lambda: float(next(ticks))


@pytest.fixture
def catalog():
    c = StorageCatalog(":memory:")
    yield c
    c.close()


@pytest.fixture
def service(catalog, clock):
    def owner(bucket):
        row = catalog.get_bucket(bucket)
        return row.unwrap().created_by if row.is_ok else None

    return StorageService(
        catalog=catalog,
        chunks=MemoryChunkStore(),
        authenticator=Authenticator(VE_TOKENS, [SERVICE_TOKEN]),
        access=StaticAccessChecker(RULES, bucket_owner_fn=owner),
        now_fn=clock,
    )


@pytest.fixture
def server(service):
    srv = StorageRouterServer("tcp://127.0.0.1:0", service, workers=4)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def service_client(server):
    client = AxoStorageClient(server.bind_address, ServiceCredentials(SERVICE_TOKEN), timeout_seconds=5)
    yield client
    client.close()


@pytest.fixture
def prod_client(server):
    client = AxoStorageClient(server.bind_address, VeCredentials("ml-prod", VE_TOKENS["ml-prod"]), timeout_seconds=5)
    yield client
    client.close()
