from __future__ import annotations

from typing import Any, Dict, List, Optional

from axo_shared.axo_storage import AxoStorageClient
from axo_shared.errors import (
    AxoError,
    BucketAlreadyExistsError,
    BucketNotFoundError,
    ObjectNotCompleteError,
    ObjectNotFoundError,
    StorageAccessDeniedError,
    StorageUnavailableError,
)

from axo_vem.domain.errors import ConflictError, DomainError, NotFoundError, NotOwnerError, UpstreamTimeoutError


def _domain_error(err: AxoError) -> DomainError:
    """axo_storage's typed errors as the VEM's HTTP-mapped domain errors."""
    if isinstance(err, (BucketNotFoundError, ObjectNotFoundError)):
        return NotFoundError(err.message)
    if isinstance(err, StorageAccessDeniedError):
        return NotOwnerError(err.message)
    if isinstance(err, StorageUnavailableError):
        return UpstreamTimeoutError(err.message)
    if isinstance(err, (BucketAlreadyExistsError, ObjectNotCompleteError)):
        return ConflictError(err.message)
    return ConflictError(f"{err.name}: {err.message}")


class BucketStorage:
    """What a choreography needs from axo_storage, always as the user who
    started the run (axo_storage checks their access on every call): list
    the objects in a bucket (``BK1 → FN1`` runs FN1 once per object) and
    save a result into one (``FN1 → BK2``). The VEM keeps nothing about
    buckets itself."""

    def __init__(self, client: AxoStorageClient) -> None:
        self._client = client

    @staticmethod
    def _unwrap(result):
        if result.is_err:
            raise _domain_error(result.unwrap_err())
        return result.unwrap()

    def list_objects(self, user_id: str, bucket: str) -> List[Dict[str, Any]]:
        """Every COMPLETE object version in ``bucket``."""
        objects = self._unwrap(self._client.list(user_id, bucket))
        return [o for o in objects if o.get("state") == "complete"]

    def upload(
        self, user_id: str, bucket: str, key: str, data: bytes, format: str = "raw", version: Optional[int] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stores ``data`` as a new version (the next one when ``version`` is
        None); ``source`` = what wrote it (see engine.source_tag)."""
        return self._unwrap(self._client.upload(user_id, bucket, key, data, format=format, version=version,
                                                source=source))
