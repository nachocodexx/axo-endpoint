from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable, Dict, Optional, Union

from option import Err, Ok, Result

from axo_shared import wire
from axo_shared.errors import (
    AxoError,
    ObjectAlreadyCompleteError,
    ObjectHashMismatchError,
    ObjectNotCompleteError,
    StorageAccessDeniedError,
    StorageChunkOutOfRangeError,
    StorageChunkSizeError,
    StorageInvalidRequestError,
    StorageQuotaExceededError,
    StorageUnauthenticatedError,
)
from axo_shared.protocol import Command, CommandResult

from axo_storage.access import READ, WRITE, AccessChecker, AccessDecision, AccessQuery
from axo_storage.auth import SERVICE, Authenticator, Caller
from axo_storage.catalog import COMPLETE, FAILED, UPLOADING, ObjectRow, StorageCatalog
from axo_storage.chunk_store import ChunkStore, chunk_path
from axo_storage.log import DumbLogger, Log
from axo_storage.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]

# A bucket key: a plain name (local dev / the Control Plane's fixed keys like
# bkt-<id>) or an "owner/bucket" name (e.g. axo/ve-<id>-functions).
_BUCKET_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?/)?[a-z0-9][a-z0-9._-]{0,62}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
MAX_CHUNK_BYTES = 64 * 1024 * 1024


