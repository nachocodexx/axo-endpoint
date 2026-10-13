from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Type, Union

import zmq
from option import Err, Ok, Result

from axo_shared import errors as shared_errors
from axo_shared import wire
from axo_shared.errors import AxoError, StorageUnavailableError
from axo_shared.protocol import Command, CommandResult

DEFAULT_CHUNK_BYTES = 262144


@dataclass(frozen=True)
class VeCredentials:
    """An endpoint acting for a job: proves which VE it belongs to."""

    virtual_environment_id: str
    token: str

    def to_auth(self) -> Dict[str, Any]:
        return {"ve_id": self.virtual_environment_id, "token": self.token}


@dataclass(frozen=True)
class ServiceCredentials:
    """A platform service (the VEM now, the Control Plane later) acting for a
    logged-in person -- it already authenticated them."""

    token: str

    def to_auth(self) -> Dict[str, Any]:
        return {"service_token": self.token}


Credentials = Union[VeCredentials, ServiceCredentials]


def derive_ve_token(secret: str, virtual_environment_id: str) -> str:
    """A VE's axo_storage token derived from a secret shared by axo_storage
    and whoever launches endpoints -- so a new VE needs no config change.
    Interim: the Control Plane issues tokens from phase 5 on."""
    return hmac.new(secret.encode("utf-8"), virtual_environment_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _error_classes() -> Dict[int, Type[AxoError]]:
    """Every 93xx storage error, by code -- so a reply's error_code turns
    back into the same typed error the server raised."""
    classes: Dict[int, Type[AxoError]] = {}
    for value in vars(shared_errors).values():
        if isinstance(value, type) and issubclass(value, AxoError) and 9300 <= value.code < 9400:
            classes[value.code] = value
    return classes


_ERRORS_BY_CODE = _error_classes()


def error_from_result(result: CommandResult) -> AxoError:
    """A failed reply as a typed error. Over the wire the code and name ride
    in the metadata (wire.encode_command_result), so look there too."""
    context = dict(result.metadata or {})
    code = result.error_code or int(context.pop("error_code", 0) or 0)
    name = result.error_name or str(context.pop("error_name", "") or "")
    error_class = _ERRORS_BY_CODE.get(code)
    if error_class is not None:
        return error_class(result.error, context=context)
    err = AxoError(result.error, context=context)
    err.code, err.name = code, name or "AXO_ERROR"
    return err


class AxoStorageClient:
    """Talks to axo_storage over ZMQ. Thread-safe: each thread gets its own
    DEALER socket, and a socket that timed out is thrown away so a late reply
    can never be mistaken for the answer to the next request.

    Every call names the user it acts for -- axo_storage checks that user's
    access to the bucket on every request (and, for VE credentials, that the
    user is a Developer or higher in that VE)."""

    def __init__(self, address: str, credentials: Credentials, timeout_seconds: float = 30.0) -> None:
        self._address = address
        self._credentials = credentials
        self._timeout_ms = int(timeout_seconds * 1000)
        self._local = threading.local()
        self._sockets: List["zmq.Socket"] = []
        self._sockets_lock = threading.Lock()

    @property
    def address(self) -> str:
        return self._address

    def close(self) -> None:
        with self._sockets_lock:
            for sock in self._sockets:
                sock.close(linger=0)
            self._sockets.clear()

    # ── transport ────────────────────────────────────────────────────────────
    def _socket(self) -> "zmq.Socket":
        sock = getattr(self._local, "sock", None)
        if sock is None:
            sock = zmq.Context.instance().socket(zmq.DEALER)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(self._address)
            self._local.sock = sock
            with self._sockets_lock:
                self._sockets.append(sock)
        return sock

    def _drop_socket(self) -> None:
        sock = getattr(self._local, "sock", None)
        self._local.sock = None
        if sock is not None:
            with self._sockets_lock:
                if sock in self._sockets:
                    self._sockets.remove(sock)
            sock.close(linger=0)

    def call(
        self, operation: str, user_id: str, fields: Dict[str, Any], payload: bytes = b"",
    ) -> Result[CommandResult, AxoError]:
        """Sends one command; Ok only for a successful reply."""
        envelope = {**fields, "auth": self._credentials.to_auth(), "user_id": user_id}
        command = Command(
            operation=operation,
            content_type="application/octet-stream" if payload else "application/json",
            envelope=envelope,
            payload=payload,
        )
        sock = self._socket()
        try:
            sock.send_multipart(wire.encode_command(command))
            if not sock.poll(self._timeout_ms, zmq.POLLIN):
                self._drop_socket()
                return Err(StorageUnavailableError(
                    f"axo_storage at {self._address} did not answer in time",
                    context={"operation": operation},
                ))
            frames = sock.recv_multipart()
        except zmq.ZMQError as exc:
            self._drop_socket()
            return Err(StorageUnavailableError(str(exc), context={"operation": operation}))
        decoded = wire.decode_command_result(frames)
        if decoded.is_err:
            return Err(decoded.unwrap_err())
        result = decoded.unwrap()
        if not result.ok:
            return Err(error_from_result(result))
        return Ok(result)

    def _meta(self, operation: str, user_id: str, fields: Dict[str, Any], payload: bytes = b"") -> Result[Dict[str, Any], AxoError]:
        result = self.call(operation, user_id, fields, payload)
        return Ok(dict(result.unwrap().metadata or {})) if result.is_ok else Err(result.unwrap_err())

    # ── buckets ──────────────────────────────────────────────────────────────
    def create_bucket(self, user_id: str, name: str, quota_bytes: Optional[int] = None) -> Result[Dict[str, Any], AxoError]:
        return self._meta(wire.BUCKET_CREATE, user_id, {"name": name, "quota_bytes": quota_bytes})

    def bucket_info(self, user_id: str, name: str) -> Result[Dict[str, Any], AxoError]:
        return self._meta(wire.BUCKET_INFO, user_id, {"name": name})

    def delete_bucket(self, user_id: str, name: str) -> Result[Dict[str, Any], AxoError]:
        """Platform services only: every object of the bucket, then the bucket."""
        return self._meta(wire.BUCKET_DELETE, user_id, {"name": name})

    # ── objects ─────────────────────────────────────────────────────────────
    def register(
        self,
        user_id: str,
        bucket: str,
        name: str,
        version: Optional[int] = None,
        format: str = "raw",
        total_size: Optional[int] = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        content_hash: Optional[str] = None,
    ) -> Result[Dict[str, Any], AxoError]:
        """Starts a new object version (next one when ``version`` is None).
        ``total_size`` None = streamed: put chunks in order, then finalize()."""
        return self._meta(wire.OBJECT_REGISTER, user_id, {
            "bucket": bucket, "name": name, "version": version, "format": format,
            "total_size": total_size, "chunk_bytes": chunk_bytes, "content_hash": content_hash,
        })

    def put_chunk(
        self, user_id: str, bucket: str, name: str, version: int, chunk_index: int, data: bytes,
    ) -> Result[Dict[str, Any], AxoError]:
        return self._meta(wire.OBJECT_CHUNK_PUT, user_id, {
            "bucket": bucket, "name": name, "version": version, "chunk_index": chunk_index,
        }, payload=data)

    def finalize(self, user_id: str, bucket: str, name: str, version: int) -> Result[Dict[str, Any], AxoError]:
        return self._meta(wire.OBJECT_FINALIZE, user_id, {"bucket": bucket, "name": name, "version": version})

    def get_chunk(
        self, user_id: str, bucket: str, name: str, version: int, chunk_index: int,
    ) -> Result[bytes, AxoError]:
        result = self.call(wire.OBJECT_CHUNK_GET, user_id, {
            "bucket": bucket, "name": name, "version": version, "chunk_index": chunk_index,
        })
        return Ok(result.unwrap().payload) if result.is_ok else Err(result.unwrap_err())

    def status(self, user_id: str, bucket: str, name: str, version: int) -> Result[Dict[str, Any], AxoError]:
        return self._meta(wire.OBJECT_STATUS, user_id, {"bucket": bucket, "name": name, "version": version})

    def info(self, user_id: str, bucket: str, name: str, version: Optional[int] = None) -> Result[Dict[str, Any], AxoError]:
        """One object version's record; ``version`` None = latest complete."""
        return self._meta(wire.OBJECT_INFO, user_id, {"bucket": bucket, "name": name, "version": version})

    def list(self, user_id: str, bucket: str, name: Optional[str] = None) -> Result[List[Dict[str, Any]], AxoError]:
        result = self._meta(wire.OBJECT_LIST, user_id, {"bucket": bucket, "name": name})
        return Ok(result.unwrap().get("objects", [])) if result.is_ok else Err(result.unwrap_err())

    def delete(self, user_id: str, bucket: str, name: str, version: Optional[int] = None) -> Result[Dict[str, Any], AxoError]:
        """Deletes one version, or every version when ``version`` is None."""
        return self._meta(wire.OBJECT_DELETE, user_id, {"bucket": bucket, "name": name, "version": version})

    # ── conveniences ─────────────────────────────────────────────────────────
    def upload(
        self,
        user_id: str,
        bucket: str,
        name: str,
        data: bytes,
        format: str = "raw",
        version: Optional[int] = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    ) -> Result[Dict[str, Any], AxoError]:
        """Registers and uploads ``data`` in one go; returns the final record."""
        registered = self.register(
            user_id, bucket, name, version=version, format=format, total_size=len(data),
            chunk_bytes=chunk_bytes, content_hash=hashlib.sha256(data).hexdigest(),
        )
        if registered.is_err:
            return registered
        record = registered.unwrap()
        for index in range(record.get("total_chunks") or 0):
            put = self.put_chunk(
                user_id, bucket, name, record["version"], index,
                data[index * chunk_bytes:(index + 1) * chunk_bytes],
            )
            if put.is_err:
                return put
        return self.info(user_id, bucket, name, record["version"])

    def iter_chunks(
        self, user_id: str, bucket: str, name: str, version: Optional[int] = None,
    ) -> Iterator[Result[bytes, AxoError]]:
        """Yields each chunk of a complete version (latest when None), in order."""
        info = self.info(user_id, bucket, name, version)
        if info.is_err:
            yield Err(info.unwrap_err())
            return
        record = info.unwrap()
        for index in range(record["total_chunks"]):
            yield self.get_chunk(user_id, bucket, name, record["version"], index)

    def download(
        self, user_id: str, bucket: str, name: str, version: Optional[int] = None,
    ) -> Result[bytes, AxoError]:
        parts: List[bytes] = []
        for chunk in self.iter_chunks(user_id, bucket, name, version):
            if chunk.is_err:
                return Err(chunk.unwrap_err())
            parts.append(chunk.unwrap())
        return Ok(b"".join(parts))
