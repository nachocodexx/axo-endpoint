"""Turns attribute values into stored bytes and back.

- JSON-safe values (numbers, text, bools, None, lists, dicts with text keys)
  become JSON text; small ones go to the hot tier, bigger ones to blobs.
- ``bytes`` are stored as given, always as a blob.
- Anything else is pickled with cloudpickle, always as a blob (tuples, sets
  and custom classes keep their type that way).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Tuple

import cloudpickle
from option import Err, Ok, Result

from axo_shared.active_objects.models import AttributeEntry, ValueKind
from axo_shared.errors import ActiveObjectAttributeInvalidError, ActiveObjectStateUnreadableError, AxoError

_SCALARS = (str, int, bool, type(None))


def is_json_value(value: Any) -> bool:
    """JSON-safe and round-trips to the same Python value (no NaN/inf, which
    JSON can't hold; no tuples, which come back as lists)."""
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if type(value) is list:
        return all(is_json_value(v) for v in value)
    if type(value) is dict:
        return all(isinstance(k, str) and is_json_value(v) for k, v in value.items())
    return False


def content_hash(kind: ValueKind, data: bytes) -> str:
    h = hashlib.sha256(kind.encode())
    h.update(b":")
    h.update(data)
    return h.hexdigest()


def encode(value: Any, hot_max_bytes: int) -> Result[Tuple[AttributeEntry, bytes], AxoError]:
    """The entry and bytes for one value. ``hot_max_bytes`` (set per plan) is
    the largest JSON value kept in the hot tier; 0 sends everything to blobs."""
    if is_json_value(value):
        kind: ValueKind = "json"
        data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        tier = "hot" if len(data) <= hot_max_bytes else "blob"
    elif type(value) is bytes:
        kind, data, tier = "bytes", value, "blob"
    else:
        kind, tier = "pickle", "blob"
        try:
            data = cloudpickle.dumps(value)
        except Exception as exc:  # anything pickle can raise (TypeError, PicklingError, ...)
            return Err(ActiveObjectAttributeInvalidError(
                f"Can't save a value of type {type(value).__name__}: {exc}", {"type": type(value).__name__}))
    return Ok((AttributeEntry(tier=tier, kind=kind, size=len(data), hash=content_hash(kind, data)), data))


def decode(entry: AttributeEntry, data: bytes) -> Result[Any, AxoError]:
    try:
        if entry.kind == "json":
            return Ok(json.loads(data))
        if entry.kind == "bytes":
            return Ok(bytes(data))
        return Ok(cloudpickle.loads(data))
    except Exception as exc:  # a class the value needs may no longer exist
        return Err(ActiveObjectStateUnreadableError(
            f"Can't read a saved {entry.kind} value: {exc}", {"hash": entry.hash}))
