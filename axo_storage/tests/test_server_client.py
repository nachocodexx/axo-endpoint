"""End to end over ZMQ: AxoStorageClient (axo_shared) <-> StorageRouterServer."""
import threading

from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials
from axo_shared.errors import StorageAccessDeniedError, StorageUnavailableError


def test_upload_then_download_through_the_client(service_client, prod_client):
    assert service_client.create_bucket("boss", "bulk").is_ok
    data = bytes(range(256)) * 50
    record = service_client.upload("luis", "bulk", "blob.bin", data, chunk_bytes=1000).unwrap()
    assert record["state"] == "complete" and record["version"] == 1
    assert prod_client.download("luis", "bulk", "blob.bin").unwrap() == data


def test_errors_come_back_typed(service_client, prod_client):
    service_client.create_bucket("boss", "bulk")
    service_client.upload("luis", "bulk", "a", b"abc")
    err = prod_client.download("ana", "bulk", "a").unwrap_err()
    assert isinstance(err, StorageAccessDeniedError)


def test_many_threads_share_one_client(service_client):
    service_client.create_bucket("boss", "bulk")
    results, threads = [], []
    for i in range(8):
        def work(i=i):
            data = f"payload-{i}".encode() * 100
            service_client.upload("luis", "bulk", f"f{i}", data, chunk_bytes=128)
            results.append(service_client.download("luis", "bulk", f"f{i}").unwrap() == data)
        threads.append(threading.Thread(target=work))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 8


def test_unreachable_storage_is_an_error_not_a_hang():
    client = AxoStorageClient("tcp://127.0.0.1:1", ServiceCredentials("x"), timeout_seconds=0.2)
    assert isinstance(client.bucket_info("luis", "bulk").unwrap_err(), StorageUnavailableError)
    client.close()
