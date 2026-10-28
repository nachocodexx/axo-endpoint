"""What a running function or method gets to know about its job.

On the platform the endpoint passes its own context object; anything with
``job_id``, ``scratch_dir`` and ``warn()`` fits ``Context``. Locally (calling
a function or object directly) a ``LocalContext`` stands in."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, runtime_checkable

from axo.errors import AxoStoreUnavailableError


@runtime_checkable
class Context(Protocol):
    job_id: str
    scratch_dir: str

    def warn(self, message: str) -> None:
        ...


@dataclass
class LocalContext:
    job_id: str = "local"
    scratch_dir: str = field(default_factory=tempfile.gettempdir)
    # The input that started this call in a choreography (a step's output, a
    # new bucket item); None for a direct call.
    event: Any = None
    output_buckets: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class Store:
    """The buckets a function or object is connected to (its choreography
    edges), read and written through the endpoint. ``bucket`` defaults to
    the first connected one."""

    def __init__(self, ctx: Optional[Any]) -> None:
        self._ctx = ctx

    @property
    def buckets(self) -> List[str]:
        return list(getattr(self._ctx, "output_buckets", None) or [])

    def _ref(self, name: str, bucket: Optional[str], format: str, version: Optional[int] = None):
        try:
            from axo_endpoint.core.dataio import IORef
        except ImportError as exc:
            raise AxoStoreUnavailableError("Buckets can only be used while running on the platform") from exc
        target = bucket or (self.buckets[0] if self.buckets else None)
        if target is None:
            raise AxoStoreUnavailableError("No bucket given and none is connected to this step")
        return IORef(bucket=target, name=name, format=format, version=version)

    def read(self, name: str, bucket: Optional[str] = None, version: Optional[int] = None,
             format: str = "pickle") -> Any:
        from axo_endpoint import dataio
        return dataio.read(self._ref(name, bucket, format, version))

    def write(self, name: str, value: Any, bucket: Optional[str] = None, format: str = "pickle",
              wait: bool = True) -> Any:
        """Stores ``value`` as a new version; returns the reference written.
        ``wait=False`` returns None at once: the endpoint uploads it while the
        code goes on, the job finishes only after the upload, and a failed
        upload shows as a warning on the job instead of failing it."""
        from axo_endpoint import dataio
        if not wait:
            dataio.write_async(value, self._ref(name, bucket, format))
            return None
        return dataio.write(value, self._ref(name, bucket, format))

    def put(self, name: str, value: Any, bucket: Optional[str] = None, format: str = "pickle") -> None:
        """``write(..., wait=False)``."""
        self.write(name, value, bucket, format, wait=False)
