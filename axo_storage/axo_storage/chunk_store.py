from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional

from option import Err, Ok, Result

from axo_shared.errors import AxoError, StorageBackendFailureError
from axo_shared.storage.filesystem import FilesystemStorageBackend, FsKey


def chunk_path(bucket: str, name: str, version: int, index: int) -> str:
    """Where one chunk lives in the StorageLayer -- the one place this
    naming is defined. Chunks are never merged into one file."""
    # "owner/bucket" names keep their "/" out of the path ("~" is never in a
    # name), so a bucket can't overlap another bucket's objects.
    return f"{bucket.replace('/', '~')}/{name}/v{version}/chunk_{index:08d}"


class ChunkStore(ABC):
    """The StorageLayer as axo_storage sees it: bytes by path. Memory and
    filesystem today; S3/MinIO later is one more implementation."""

    @abstractmethod
    def put(self, path: str, data: bytes) -> Result[None, AxoError]: ...

    @abstractmethod
    def get(self, path: str) -> Result[Optional[bytes], AxoError]: ...

    @abstractmethod
    def delete(self, path: str) -> Result[None, AxoError]: ...


class MemoryChunkStore(ChunkStore):
    """Tests and local dev -- everything is lost on restart."""

    def __init__(self) -> None:
        self._data: Dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, path: str, data: bytes) -> Result[None, AxoError]:
        with self._lock:
            self._data[path] = bytes(data)
        return Ok(None)

    def get(self, path: str) -> Result[Optional[bytes], AxoError]:
        with self._lock:
            return Ok(self._data.get(path))

    def delete(self, path: str) -> Result[None, AxoError]:
        with self._lock:
            self._data.pop(path, None)
        return Ok(None)


class FilesystemChunkStore(ChunkStore):
    """Chunks as files under a root directory, via axo_shared's
    FilesystemStorageBackend (which refuses paths escaping the root)."""

    def __init__(self, root: str) -> None:
        self._backend = FilesystemStorageBackend(root)

    def put(self, path: str, data: bytes) -> Result[None, AxoError]:
        result = self._backend.put(FsKey(path=path), data)
        return Ok(None) if result.is_ok else Err(_wrap(result.unwrap_err(), path))

    def get(self, path: str) -> Result[Optional[bytes], AxoError]:
        result = self._backend.get(FsKey(path=path))
        return result if result.is_ok else Err(_wrap(result.unwrap_err(), path))

    def delete(self, path: str) -> Result[None, AxoError]:
        result = self._backend.delete(FsKey(path=path))
        return Ok(None) if result.is_ok else Err(_wrap(result.unwrap_err(), path))


def _wrap(err: AxoError, path: str) -> AxoError:
    return StorageBackendFailureError(str(err), context={"path": path, "cause": err.name})
