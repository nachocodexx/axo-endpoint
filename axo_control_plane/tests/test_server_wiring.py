"""The real composition root (``Server``) built end to end -- only Mongo,
Kurrent, Xolo and Docker are swapped for stand-ins -- so a service the
routes need but ``Server`` forgets to pass (the use case tests build their
own ``Services``) fails here instead of on the platform."""
import mongomock
import pytest
from fastapi.testclient import TestClient

import kurrentdbclient
from axo_control_plane import server as server_module
from axo_control_plane.application.ports import InMemoryHistory
from axo_control_plane.config import Config
from axo_control_plane.infrastructure.api.app import create_app
from axo_control_plane.infrastructure.memory import InMemoryIdentity, InMemoryStackDriver
from axo_control_plane.infrastructure.storage_gateway import AxoStorageGateway


@pytest.fixture
def services(monkeypatch):
    for name in ("AXO_CP_VEM_TOKEN_SECRET", "AXO_CP_STORAGE_VE_TOKEN_SECRET", "AXO_CP_STORAGE_CHECK_TOKEN",
                 "AXO_CP_SDK_KEY_PEPPER", "AXO_CP_STORAGE_SERVICE_TOKEN", "AXO_CP_XOLO_API_KEY"):
        monkeypatch.setenv(name, "a-long-random-test-secret")
    monkeypatch.setattr(server_module, "MongoClient", lambda uri: mongomock.MongoClient())
    monkeypatch.setattr(kurrentdbclient, "KurrentDBClient", lambda uri: None)
    monkeypatch.setattr(server_module, "KurrentHistory", lambda client: InMemoryHistory())
    monkeypatch.setattr(server_module, "XoloIdentity", lambda *a, **k: InMemoryIdentity())
    monkeypatch.setattr(server_module, "DockerStackDriver", InMemoryStackDriver)
    return server_module.Server(Config())._services


def test_bucket_files_get_the_real_storage(services):
    assert services.bucket_files is not None
    assert isinstance(services.bucket_files._store, AxoStorageGateway)


def test_the_new_bucket_routes_answer(services):
    client = TestClient(create_app(services))
    # Signed out: refused, not missing (404) or broken (500).
    assert client.get("/buckets/demo/data/objects").status_code == 401
    assert client.post("/buckets/demo/data/uploads", json={"name": "a", "size": 1}).status_code == 401
    assert client.get("/ves/ve-1/stored").status_code == 401
    # A download link needs no sign-in: an unknown one is a plain 404.
    assert client.get("/downloads/not-a-token").status_code == 404
