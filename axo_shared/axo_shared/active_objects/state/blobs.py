"""Blob tiers: a local directory, or a platform bucket in axo_storage
(``axo/ve-<id>-objects``, one storage object per value named by its hash)."""

from __future__ import annotations

import os
import re
import tempfile
from typing import Optional

from option import Err, Ok, Result

from axo_shared.active_objects.state.ports import ValueStore
from axo_shared.axo_storage import AxoStorageClient
from axo_shared.errors import ActiveObjectStateBackendError, AxoError, ObjectNotFoundError

_HASH = re.compile(r"^[0-9a-f]{64}$")


def objects_bucket_name(virtual_environment_id: str) -> str:
    """The hidden platform bucket holding a VE's object state blobs (next to
    its ``-functions`` code bucket)."""
    slug = re.sub(r"[^a-z0-9-]", "-", virtual_environment_id.lower()).strip("-") or "ve"
    return f"axo/ve-{slug}-objects"[:67]


class FilesystemValueStore(ValueStore):
    """``<root>/<first 2 hash chars>/<hash>``; written to a temp file and
    renamed, so a value is either whole or absent."""

    def __init__(self, root: str) -> None:
        self._root = os.path.realpath(root)
        os.makedirs(self._root, exist_ok=True)

    def _path(self, hash: str) -> Result[str, AxoError]:
        if not _HASH.match(hash):
            return Err(ActiveObjectStateBackendError(f"Not a value hash: {hash!r}", {"backend": "filesystem"}))
        return Ok(os.path.join(self._root, hash[:2], hash))

    def _io(self, what: str, exc: OSError) -> AxoError:
        return ActiveObjectStateBackendError(f"File {what} failed: {exc}", {"backend": "filesystem"})

    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        path = self._path(hash)
        if path.is_err:
            return path
        target = path.unwrap()
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".tmp-")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except OSError as exc:
            return Err(self._io("write", exc))
        return Ok(None)

    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        path = self._path(hash)
        if path.is_err:
            return path
        try:
            with open(path.unwrap(), "rb") as f:
                return Ok(f.read())
        except FileNotFoundError:
            return Ok(None)
        except OSError as exc:
            return Err(self._io("read", exc))

    def exists(self, hash: str) -> Result[bool, AxoError]:
        return self._path(hash).map(os.path.exists)

    def delete(self, hash: str) -> Result[None, AxoError]:
        path = self._path(hash)
        if path.is_err:
            return path
        try:
            os.remove(path.unwrap())
        except FileNotFoundError:
            pass
        except OSError as exc:
            return Err(self._io("delete", exc))
        return Ok(None)


class AxoStorageValueStore(ValueStore):
    """Values as objects of a platform bucket. Every value has one version
    (its name is its content). ``principal`` is who the endpoint acts as
    (``endpoint:<ve id>``); the Control Plane allows only that VE in."""

    def __init__(self, client: AxoStorageClient, principal: str, bucket: str) -> None:
        self._client = client
        self._principal = principal
        self._bucket = bucket

    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        return self._client.upload(self._principal, self._bucket, hash, data, format="raw").map(lambda _: None)

    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        got = self._client.download(self._principal, self._bucket, hash)
        if got.is_err and isinstance(got.unwrap_err(), ObjectNotFoundError):
            return Ok(None)
        return got

    def exists(self, hash: str) -> Result[bool, AxoError]:
        info = self._client.info(self._principal, self._bucket, hash)   # the latest complete version
        if info.is_ok:
            return Ok(True)
        if isinstance(info.unwrap_err(), ObjectNotFoundError):
            return Ok(False)
        return Err(info.unwrap_err())

    def delete(self, hash: str) -> Result[None, AxoError]:
        gone = self._client.delete(self._principal, self._bucket, hash)
        if gone.is_err and not isinstance(gone.unwrap_err(), ObjectNotFoundError):
            return Err(gone.unwrap_err())
        return Ok(None)
