from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections import OrderedDict
from typing import Optional, Tuple

ChunkId = Tuple[str, str, int, int]  # (bucket, name, version, chunk_index)


class ChunkCache:
    """This endpoint's temporary copy of chunks it already downloaded from
    axo_storage, so the next job using the same data doesn't download it
    again. Safe without invalidation: an object version never changes once
    complete. Least recently used chunks are dropped past ``max_bytes``.

    Ephemeral: ``directory`` None keeps chunks in memory; otherwise they're
    files under ``directory``, which is wiped on start and on close() --
    nothing survives the endpoint going down."""

    def __init__(self, max_bytes: int, directory: Optional[str] = None) -> None:
        self._max_bytes = max(0, max_bytes)
        self._dir = directory
        self._index: "OrderedDict[ChunkId, int]" = OrderedDict()
        self._memory: dict = {}
        self._size = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            os.makedirs(self._dir, exist_ok=True)

    def _path(self, chunk: ChunkId) -> str:
        digest = hashlib.sha256(repr(chunk).encode("utf-8")).hexdigest()
        return os.path.join(self._dir, digest[:2], digest)

    def get(self, chunk: ChunkId) -> Optional[bytes]:
        with self._lock:
            if chunk not in self._index:
                self.misses += 1
                return None
            self._index.move_to_end(chunk)
            self.hits += 1
            if self._dir is None:
                return self._memory[chunk]
        try:
            with open(self._path(chunk), "rb") as f:
                return f.read()
        except OSError:
            self._forget(chunk)
            return None

    def put(self, chunk: ChunkId, data: bytes) -> None:
        if len(data) > self._max_bytes:
            return
        if self._dir is not None:
            path = self._path(chunk)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp.{threading.get_ident()}"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        with self._lock:
            if chunk in self._index:
                self._size -= self._index.pop(chunk)
            self._index[chunk] = len(data)
            self._size += len(data)
            if self._dir is None:
                self._memory[chunk] = bytes(data)
            victims = []
            while self._size > self._max_bytes and self._index:
                victim, size = self._index.popitem(last=False)
                self._size -= size
                self._memory.pop(victim, None)
                victims.append(victim)
        for victim in victims:
            self._remove_file(victim)

    def _forget(self, chunk: ChunkId) -> None:
        with self._lock:
            if chunk in self._index:
                self._size -= self._index.pop(chunk)
            self._memory.pop(chunk, None)

    def _remove_file(self, chunk: ChunkId) -> None:
        if self._dir is not None:
            try:
                os.remove(self._path(chunk))
            except OSError:
                pass

    def stats(self) -> dict:
        with self._lock:
            return {"chunks": len(self._index), "bytes": self._size, "max_bytes": self._max_bytes,
                    "hits": self.hits, "misses": self.misses}

    def clear(self) -> None:
        with self._lock:
            self._index.clear()
            self._memory.clear()
            self._size = 0
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            os.makedirs(self._dir, exist_ok=True)

    def close(self) -> None:
        """Endpoint going down: drop everything, including the files."""
        self.clear()
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