class StorageService:
    """Everything axo_storage does, one method per wire operation. Every
    request is authenticated (VE token or service token) and access-checked
    for the user it acts for, on every call -- chunk requests included.

    Objects live in buckets, by (bucket, name, version). A version never
    changes once complete; writing again creates the next version, and a
    read without a version gets the latest complete one."""

    def __init__(
        self,
        catalog: StorageCatalog,
        chunks: ChunkStore,
        authenticator: Authenticator,
        access: AccessChecker,
        now_fn: Callable[[], float] = time.time,
        logger: _Logger = None,
    ) -> None:
        self._catalog = catalog
        self._chunks = chunks
        self._auth = authenticator
        self._access = access
        self._now_fn = now_fn
        self._logger: _Logger = logger or DumbLogger()
        self._handlers: Dict[str, Callable[[Caller, Command], Result[CommandResult, AxoError]]] = {
            wire.BUCKET_CREATE: self._bucket_create,
            wire.BUCKET_INFO:     self._bucket_info,
            wire.BUCKET_DELETE:   self._bucket_delete,
            wire.OBJECT_REGISTER:   self._data_register,
            wire.OBJECT_CHUNK_PUT:  self._chunk_put,
            wire.OBJECT_FINALIZE:   self._finalize,
            wire.OBJECT_CHUNK_GET:  self._chunk_get,
            wire.OBJECT_STATUS:     self._status,
            wire.OBJECT_INFO:       self._info,
            wire.OBJECT_LIST:       self._list,
            wire.OBJECT_DELETE:     self._delete,
        }

    def operations(self):
        return [wire.PING, *self._handlers]

    def handle(self, command: Command) -> CommandResult:
        """Never raises: every failure becomes CommandResult.from_error."""
        t0 = time.monotonic()
        if command.operation == wire.PING:  # health checks need no credentials
            return CommandResult(ok=True, metadata={"service": "axo_storage"})
        handler = self._handlers.get(command.operation)
        if handler is None:
            return CommandResult.from_error(StorageInvalidRequestError(
                f"unknown operation {command.operation!r}", context={"operation": command.operation},
            ))
        caller_result = self._auth.authenticate(command.envelope)
        if caller_result.is_err:
            return self._fail(command, caller_result.unwrap_err(), t0)
        caller = caller_result.unwrap()
        result = handler(caller, command)
        if result.is_err:
            return self._fail(command, result.unwrap_err(), t0, caller)
        self._logger.debug_event(
            Event.Request.HANDLED,
            component=Component.SERVICE,
            operation=command.operation,
            user_id=caller.user_id,
            ve_id=caller.virtual_environment_id,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        return result.unwrap()

    def _fail(self, command: Command, err: AxoError, t0: float, caller: Optional[Caller] = None) -> CommandResult:
        self._logger.info_event(
            Event.Request.REJECTED,
            component=Component.SERVICE,
            operation=command.operation,
            user_id=caller.user_id if caller else None,
            ve_id=caller.virtual_environment_id if caller else None,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
            **err.to_dict(),
        )
        return CommandResult.from_error(err)

    # ── access ───────────────────────────────────────────────────────────────
    def _authorize(self, caller: Caller, bucket: str, mode: str) -> Result[AccessDecision, AxoError]:
        query = AccessQuery(
            user_id=caller.user_id, bucket=bucket, mode=mode,
            virtual_environment_id=caller.virtual_environment_id,
        )
        decision = self._access.check(query)
        if decision.is_err:
            return decision
        if not decision.unwrap().allowed:
            return Err(StorageAccessDeniedError(
                decision.unwrap().reason or "access denied",
                context={"bucket": bucket, "mode": mode, "user_id": caller.user_id,
                         "ve_id": caller.virtual_environment_id},
            ))
        return decision

    def _open(self, caller: Caller, bucket: str, mode: str):
        """Access first (so a refused caller learns nothing about the bucket),
        then the bucket row under its storage key (names and aliases resolve
        to it). Ok((key, row, decision))."""
        decision = self._authorize(caller, bucket, mode)
        if decision.is_err:
            return Err(decision.unwrap_err())
        key = decision.unwrap().bucket_key or bucket
        row = self._catalog.get_bucket(key)
        if row.is_err:
            return Err(row.unwrap_err())
        return Ok((key, row.unwrap(), decision.unwrap()))

    # ── ops ──────────────────────────────────────────────────────────────────
    def _bucket_create(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        """Platform services only: buckets are created from bucket pages
        (the CP decides ownership), never by a job."""
        if caller.kind != SERVICE:
            return Err(StorageUnauthenticatedError("only a platform service can create buckets"))
        env = command.envelope
        name = env.get("name")
        if not isinstance(name, str) or not _BUCKET_RE.match(name):
            return Err(_invalid("name", "lowercase letters, digits, '.', '_' or '-', up to 63 chars"))
        quota = env.get("quota_bytes")
        if quota is not None and (not isinstance(quota, int) or quota < 0):
            return Err(_invalid("quota_bytes", "a non-negative integer"))
        created = self._catalog.create_bucket(name, caller.user_id, self._now_fn(), quota)
        if created.is_err:
            return Err(created.unwrap_err())
        self._logger.info_event(Event.Bucket.CREATED, component=Component.SERVICE, bucket=name, user_id=caller.user_id)
        return Ok(CommandResult(ok=True, metadata=created.unwrap().to_dict()))

    def _bucket_info(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        name = command.envelope.get("name")
        if not isinstance(name, str):
            return Err(_invalid("name", "a string"))
        opened = self._open(caller, name, READ)
        if opened.is_err:
            return Err(opened.unwrap_err())
        key, bucket, decision = opened.unwrap()
        return Ok(CommandResult(ok=True, metadata={
            **bucket.to_dict(),
            "quota_bytes": self._quota(decision, bucket.quota_bytes),
            "used_bytes": self._catalog.bucket_usage(key),
        }))

    def _bucket_delete(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        """Platform services only (the Control Plane, once a deleted bucket's
        grace period is over): every object and the bucket itself, for good.
        ``name`` is the storage key."""
        if caller.kind != SERVICE:
            return Err(StorageUnauthenticatedError("only a platform service can delete buckets"))
        name = command.envelope.get("name")
        if not isinstance(name, str) or not _BUCKET_RE.match(name):
            return Err(_invalid("name", "a bucket key"))
        bucket = self._catalog.get_bucket(name)
        if bucket.is_err:
            return Err(bucket.unwrap_err())
        removed = 0
        for row in self._catalog.list_objects(name, None):
            for index in self._catalog.delete_object(name, row.name, row.version):
                self._chunks.delete(chunk_path(name, row.name, row.version, index))
            removed += 1
        self._catalog.delete_bucket(name)
        self._logger.info_event(Event.Bucket.DELETED, component=Component.SERVICE, bucket=name,
                                user_id=caller.user_id, objects=removed)
        return Ok(CommandResult(ok=True, metadata={"bucket": name, "deleted_versions": removed}))

    def _data_register(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        env = command.envelope
        target = _bucket_and_name(env)
        if target.is_err:
            return Err(target.unwrap_err())
        bucket, name = target.unwrap()
        version, total_size = env.get("version"), env.get("total_size")
        chunk_bytes = env.get("chunk_bytes")
        if version is not None and (not isinstance(version, int) or version < 1):
            return Err(_invalid("version", "a positive integer, or omitted for the next version"))
        if total_size is not None and (not isinstance(total_size, int) or total_size < 0):
            return Err(_invalid("total_size", "a non-negative integer, or omitted for a streamed upload"))
        if not isinstance(chunk_bytes, int) or not 0 < chunk_bytes <= MAX_CHUNK_BYTES:
            return Err(_invalid("chunk_bytes", f"between 1 and {MAX_CHUNK_BYTES}"))

        opened = self._open(caller, bucket, WRITE)
        if opened.is_err:
            return Err(opened.unwrap_err())
        bucket, bucket_row, decision = opened.unwrap()
        quota = self._quota(decision, bucket_row.quota_bytes)
        if quota is not None and total_size is not None:
            used = self._catalog.bucket_usage(bucket)
            if used + total_size > quota:
                return Err(_quota_error(bucket, used, total_size, quota))

        now = self._now_fn()
        row = ObjectRow(
            bucket=bucket, name=name,
            version=version if version is not None else self._catalog.next_version(bucket, name),
            format=str(env.get("format") or "raw"),
            total_size=total_size, chunk_bytes=chunk_bytes,
            total_chunks=_total_chunks(total_size, chunk_bytes) if total_size is not None else None,
            declared_hash=env.get("content_hash"), content_hash=None,
            hash_algo=str(env.get("content_hash_algo") or "sha256"),
            state=UPLOADING, created_at=now, created_by=caller.user_id,
        )
        inserted = self._catalog.insert_object(row)
        if inserted.is_err:
            # A concurrent register without a version may have taken the same
            # number -- that's an ordinary conflict; the writer retries.
            return Err(inserted.unwrap_err())
        self._logger.info_event(
            Event.Object.REGISTERED, component=Component.SERVICE,
            bucket=bucket, name=name, version=row.version, total_size=total_size, user_id=caller.user_id,
        )
        if total_size == 0:
            finished = self._finish(row, {})
            if finished.is_err:
                return Err(finished.unwrap_err())
            row = finished.unwrap()
        return Ok(CommandResult(ok=True, metadata=row.to_dict()))

    def _chunk_put(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        located = self._locate(caller, command.envelope, WRITE, need_version=True)
        if located.is_err:
            return Err(located.unwrap_err())
        row, decision = located.unwrap()
        if row.state != UPLOADING:
            return Err(ObjectAlreadyCompleteError(
                f"{row.bucket}/{row.name} v{row.version} is {row.state} and can't change",
                context=_ident(row),
            ))
        index = command.envelope.get("chunk_index")
        if not isinstance(index, int) or index < 0:
            return Err(_invalid("chunk_index", "a non-negative integer"))
        data = command.payload

        if row.total_size is not None:
            if index >= (row.total_chunks or 0):
                return Err(StorageChunkOutOfRangeError(
                    f"chunk {index} is past the last chunk ({(row.total_chunks or 0) - 1})",
                    context={**_ident(row), "chunk_index": index},
                ))
            expected = _expected_len(row.total_size, row.chunk_bytes, index)
            if len(data) != expected:
                return Err(_size_error(row, index, expected, len(data)))
        else:
            if not 0 < len(data) <= row.chunk_bytes:
                return Err(_size_error(row, index, row.chunk_bytes, len(data)))
            quota = self._quota(decision, self._bucket_quota(row.bucket))
            if quota is not None:
                used = self._catalog.bucket_usage(row.bucket)
                if used + len(data) > quota:
                    return Err(_quota_error(row.bucket, used, len(data), quota))

        stored = self._chunks.put(chunk_path(row.bucket, row.name, row.version, index), data)
        if stored.is_err:
            return Err(stored.unwrap_err())
        self._catalog.record_chunk(row.bucket, row.name, row.version, index, len(data))
        sizes = self._catalog.chunk_sizes(row.bucket, row.name, row.version)

        if row.total_size is not None and len(sizes) == row.total_chunks:
            finished = self._finish(row, sizes)
            if finished.is_err:
                return Err(finished.unwrap_err())
            row = finished.unwrap()
        return Ok(CommandResult(ok=True, metadata={
            **_ident(row), "chunk_index": index, "state": row.state, "received_chunks": len(sizes),
        }))

    def _finalize(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        """Ends a streamed upload: chunks 0..n-1 must all be there, every
        one full-size except the last."""
        located = self._locate(caller, command.envelope, WRITE, need_version=True)
        if located.is_err:
            return Err(located.unwrap_err())
        row, _ = located.unwrap()
        if row.state != UPLOADING:
            return Err(ObjectAlreadyCompleteError(f"{row.bucket}/{row.name} v{row.version} is {row.state}", context=_ident(row)))
        sizes = self._catalog.chunk_sizes(row.bucket, row.name, row.version)
        if row.total_size is not None:
            missing = [i for i in range(row.total_chunks or 0) if i not in sizes]
            return Err(ObjectNotCompleteError(
                f"{len(missing)} chunks still missing", context={**_ident(row), "missing": missing[:20]},
            ))
        indices = sorted(sizes)
        if indices != list(range(len(indices))):
            gaps = sorted(set(range(indices[-1] + 1)) - set(indices)) if indices else []
            return Err(ObjectNotCompleteError("chunks are missing", context={**_ident(row), "missing": gaps[:20]}))
        short = [i for i in indices[:-1] if sizes[i] != row.chunk_bytes]
        if short:
            return Err(_size_error(row, short[0], row.chunk_bytes, sizes[short[0]]))
        finished = self._finish(row, sizes)
        if finished.is_err:
            return Err(finished.unwrap_err())
        return Ok(CommandResult(ok=True, metadata=finished.unwrap().to_dict()))

    def _chunk_get(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        located = self._locate(caller, command.envelope, READ, need_version=False)
        if located.is_err:
            return Err(located.unwrap_err())
        row, _ = located.unwrap()
        if row.state != COMPLETE:
            return Err(ObjectNotCompleteError(f"{row.bucket}/{row.name} v{row.version} is {row.state}", context=_ident(row)))
        index = command.envelope.get("chunk_index")
        if not isinstance(index, int) or not 0 <= index < (row.total_chunks or 0):
            return Err(StorageChunkOutOfRangeError(
                f"chunk {index!r} out of range", context={**_ident(row), "chunk_index": index},
            ))
        data = self._chunks.get(chunk_path(row.bucket, row.name, row.version, index))
        if data.is_err:
            return Err(data.unwrap_err())
        if data.unwrap() is None:
            return Err(ObjectNotCompleteError("chunk bytes are missing", context={**_ident(row), "chunk_index": index}))
        return Ok(CommandResult(ok=True, payload=data.unwrap(), metadata={
            **_ident(row), "chunk_index": index, "total_chunks": row.total_chunks,
        }))

    def _status(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        """Which chunks arrived -- what a resuming uploader needs."""
        located = self._locate(caller, command.envelope, READ, need_version=True)
        if located.is_err:
            return Err(located.unwrap_err())
        row, _ = located.unwrap()
        present = sorted(self._catalog.chunk_sizes(row.bucket, row.name, row.version))
        missing = (
            [i for i in range(row.total_chunks) if i not in set(present)]
            if row.total_chunks is not None else []
        )
        return Ok(CommandResult(ok=True, metadata={
            **_ident(row), "state": row.state, "total_chunks": row.total_chunks,
            "present_chunks": present, "missing_chunks": missing,
        }))

    def _info(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        located = self._locate(caller, command.envelope, READ, need_version=False)
        if located.is_err:
            return Err(located.unwrap_err())
        return Ok(CommandResult(ok=True, metadata=located.unwrap()[0].to_dict()))

    def _list(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        bucket = command.envelope.get("bucket")
        name = command.envelope.get("name")
        if not isinstance(bucket, str):
            return Err(_invalid("bucket", "a string"))
        opened = self._open(caller, bucket, READ)
        if opened.is_err:
            return Err(opened.unwrap_err())
        key = opened.unwrap()[0]
        rows = self._catalog.list_objects(key, name if isinstance(name, str) else None)
        return Ok(CommandResult(ok=True, metadata={"bucket": bucket, "objects": [r.to_dict() for r in rows]}))

    def _delete(self, caller: Caller, command: Command) -> Result[CommandResult, AxoError]:
        """One version, or every version when ``version`` is omitted."""
        env = command.envelope
        target = _bucket_and_name(env)
        if target.is_err:
            return Err(target.unwrap_err())
        bucket, name = target.unwrap()
        version = env.get("version")
        opened = self._open(caller, bucket, WRITE)
        if opened.is_err:
            return Err(opened.unwrap_err())
        bucket = opened.unwrap()[0]
        if version is None:
            versions = [r.version for r in self._catalog.list_objects(bucket, name)]
        else:
            found = self._catalog.get_object(bucket, name, version)
            if found.is_err:
                return Err(found.unwrap_err())
            versions = [version]
        if not versions:
            found = self._catalog.get_object(bucket, name, None)
            return Err(found.unwrap_err())
        for v in versions:
            for index in self._catalog.delete_object(bucket, name, v):
                self._chunks.delete(chunk_path(bucket, name, v, index))
            self._logger.info_event(
                Event.Object.DELETED, component=Component.SERVICE,
                bucket=bucket, name=name, version=v, user_id=caller.user_id,
            )
        return Ok(CommandResult(ok=True, metadata={"bucket": bucket, "name": name, "versions": versions}))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _locate(
        self, caller: Caller, env: Dict[str, Any], mode: str, need_version: bool,
    ):
        """Validates (bucket, name, version), checks access, loads the row."""
        target = _bucket_and_name(env)
        if target.is_err:
            return target
        bucket, name = target.unwrap()
        version = env.get("version")
        if version is None and need_version:
            return Err(_invalid("version", "a positive integer"))
        if version is not None and not isinstance(version, int):
            return Err(_invalid("version", "a positive integer"))
        opened = self._open(caller, bucket, mode)
        if opened.is_err:
            return Err(opened.unwrap_err())
        key, _, decision = opened.unwrap()
        row = self._catalog.get_object(key, name, version)
        if row.is_err:
            return Err(row.unwrap_err())
        return Ok((row.unwrap(), decision))

    def _finish(self, row: ObjectRow, sizes: Dict[int, int]) -> Result[ObjectRow, AxoError]:
        """Hashes the received bytes and marks the version complete -- or
        failed, when they don't match the hash the writer declared."""
        digest = hashlib.new(row.hash_algo) if row.hash_algo in hashlib.algorithms_available else hashlib.sha256()
        for index in sorted(sizes):
            data = self._chunks.get(chunk_path(row.bucket, row.name, row.version, index))
            if data.is_err:
                return Err(data.unwrap_err())
            digest.update(data.unwrap() or b"")
        content_hash = digest.hexdigest()
        total_size = sum(sizes.values())
        mismatch = row.declared_hash is not None and row.declared_hash != content_hash
        state = FAILED if mismatch else COMPLETE
        self._catalog.mark_finished(
            row.bucket, row.name, row.version, state, total_size, len(sizes), content_hash, self._now_fn(),
        )
        finished = self._catalog.get_object(row.bucket, row.name, row.version)
        if finished.is_err:
            return finished
        if mismatch:
            self._logger.warning_event(
                Event.Object.FAILED, component=Component.SERVICE, **_ident(row),
                declared_hash=row.declared_hash, content_hash=content_hash,
            )
            return Err(ObjectHashMismatchError(
                "received bytes don't match the declared content_hash",
                context={**_ident(row), "declared_hash": row.declared_hash, "content_hash": content_hash},
            ))
        self._logger.info_event(
            Event.Object.COMPLETED, component=Component.SERVICE, **_ident(row),
            total_size=total_size, total_chunks=len(sizes),
        )
        return finished

    def _bucket_quota(self, bucket: str) -> Optional[int]:
        row = self._catalog.get_bucket(bucket)
        return row.unwrap().quota_bytes if row.is_ok else None

    @staticmethod
    def _quota(decision: AccessDecision, fallback: Optional[int]) -> Optional[int]:
        return decision.quota_bytes if decision.quota_bytes is not None else fallback


def _bucket_and_name(env: Dict[str, Any]) -> Result:
    bucket, name = env.get("bucket"), env.get("name")
    if not isinstance(bucket, str) or not _BUCKET_RE.match(bucket):
        return Err(_invalid("bucket", "an existing bucket name"))
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name.split("/"):
        return Err(_invalid("name", "letters, digits, '.', '_', '-' and '/' separators"))
    return Ok((bucket, name))


def _total_chunks(total_size: int, chunk_bytes: int) -> int:
    return 0 if total_size <= 0 else -(-total_size // chunk_bytes)


def _expected_len(total_size: int, chunk_bytes: int, index: int) -> int:
    last = _total_chunks(total_size, chunk_bytes) - 1
    return total_size - index * chunk_bytes if index == last else chunk_bytes


def _ident(row: ObjectRow) -> Dict[str, Any]:
    return {"bucket": row.bucket, "name": row.name, "version": row.version}


def _invalid(field: str, expected: str) -> StorageInvalidRequestError:
    return StorageInvalidRequestError(f"{field} must be {expected}", context={"field": field})


def _size_error(row: ObjectRow, index: int, expected: int, got: int) -> StorageChunkSizeError:
    return StorageChunkSizeError(
        f"chunk {index} should be {expected} bytes, got {got}",
        context={**_ident(row), "chunk_index": index, "expected": expected, "got": got},
    )


def _quota_error(bucket: str, used: int, incoming: int, quota: int) -> StorageQuotaExceededError:
    return StorageQuotaExceededError(
        f"bucket {bucket!r} would exceed its quota ({used + incoming} > {quota} bytes)",
        context={"bucket": bucket, "used_bytes": used, "incoming_bytes": incoming, "quota_bytes": quota},
    )
