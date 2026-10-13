"""Client side of axo_storage, the platform bucket service: shared by
axo_endpoint (jobs' dataio) and axo_vem / the Control Plane (bucket pages)."""

from axo_shared.axo_storage.client import (
    DEFAULT_CHUNK_BYTES,
    AxoStorageClient,
    Credentials,
    ServiceCredentials,
    VeCredentials,
    derive_ve_token,
    error_from_result,
)

__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "AxoStorageClient",
    "Credentials",
    "ServiceCredentials",
    "VeCredentials",
    "derive_ve_token",
    "error_from_result",
]
