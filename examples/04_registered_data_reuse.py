"""Upload an object to a bucket ONCE, then run the same function many times
against it -- all in one process: an in-process axo_storage, plus the
endpoint-side DataIOResolver a worker's dataio requests go through.

    python examples/04_registered_data_reuse.py
"""
import pickle
import time as T

import cloudpickle

from axo_endpoint.core.dataio import ChunkCache, DataIOResolver, IORef
from axo_endpoint.core.events import InMemoryEventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.storage import InMemoryStorageBackend
from axo_endpoint.service.runtime.process_runtime import ProcessFunctionRuntime
from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, VeCredentials
from axo_storage.access import AllowAllAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import MemoryChunkStore
from axo_storage.server import StorageRouterServer
from axo_storage.service import StorageService

USER = "local"

# axo_storage: where every bucket lives (in memory here).
storage = StorageRouterServer("tcp://127.0.0.1:0", StorageService(
    catalog=StorageCatalog(":memory:"), chunks=MemoryChunkStore(),
    authenticator=Authenticator({"dev-ve": "dev-ve-token"}, ["dev-vem-token"]),
    access=AllowAllAccessChecker(),
))


def lookup_price(params, ctx):
    # Runs inside the forked worker, where dataio is bound to this job.
    from axo_endpoint import dataio

    prices = dataio.read(IORef(bucket="shop", name="prices.pkl", format="pickle"))  # latest version
    return prices.get(params["item"])


def main():
    storage.start()

    # Upload the data ONCE (what the bucket page does, as a platform service).
    uploader = AxoStorageClient(storage.bind_address, ServiceCredentials("dev-vem-token"))
    uploader.create_bucket(USER, "shop").unwrap()
    record = uploader.upload(USER, "shop", "prices.pkl", pickle.dumps({"apple": 1.50, "banana": 0.75}), format="pickle")
    print("uploaded:", record.unwrap())

    # The endpoint side: this VE's credentials, acting for each job's user.
    resolver = DataIOResolver(
        client=AxoStorageClient(storage.bind_address, VeCredentials("dev-ve", "dev-ve-token")),
        cache=ChunkCache(64 * 1024 * 1024),
        user_for_job=lambda job_id: USER,
    )
    results = {}
    runtime = ProcessFunctionRuntime(
        function_registry=(registry := FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=InMemoryEventBus())),
        on_complete=lambda handle, result, warnings=None: results.__setitem__(handle.job_id, result),
        scratch_root="/tmp/scratch",
        dataio=resolver,
    )
    key = registry.register("lookup_price", 0, cloudpickle.dumps(lookup_price), T.time()).unwrap()

    # The SAME function, many times, against the SAME data -- after the first
    # job, the chunks come from the endpoint's cache.
    items = ["apple", "banana", "durian"]
    for item in items:
        runtime.invoke(key, f"job-{item}", {"item": item})
    while len(results) < len(items):
        T.sleep(0.1)
    for item in items:
        print(f"{item}: {results[f'job-{item}']}")
    print("cache:", resolver._cache.stats())
    storage.stop()


if __name__ == "__main__":
    main()
