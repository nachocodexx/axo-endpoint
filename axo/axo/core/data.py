"""``DataRef``: a bucket file handed to a step, read only when asked.

A choreography passes a bucket file to the next step as the ``data``
parameter (``{"__axo_data__": {bucket, name, version, format}}``); the
platform turns it into a ``DataRef`` before the function sees it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from axo.errors import AxoStoreUnavailableError

DATA_REF_KEY = "__axo_data__"


@dataclass(frozen=True)
class DataRef:
    bucket: str
    name: str
    version: Optional[int] = None
    format: str = "raw"
    size: Optional[int] = None

    @classmethod
    def from_value(cls, value: Any) -> Optional["DataRef"]:
        """A DataRef if ``value`` is one (wrapped or a plain {bucket, name, ...}), else None."""
        if isinstance(value, dict) and isinstance(value.get(DATA_REF_KEY), dict) and len(value) == 1:
            value = value[DATA_REF_KEY]
        elif not (isinstance(value, dict) and {"bucket", "name"} <= set(value) and "store" not in value):
            return None
        return cls(bucket=str(value["bucket"]), name=str(value["name"]), version=value.get("version"),
                   format=str(value.get("format") or "raw"), size=value.get("size"))

    def _io_ref(self):
        try:
            from axo_endpoint.core.dataio import IORef
        except ImportError as exc:
            raise AxoStoreUnavailableError("Bucket files can only be read while running on the platform") from exc
        return IORef(bucket=self.bucket, name=self.name, version=self.version, format=self.format)

    def read(self) -> Any:
        """The file's content, decoded by its format (raw = bytes)."""
        from axo_endpoint import dataio
        return dataio.read(self._io_ref())

    def iter_chunks(self) -> Iterator[bytes]:
        from axo_endpoint import dataio
        return dataio.iter_chunks(self._io_ref())

    def to_dict(self) -> Dict[str, Any]:
        return {DATA_REF_KEY: {"bucket": self.bucket, "name": self.name, "version": self.version,
                               "format": self.format, "size": self.size}}


def wrap_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Every ``{"__axo_data__": ...}`` parameter (or list of them: a step
    getting all of a bucket's files at once) becomes a DataRef."""
    def is_ref(value: Any) -> bool:
        return isinstance(value, dict) and len(value) == 1 and DATA_REF_KEY in value

    out = {}
    for key, value in (params or {}).items():
        if is_ref(value):
            out[key] = DataRef.from_value(value)
        elif isinstance(value, list) and value and all(is_ref(v) for v in value):
            out[key] = [DataRef.from_value(v) for v in value]
        else:
            out[key] = value
    return out
