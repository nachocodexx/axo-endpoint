from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from option import Result

from axo_endpoint.core.errors import AxoError
from axo_endpoint.core.storage.backend import StorageKey


class FunctionRuntimeError(AxoError):
    """Base class for all FunctionRuntime failure modes."""

    code = 3000
    name = "RUNTIME_ERROR"


@dataclass(frozen=True)
class InvocationHandle:
    """Confirms that a job was accepted for execution. version/started_at
    are None on the "accepted" handle invoke() returns and on any handle
    built before a job was ever actually dispatched to a worker/container
    (e.g. a cold-start readiness timeout, or a job still queued behind a
    crashed worker) -- both are only meaningfully populated once dispatch
    has actually happened, so downstream consumers (build_completion_recorder)
    must treat them as optional."""

    job_id: str
    function_id: str
    version: Optional[int] = None
    started_at: Optional[float] = None


@dataclass(frozen=True)
class InvocationContext:
    """Information given to a running function: its job id and a folder for
    temporary files. ``warnings`` is a mutable list a running function can
    append to via warn() -- fine even though the dataclass is frozen, since
    only *assigning* a new value to a field is blocked, not mutating one
    that's already there."""

    job_id: str
    scratch_dir: str
    warnings: List[str] = field(default_factory=list)
    # Buckets this job's choreography node is connected to (FN -> bucket
    # edges): the function may write its own objects there via dataio.
    output_buckets: List[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        """Records a warning that travels back with this job's result."""
        self.warnings.append(message)


# Travels inside the params so every transport (pipe, container, JOB_FORWARD)
# carries it unchanged; the worker takes it out before calling the function.
OUTPUT_BUCKETS_PARAM = "__axo_output_buckets__"


def split_output_buckets(params: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(params without the reserved key, the output bucket names)."""
    if not isinstance(params, dict) or OUTPUT_BUCKETS_PARAM not in params:
        return params, []
    rest = {k: v for k, v in params.items() if k != OUTPUT_BUCKETS_PARAM}
    buckets = params[OUTPUT_BUCKETS_PARAM]
    return rest, [str(b) for b in buckets] if isinstance(buckets, list) else []


def build_invocation(job_id: str, scratch_dir: str, params: Dict[str, Any]) -> Tuple[InvocationContext, Dict[str, Any]]:
    """The ctx and the params a function is actually called with."""
    params, buckets = split_output_buckets(params)
    return InvocationContext(job_id=job_id, scratch_dir=scratch_dir, output_buckets=buckets), params


class FunctionRuntime(ABC):
    """Something that can run a registered function with the given parameters."""

    @abstractmethod
    def invoke(
        self, function_ref: StorageKey, job_id: str, params: Dict[str, Any]
    ) -> Result[InvocationHandle, FunctionRuntimeError]:
        """Starts running a function and returns right away, before it finishes."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Stops a specific job: kills the in-flight worker process or
        dismisses the in-flight container, or drops it if it's still only
        queued. Returns True if the job was found (and thus acted on),
        False if it's unknown to this runtime (already finished, or never
        ran here)."""
