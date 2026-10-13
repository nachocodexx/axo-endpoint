from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from axo_shared.axo_storage import AxoStorageClient
from axo_shared.errors import AxoError, BucketAlreadyExistsError

from axo_control_plane.application.buckets import CP_PRINCIPAL
from axo_control_plane.application.ports import StorageGateway
from axo_control_plane.domain.errors import ConflictError, DomainError, ForbiddenError, NotFoundError, UnavailableError


def _domain(err: AxoError) -> DomainError:
    name = getattr(err, "name", "")
    if isinstance(err, BucketAlreadyExistsError):
        return ConflictError(str(err))
    if "NOT_FOUND" in name:
        return NotFoundError(str(err))
    if "DENIED" in name or "UNAUTHENTICATED" in name:
        return ForbiddenError(str(err))
    if "QUOTA" in name or "INVALID" in name or "HASH" in name:
        return ConflictError(str(err), code=name or "CONFLICT")
    return UnavailableError(f"axo_storage: {err}")


class AxoStorageGateway(StorageGateway):
    """axo_storage as the Control Plane uses it: with the platform service
    token, acting for the person on whose behalf the CP works (after the CP
    checked that person's role). Buckets are created under their fixed
    storage key (``bkt-<id>``)."""

    def __init__(self, client: AxoStorageClient) -> None:
        self._client = client

    def _ok(self, result):
        if result.is_err:
            raise _domain(result.unwrap_err())
        return result.unwrap()

    def create_bucket(self, full_name: str, acting_user: str, quota_bytes: Optional[int]) -> None:
        self._ok(self._client.create_bucket(acting_user, full_name, quota_bytes))

    def purge_bucket(self, full_name: str) -> None:
        result = self._client.delete_bucket(CP_PRINCIPAL, full_name)
        if result.is_err and "NOT_FOUND" not in getattr(result.unwrap_err(), "name", ""):
            raise _domain(result.unwrap_err())

    def usage_bytes(self, full_name: str) -> int:
        return int(self._ok(self._client.bucket_info(CP_PRINCIPAL, full_name)).get("used_bytes", 0))

    def put_object(self, bucket_key: str, name: str, data: bytes) -> None:
        info = self._client.bucket_info(CP_PRINCIPAL, bucket_key)
        if info.is_err:
            created = self._client.create_bucket(CP_PRINCIPAL, bucket_key, None)
            if created.is_err and not isinstance(created.unwrap_err(), BucketAlreadyExistsError):
                raise _domain(created.unwrap_err())
        self._ok(self._client.upload(CP_PRINCIPAL, bucket_key, name, data))

    # ── the bucket pages (UI) -- the CP already checked the person's role ────
    def list_objects(self, acting_user: str, bucket_key: str, prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._ok(self._client.list(acting_user, bucket_key, prefix))

    def object_info(self, acting_user: str, bucket_key: str, name: str, version: Optional[int]) -> Dict[str, Any]:
        return self._ok(self._client.info(acting_user, bucket_key, name, version))

    def upload(self, acting_user: str, bucket_key: str, name: str, data: bytes) -> Dict[str, Any]:
        return self._ok(self._client.upload(acting_user, bucket_key, name, data))

    def download(self, acting_user: str, bucket_key: str, name: str, version: Optional[int]) -> Iterator[bytes]:
        for chunk in self._client.iter_chunks(acting_user, bucket_key, name, version):
            yield self._ok(chunk)

    def delete_object(self, acting_user: str, bucket_key: str, name: str, version: Optional[int]) -> Dict[str, Any]:
        return self._ok(self._client.delete(acting_user, bucket_key, name, version))
