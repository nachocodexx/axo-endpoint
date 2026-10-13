from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class IORef:
    """Points at one object in axo_storage: ``bucket`` + ``name`` +
    ``version``. Every object lives in a bucket.

    - ``version`` None: a read gets the latest COMPLETE version; a write
      creates the next version. Versions never change once complete.
    - ``format`` selects the (de)serializer, applied inside the worker /
      container -- the endpoint only ever moves raw bytes.
    - ``chunk_index`` is only used by the chunk-by-chunk ops.
    """

    bucket: str
    name: str
    version: Optional[int] = None
    format: str = "raw"
    chunk_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bucket": self.bucket, "name": self.name, "version": self.version,
            "format": self.format, "chunk_index": self.chunk_index,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "IORef":
        return IORef(
            bucket=d["bucket"], name=d["name"], version=d.get("version"),
            format=d.get("format", "raw"), chunk_index=d.get("chunk_index"),
        )
