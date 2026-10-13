from __future__ import annotations

import contextvars
import dataclasses
import json
from typing import Any, Iterable, Iterator, Optional, Protocol

from axo_endpoint.core.dataio import (
    APPEND_CHUNK,
    CHUNK_STATUS,
    FINALIZE_STREAM,
    OPEN_STREAM,
    READ,
    READ_CHUNK,
    WRITE,
    DataIOUnavailableError,
    IORef,
    get_format,
)

DEFAULT_CHUNK_BYTES = 262144


class IOChannel(Protocol):
    """Whatever transport is carrying this job (Pipe or ZMQ) implements this --
    a single blocking round trip to the endpoint and back."""

    def request(self, op: str, ref: IORef, data: Optional[bytes]) -> bytes: ...


_channel_var: "contextvars.ContextVar[Optional[IOChannel]]" = contextvars.ContextVar(
    "axo_endpoint_dataio_channel", default=None
)


def bind_channel(channel: IOChannel) -> contextvars.Token:
    """Called by the worker/container entrypoint right before invoking the
    function, so dataio.read/write below know how to reach the endpoint."""
    return _channel_var.set(channel)


def reset_channel(token: contextvars.Token) -> None:
    _channel_var.reset(token)


def _require_channel() -> IOChannel:
    channel = _channel_var.get()
    if channel is None:
        raise DataIOUnavailableError(
            "dataio.read/write called with no active job io channel bound "
            "(e.g. calling it outside a running job, or from an invocation "
            "path with no endpoint round trip available)"
        )
    return channel


def read(ref: IORef) -> Any:
    """Fetches an object from its bucket and deserializes it per ``ref.format``.
    ``ref.version`` None = the latest complete version."""
    channel = _require_channel()
    raw = channel.request(READ, ref, None)
    return get_format(ref.format).unwrap().decode(raw)


def write(obj: Any, ref: IORef) -> IORef:
    """Serializes ``obj`` per ``ref.format`` and stores it as a NEW version
    of ``ref.bucket``/``ref.name`` (versions never change once written).
    Returns the ref pinned to the version that was created."""
    channel = _require_channel()
    data = get_format(ref.format).unwrap().encode(obj)
    record = json.loads(channel.request(WRITE, ref, data).decode("utf-8"))
    return dataclasses.replace(ref, version=record["version"], chunk_index=None)


def iter_chunks(ref: IORef) -> Iterator[bytes]:
    """Reads an object chunk by chunk -- bounded memory throughout, since
    neither the endpoint nor this generator ever holds the whole thing.
    Yields raw bytes per chunk (``ref.format`` only applies to whole objects
    via read()). Pins the version first, so a newer version finishing
    mid-read can't mix chunks of two versions."""
    channel = _require_channel()
    status = json.loads(channel.request(CHUNK_STATUS, ref, None).decode("utf-8"))
    pinned = dataclasses.replace(ref, version=status["version"])
    for i in range(status["total_chunks"]):
        yield channel.request(READ_CHUNK, dataclasses.replace(pinned, chunk_index=i), None)


def write_chunks(ref: IORef, chunks: Iterable[bytes], chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> IORef:
    """Streams a function's output as a new version without knowing its total
    size upfront. ``chunks`` may be any sizes: they're re-cut here into
    ``chunk_bytes`` pieces (storage wants every chunk but the last full-size).
    The version only becomes readable once everything arrived and was
    verified. Returns the ref pinned to the created version."""
    channel = _require_channel()
    opened = json.loads(channel.request(
        OPEN_STREAM, ref, json.dumps({"chunk_bytes": chunk_bytes}).encode("utf-8"),
    ).decode("utf-8"))
    pinned = dataclasses.replace(ref, version=opened["version"], chunk_index=None)
    size = opened.get("chunk_bytes", chunk_bytes)
    buffer = bytearray()
    index = 0
    for chunk in chunks:
        buffer.extend(chunk)
        while len(buffer) >= size:
            channel.request(APPEND_CHUNK, dataclasses.replace(pinned, chunk_index=index), bytes(buffer[:size]))
            del buffer[:size]
            index += 1
    if buffer:
        channel.request(APPEND_CHUNK, dataclasses.replace(pinned, chunk_index=index), bytes(buffer))
    channel.request(FINALIZE_STREAM, pinned, None)
    return pinned
