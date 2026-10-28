from __future__ import annotations

import re
from typing import Optional

from axo_shared.axo_storage import AxoStorageClient
from axo_shared.errors import BucketAlreadyExistsError

from axo_vem.domain.errors import ConflictError, NotFoundError


def code_bucket_name(virtual_environment_id: str) -> str:
    """The hidden, platform-owned bucket holding this VE's function code
    (owner "axo", the platform). The Control Plane creates it with the VE;
    only this VE's VEM may use it."""
    slug = re.sub(r"[^a-z0-9-]", "-", virtual_environment_id.lower()).strip("-") or "ve"
    return f"axo/ve-{slug}-functions"[:67]


class FunctionCodeStore:
    """Keeps every registered function's code in axo_storage, so Restoring
    can re-register functions into empty endpoints after the VE starts again
    (endpoints keep their catalogs in memory). One object per function, its
    object versions = the function's versions -- both never change once
    written. The VEM acts as its own principal (``vem:<ve id>``), so the
    bucket belongs to the platform, not to any user."""

    def __init__(self, client: AxoStorageClient, virtual_environment_id: str) -> None:
        self._client = client
        self._bucket = code_bucket_name(virtual_environment_id)
        self._principal = f"vem:{virtual_environment_id}"
        self._ready = False

    @property
    def bucket(self) -> str:
        return self._bucket

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        info = self._client.bucket_info(self._principal, self._bucket)
        if info.is_ok:
            self._ready = True
            return
        # Without a Control Plane (local dev, service token) create it here.
        created = self._client.create_bucket(self._principal, self._bucket)
        if created.is_err and not isinstance(created.unwrap_err(), BucketAlreadyExistsError):
            raise ConflictError(f"function code bucket unavailable: {info.unwrap_err()}")
        self._ready = True

    def save(self, function_id: str, version: int, code: bytes) -> None:
        self._ensure_bucket()
        saved = self._client.upload(self._principal, self._bucket, function_id, code, format="raw", version=version)
        if saved.is_err:
            raise ConflictError(f"saving the code of {function_id} v{version} failed: {saved.unwrap_err()}")

    def save_display(self, function_id: str, version: int, text: bytes) -> None:
        """A pickled function's readable text (shown, never run)."""
        self._ensure_bucket()
        saved = self._client.upload(self._principal, self._bucket, f"{function_id}.display", text, format="raw",
                                    version=version)
        if saved.is_err:
            raise ConflictError(f"saving the text of {function_id} v{version} failed: {saved.unwrap_err()}")

    def load_display(self, function_id: str, version: int) -> Optional[bytes]:
        text = self._client.download(self._principal, self._bucket, f"{function_id}.display", version)
        return text.unwrap() if text.is_ok else None

    def load(self, function_id: str, version: int) -> bytes:
        code = self._client.download(self._principal, self._bucket, function_id, version)
        if code.is_err:
            raise NotFoundError(f"no saved code for {function_id} v{version}: {code.unwrap_err()}")
        return code.unwrap()

    def delete(self, function_id: str, version: Optional[int] = None) -> None:
        """One version, or all of them. Missing code is not an error."""
        self._client.delete(self._principal, self._bucket, function_id, version)
        self._client.delete(self._principal, self._bucket, f"{function_id}.display", version)
