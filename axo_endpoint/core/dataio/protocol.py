from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from typing import Any, Callable, Dict, List, Optional, Tuple

from option import Err, Ok, Result

from axo_shared.axo_storage import DEFAULT_CHUNK_BYTES, AxoStorageClient
from axo_shared.errors import AxoError

from axo_endpoint.core.dataio.cache import ChunkCache
from axo_endpoint.core.dataio.errors import (
    DataIOError,
    DataIOUnavailableError,
    IOBackendError,
    UnknownIOOpError,
)
from axo_endpoint.core.dataio.ref import IORef

READ = "read"
WRITE = "write"
# The function doesn't wait: the upload goes on in the endpoint, and the job
# only finishes once it's done (flush) -- a failure becomes a warning.
WRITE_ASYNC = "write_async"
CHUNK_STATUS = "chunk_status"
READ_CHUNK = "read_chunk"
OPEN_STREAM = "open_stream"
APPEND_CHUNK = "append_chunk"
FINALIZE_STREAM = "finalize_stream"


class DataIOResolver:
    """Answers a running function's dataio requests -- the one place either
    pump loop (pipe or ZMQ) needs to know about storage. Everything goes to
    axo_storage, acting for the user who started the job (axo_storage
    checks that user's bucket access and their role in this endpoint's VE).
    Chunks already downloaded are served from the endpoint's ChunkCache.

    A cache hit skips the download but not the access rule: the user's read
    access to that bucket is re-confirmed with axo_storage at least every
    ``access_recheck_seconds`` (the same few seconds axo_storage itself
    caches answers for), so revoked access stops a run within seconds."""

    def __init__(
        self,
        client: Optional[AxoStorageClient],
        cache: Optional[ChunkCache],
        user_for_job: Callable[[str], Optional[str]],
        access_recheck_seconds: float = 5.0,
        now_fn: Callable[[], float] = time.monotonic,
        on_io: Optional[Callable[..., None]] = None,
        virtual_environment_id: Optional[str] = None,
    ) -> None:
        self._client = client
        # Every write is tagged "job:<ve>:<job_id>" (the object's ``source``),
        # so the bucket pages can say which job -- and so which function or
        # choreography -- wrote a file.
        self._ve_id = virtual_environment_id or ""
        self._cache = cache
        self._user_for_job = user_for_job
        self._recheck_s = access_recheck_seconds
        self._now_fn = now_fn
        # (job_id, bucket, bytes_read=, bytes_written=, cache_hit=) per
        # request that moved bytes -- the job's attempt record (metering).
        self._on_io = on_io
        self._confirmed: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()
        self._uploads: Optional[ThreadPoolExecutor] = None
        self._pending: Dict[str, List[Tuple[IORef, Future]]] = {}

    def resolve(self, job_id: str, op: str, ref: IORef, data: Optional[bytes]) -> Result[bytes, DataIOError]:
        if self._client is None:
            return Err(DataIOUnavailableError(
                "this endpoint has no axo_storage configured (AXO_ENDPOINT_STORAGE_URI)", context={"op": op},
            ))
        user_id = self._user_for_job(job_id)
        if not user_id:
            return Err(DataIOUnavailableError(
                "no user is known for this job, so storage access can't be checked", context={"job_id": job_id},
            ))
        if op == WRITE_ASYNC:
            return self._write_async(job_id, user_id, ref, data)
        handler = {
            READ: self._read, WRITE: self._write, CHUNK_STATUS: self._chunk_status,
            READ_CHUNK: self._read_chunk, OPEN_STREAM: self._open_stream,
            APPEND_CHUNK: self._append_chunk, FINALIZE_STREAM: self._finalize_stream,
        }.get(op)
        if handler is None:
            return Err(UnknownIOOpError(f"unknown io op {op!r}", context={"op": op}))
        result = handler(user_id, ref, data, job_id)
        if result.is_ok and op in (WRITE, APPEND_CHUNK):
            self._report(job_id, ref.bucket, bytes_written=len(data or b""))
        if result.is_ok and op in (WRITE, FINALIZE_STREAM):
            self._report(job_id, ref.bucket, file=_written(ref, result.unwrap(), data if op == WRITE else None))
        return result

    # ── reads ────────────────────────────────────────────────────────────────
    def _info(self, user_id: str, ref: IORef) -> Result[Dict[str, Any], DataIOError]:
        """The version to read (latest complete when ref.version is None);
        refuses anything not complete. Access-checked by axo_storage."""
        info = self._client.info(user_id, ref.bucket, ref.name, ref.version)
        if info.is_err:
            return Err(_wrap(info.unwrap_err(), ref))
        record = info.unwrap()
        if record.get("state") != "complete":
            return Err(IOBackendError(
                f"{ref.bucket}/{ref.name} v{record.get('version')} is {record.get('state')}, not complete",
                context=_ctx(ref),
            ))
        self._confirm(user_id, ref.bucket)
        return Ok(record)

    def _read(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        info = self._info(user_id, ref)
        if info.is_err:
            return Err(info.unwrap_err())
        record = info.unwrap()
        parts = []
        for index in range(record["total_chunks"]):
            chunk = self._chunk(user_id, ref.bucket, ref.name, record["version"], index, job_id)
            if chunk.is_err:
                return chunk
            parts.append(chunk.unwrap())
        return Ok(b"".join(parts))

    def _chunk_status(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        info = self._info(user_id, ref)
        if info.is_err:
            return Err(info.unwrap_err())
        record = info.unwrap()
        return Ok(_json({
            "version": record["version"], "total_chunks": record["total_chunks"],
            "chunk_bytes": record["chunk_bytes"], "format": record.get("format"),
        }))

    def _read_chunk(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        if ref.version is None or ref.chunk_index is None:
            return Err(UnknownIOOpError("read_chunk needs ref.version and ref.chunk_index", context=_ctx(ref)))
        return self._chunk(user_id, ref.bucket, ref.name, ref.version, ref.chunk_index, job_id)

    def _chunk(self, user_id: str, bucket: str, name: str, version: int, index: int,
               job_id: str = "") -> Result[bytes, DataIOError]:
        chunk_id = (bucket, name, version, index)
        cached = self._cache.get(chunk_id) if self._cache is not None else None
        if cached is not None:
            if not self._recently_confirmed(user_id, bucket):
                recheck = self._info(user_id, IORef(bucket=bucket, name=name, version=version))
                if recheck.is_err:
                    return Err(recheck.unwrap_err())
            self._report(job_id, bucket, cache_hit=True)     # served here: nothing read from storage
            return Ok(cached)
        fetched = self._client.get_chunk(user_id, bucket, name, version, index)
        if fetched.is_err:
            return Err(_wrap(fetched.unwrap_err(), IORef(bucket=bucket, name=name, version=version)))
        self._confirm(user_id, bucket)
        if self._cache is not None:
            self._cache.put(chunk_id, fetched.unwrap())
        self._report(job_id, bucket, bytes_read=len(fetched.unwrap()),
                     cache_hit=False if self._cache is not None else None)
        return Ok(fetched.unwrap())

    def _report(self, job_id: str, bucket: str, **io: Any) -> None:
        if self._on_io is not None and job_id:
            self._on_io(job_id, bucket, **io)

    # ── writes ───────────────────────────────────────────────────────────────
    def _source(self, job_id: str) -> Optional[str]:
        return f"job:{self._ve_id}:{job_id}" if job_id else None

    def _write(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        """One whole object = one new version (``ref.version`` None = next)."""
        uploaded = self._client.upload(user_id, ref.bucket, ref.name, data or b"", format=ref.format,
                                       version=ref.version, source=self._source(job_id))
        if uploaded.is_err:
            return Err(_wrap(uploaded.unwrap_err(), ref))
        return Ok(_json(uploaded.unwrap()))

    def _write_async(self, job_id: str, user_id: str, ref: IORef, data: Optional[bytes]) -> Result[bytes, DataIOError]:
        def upload() -> Result[bytes, DataIOError]:
            result = self._write(user_id, ref, data, job_id)
            if result.is_ok:
                self._report(job_id, ref.bucket, bytes_written=len(data or b""),
                             file=_written(ref, result.unwrap(), data))
            return result

        with self._lock:
            if self._uploads is None:
                self._uploads = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dataio-upload")
            future = self._uploads.submit(upload)
            self._pending.setdefault(job_id, []).append((ref, future))
        return Ok(_json({"pending": True, "bucket": ref.bucket, "name": ref.name}))

    def flush(self, job_id: str, timeout_s: float = 600.0) -> List[str]:
        """Waits for the job's background writes; returns a warning per one
        that failed (or didn't finish in time). Nothing pending = at once."""
        with self._lock:
            pending = self._pending.pop(job_id, [])
        if not pending:
            return []
        done, _ = wait_futures([f for _r, f in pending], timeout=timeout_s)
        warnings: List[str] = []
        for ref, future in pending:
            if future not in done:
                warnings.append(f"writing {ref.bucket}/{ref.name} didn't finish in {timeout_s:g} s")
                continue
            try:
                result = future.result()
            except Exception as exc:  # the upload itself crashed
                warnings.append(f"writing {ref.bucket}/{ref.name} failed: {exc}")
                continue
            if result.is_err:
                warnings.append(f"writing {ref.bucket}/{ref.name} failed: {result.unwrap_err()}")
        return warnings

    def _open_stream(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        chunk_bytes = DEFAULT_CHUNK_BYTES
        if data:
            try:
                chunk_bytes = int(json.loads(data.decode("utf-8")).get("chunk_bytes", chunk_bytes))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                pass
        registered = self._client.register(
            user_id, ref.bucket, ref.name, version=ref.version, format=ref.format, chunk_bytes=chunk_bytes,
            source=self._source(job_id),
        )
        if registered.is_err:
            return Err(_wrap(registered.unwrap_err(), ref))
        record = registered.unwrap()
        return Ok(_json({"version": record["version"], "chunk_bytes": record["chunk_bytes"]}))

    def _append_chunk(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        if ref.version is None or ref.chunk_index is None:
            return Err(UnknownIOOpError("append_chunk needs ref.version and ref.chunk_index", context=_ctx(ref)))
        put = self._client.put_chunk(user_id, ref.bucket, ref.name, ref.version, ref.chunk_index, data or b"")
        if put.is_err:
            return Err(_wrap(put.unwrap_err(), ref))
        return Ok(b"")

    def _finalize_stream(self, user_id: str, ref: IORef, data: Optional[bytes], job_id: str = "") -> Result[bytes, DataIOError]:
        if ref.version is None:
            return Err(UnknownIOOpError("finalize_stream needs ref.version", context=_ctx(ref)))
        finalized = self._client.finalize(user_id, ref.bucket, ref.name, ref.version)
        if finalized.is_err:
            return Err(_wrap(finalized.unwrap_err(), ref))
        return Ok(_json(finalized.unwrap()))

    # ── access memo for cache hits ───────────────────────────────────────────
    def _confirm(self, user_id: str, bucket: str) -> None:
        with self._lock:
            self._confirmed[(user_id, bucket)] = self._now_fn()

    def _recently_confirmed(self, user_id: str, bucket: str) -> bool:
        with self._lock:
            at = self._confirmed.get((user_id, bucket))
        return at is not None and self._now_fn() - at < self._recheck_s


def _json(value: Dict[str, Any]) -> bytes:
    return json.dumps(value, default=str).encode("utf-8")


def _written(ref: IORef, answer: bytes, data: Optional[bytes]) -> Dict[str, Any]:
    """A file the job wrote, for its attempt's "Files" list."""
    try:
        record = json.loads(answer.decode("utf-8")) if answer else {}
    except (UnicodeDecodeError, ValueError):
        record = {}
    record = record if isinstance(record, dict) else {}
    size = record.get("total_size")
    return {"bucket": ref.bucket, "name": record.get("name") or ref.name, "version": record.get("version"),
            "size": int(size if size is not None else len(data or b"")), "format": record.get("format") or ref.format}


def _ctx(ref: IORef) -> Dict[str, Any]:
    return {"bucket": ref.bucket, "name": ref.name, "version": ref.version}


def _wrap(err: AxoError, ref: IORef) -> IOBackendError:
    """axo_storage's error, kept recognizable by name (e.g.
    STORAGE_ACCESS_DENIED) -- the worker only ever sees the message string."""
    return IOBackendError(f"{err.name}: {err.message}", context={**_ctx(ref), "storage_error": err.name})
