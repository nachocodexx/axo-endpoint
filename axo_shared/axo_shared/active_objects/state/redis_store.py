"""The hot tier in the VE's Redis (``ve-<id>-redis``).

Takes a redis-py client (or fakeredis'); axo_shared doesn't depend on redis
itself."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, TypeVar

from option import Err, Ok, Result

from axo_shared.active_objects.state.ports import ValueStore
from axo_shared.errors import ActiveObjectStateBackendError, AxoError

T = TypeVar("T")


class RedisValueStore(ValueStore):
    def __init__(self, client: Any, prefix: str = "axo:state:") -> None:
        self._r = client
        self._prefix = prefix

    def _key(self, hash: str) -> str:
        return self._prefix + hash

    def _call(self, what: str, fn: Callable[[], T]) -> Result[T, AxoError]:
        try:
            return Ok(fn())
        except Exception as exc:  # redis-py's ConnectionError, TimeoutError, ...
            return Err(ActiveObjectStateBackendError(f"Redis {what} failed: {exc}", {"backend": "redis"}))

    def put(self, hash: str, data: bytes) -> Result[None, AxoError]:
        return self._call("write", lambda: self._r.set(self._key(hash), bytes(data))).map(lambda _: None)

    def get(self, hash: str) -> Result[Optional[bytes], AxoError]:
        return self._call("read", lambda: self._r.get(self._key(hash)))

    def get_many(self, hashes: Iterable[str]) -> Result[Dict[str, Optional[bytes]], AxoError]:
        ids = list(hashes)
        if not ids:
            return Ok({})
        values = self._call("read", lambda: self._r.mget([self._key(h) for h in ids]))
        return values.map(lambda vs: dict(zip(ids, vs)))

    def exists(self, hash: str) -> Result[bool, AxoError]:
        return self._call("read", lambda: bool(self._r.exists(self._key(hash))))

    def delete(self, hash: str) -> Result[None, AxoError]:
        return self._call("delete", lambda: self._r.delete(self._key(hash))).map(lambda _: None)
