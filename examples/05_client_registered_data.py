"""Run against live services, e.g.:
    (cd axo_storage && poetry run python -m axo_storage)   # uses axo_storage/.env
    ./axo_endpoint.sh --env-file .env.dev                   # VE "dev-ve", storage on :7000
Then:  python examples/05_client_registered_data.py
"""
import pickle

from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials
from axo_shared.client import AxoEndpointClient

ENDPOINT = "tcp://localhost:5555"
STORAGE = "tcp://localhost:7000"
USER_ID = "local"
VIRTUAL_ENVIRONMENT_ID = "dev-ve"


def lookup_price(item: str):
    """The price of one item, read from the shop bucket."""
    from axo_endpoint import dataio
    from axo_endpoint.core.dataio import IORef

    prices = dataio.read(IORef(bucket="shop", name="prices.pkl", format="pickle"))
    return prices.get(item)


def main():
    # Data goes to axo_storage (never to an endpoint), uploaded ONCE.
    storage = AxoStorageClient(STORAGE, ServiceCredentials("dev-vem-token"))
    storage.create_bucket(USER_ID, "shop")  # fine if it already exists
    catalog = {"apple": 1.50, "banana": 0.75, "cherry": 4.20}
    print("uploaded:", storage.upload(USER_ID, "shop", "prices.pkl", pickle.dumps(catalog), format="pickle"))

    with AxoEndpointClient(ENDPOINT) as client:
        version = client.register_function(USER_ID, VIRTUAL_ENVIRONMENT_ID, "lookup_price", fn=lookup_price).metadata["version"]
        # The SAME function, many times, against that SAME data.
        for item in ["apple", "banana", "cherry", "durian"]:
            result = client.run(USER_ID, VIRTUAL_ENVIRONMENT_ID, "lookup_price", version, {"item": item})
            print("Result for", item, "->", result)


if __name__ == "__main__":
    main()
