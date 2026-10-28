from axo_shared import wire
from axo_shared.protocol import Command
from axo_storage.access import AllowAllAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import FilesystemChunkStore
from axo_storage.service import StorageService


def _service(tmp_path):
    return StorageService(
        catalog=StorageCatalog(str(tmp_path / "catalog.db")),
        chunks=FilesystemChunkStore(str(tmp_path / "data")),
        authenticator=Authenticator({}, ["svc"]),
        access=AllowAllAccessChecker(),
    )


def _call(service, op, payload=b"", **fields):
    return service.handle(Command(op, "application/json", {**fields, "auth": {"service_token": "svc"}, "user_id": "u"}, payload))


def test_disk_backend_and_catalog_survive_a_restart(tmp_path):
    first = _service(tmp_path)
    _call(first, wire.BUCKET_CREATE, name="keep")
    _call(first, wire.OBJECT_REGISTER, bucket="keep", name="a/b.txt", total_size=5, chunk_bytes=4)
    _call(first, wire.OBJECT_CHUNK_PUT, bucket="keep", name="a/b.txt", version=1, chunk_index=0, payload=b"hell")
    _call(first, wire.OBJECT_CHUNK_PUT, bucket="keep", name="a/b.txt", version=1, chunk_index=1, payload=b"o")

    second = _service(tmp_path)  # a new process: same files on disk
    info = _call(second, wire.OBJECT_INFO, bucket="keep", name="a/b.txt")
    assert info.metadata["state"] == "complete"
    chunks = [_call(second, wire.OBJECT_CHUNK_GET, bucket="keep", name="a/b.txt", version=1, chunk_index=i).payload
              for i in range(2)]
    assert b"".join(chunks) == b"hello"
    assert (tmp_path / "data" / "keep" / "a" / "b.txt" / "v1" / "chunk_00000000").exists()


def test_a_catalog_from_before_the_source_column_gets_it(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE objects (bucket TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, "
                "format TEXT NOT NULL, total_size INTEGER, chunk_bytes INTEGER NOT NULL, total_chunks INTEGER, "
                "declared_hash TEXT, content_hash TEXT, hash_algo TEXT NOT NULL, state TEXT NOT NULL, "
                "created_at REAL NOT NULL, created_by TEXT NOT NULL, completed_at REAL, "
                "PRIMARY KEY (bucket, name, version))")
    old.execute("INSERT INTO objects VALUES ('b', 'n', 1, 'raw', 1, 4, 1, NULL, 'h', 'sha256', 'complete', 1, 'u', 2)")
    old.commit()
    old.close()
    row = StorageCatalog(path).get_object("b", "n", 1).unwrap()
    assert row.source is None and row.version == 1
