from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from axo_shared.runtime.results import KeepResultError, check_keep_result

ContainerMode = Literal["docker", "swarm"]
# How the code is called: "kwargs" = a function, parameters by name and ctx
# if it asks (the only function style); "object" = an Axo class, run through
# axo.runtime.
CallStyle = Literal["kwargs", "object"]
CALL_STYLES = ("kwargs", "object")
RetryBackoff = Literal["fixed", "exponential"]
RETRY_BACKOFFS = ("fixed", "exponential")
# A job's attempts: the platform's own failures (a runner crash, an endpoint
# lost) are tried again on their own, free, up to this many attempts in all;
# a failure of the function's code only up to ``max_attempts``.
PLATFORM_MAX_ATTEMPTS = 3
MAX_ATTEMPTS_LIMIT = 10
MAX_RETRY_DELAY_SECONDS = 3600.0


class RuntimeSpecError(ValueError):
    """A runtime spec that can't be used (bad call style, attempts, ...)."""


@dataclass(frozen=True)
class RuntimeSpec:
    """Describes how a function should be executed: as a local process or a container."""

    type: Literal["process", "container"] = "process"
    python_version: str = "3.11"
    requirements: List[str] = field(default_factory=list)
    image: Optional[str] = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    # Names of env_vars whose values are never shown again once saved (only
    # the endpoints running the function hold them; events carry None).
    secret_env: List[str] = field(default_factory=list)
    # How long an idle worker/container is kept warm before it is stopped.
    # None = the endpoint's default (AXO_ENDPOINT_WORKER_IDLE_TTL_SECONDS);
    # -1 = never (always warm, avoids cold starts).
    idle_ttl_seconds: Optional[float] = None
    max_invocations: int = 0
    memory_limit_bytes: Optional[int] = None
    cpu_limit: Optional[float] = None
    # How many workers/containers may run this function's jobs in parallel.
    # 1 (default) preserves the historical one-at-a-time behavior.
    max_concurrency: int = 1
    # How long a single job may run before it's treated as timed out
    # (distinct from a crash). 0 = unlimited, same convention as
    # max_invocations.
    max_duration_seconds: float = 0
    # Attempts in all when the function's code fails (1 = never tried
    # again), the wait before the next one and how it grows (exponential =
    # doubled each time). The platform's own failures are retried anyway
    # (PLATFORM_MAX_ATTEMPTS). A job may ask for other values.
    max_attempts: int = 1
    retry_delay_seconds: float = 1.0
    retry_backoff: RetryBackoff = "fixed"
    # How long this function's results are kept by default (a keep_results
    # choice, see results.py); None = the VE's default.
    keep_result: Optional[str] = None
    call_style: CallStyle = "kwargs"

    def __post_init__(self) -> None:
        if self.call_style not in CALL_STYLES:
            raise RuntimeSpecError(
                f"call_style {self.call_style!r} is not supported: write the function with named parameters "
                f"(def f(width: int, ctx=None)), not f(params, ctx)")
        check_attempts(self.max_attempts, self.retry_delay_seconds, self.retry_backoff)
        if self.keep_result is not None:
            try:
                check_keep_result(self.keep_result)
            except KeepResultError as exc:
                raise RuntimeSpecError(str(exc)) from exc

    @staticmethod
    def from_dict(d: Dict) -> "RuntimeSpec":
        """Deserializes a RuntimeSpec from a plain dict (e.g. from the wire envelope)."""
        memory_limit_bytes = d.get("memory_limit_bytes")
        cpu_limit = d.get("cpu_limit")
        idle_ttl_seconds = d.get("idle_ttl_seconds")
        return RuntimeSpec(
            type=d.get("type", "process"),
            python_version=d.get("python_version", "3.11"),
            requirements=list(d.get("requirements", [])),
            image=d.get("image"),
            env_vars={k: v for k, v in dict(d.get("env_vars") or {}).items() if v is not None},
            secret_env=list(d.get("secret_env") or []),
            idle_ttl_seconds=float(idle_ttl_seconds) if idle_ttl_seconds is not None else None,
            max_invocations=int(d.get("max_invocations", 0)),
            memory_limit_bytes=int(memory_limit_bytes) if memory_limit_bytes is not None else None,
            cpu_limit=float(cpu_limit) if cpu_limit is not None else None,
            max_concurrency=int(d.get("max_concurrency", 1)),
            max_duration_seconds=float(d.get("max_duration_seconds", 0)),
            max_attempts=int(d.get("max_attempts", 1)),
            retry_delay_seconds=float(d.get("retry_delay_seconds", 1.0)),
            retry_backoff=d.get("retry_backoff") or "fixed",
            keep_result=d.get("keep_result"),
            call_style=d.get("call_style") or "kwargs",
        )

    def to_dict(self) -> Dict:
        """Serializes this spec to a plain dict for envelope/storage."""
        return {
            "type": self.type,
            "python_version": self.python_version,
            "requirements": list(self.requirements),
            "image": self.image,
            "env_vars": dict(self.env_vars),
            "secret_env": list(self.secret_env),
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "max_invocations": self.max_invocations,
            "memory_limit_bytes": self.memory_limit_bytes,
            "cpu_limit": self.cpu_limit,
            "max_concurrency": self.max_concurrency,
            "max_duration_seconds": self.max_duration_seconds,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "retry_backoff": self.retry_backoff,
            "keep_result": self.keep_result,
            "call_style": self.call_style,
        }

    def public_dict(self) -> Dict[str, Any]:
        """``to_dict`` for events and read models: secret values left out (None)."""
        data = self.to_dict()
        secret = set(self.secret_env)
        data["env_vars"] = {k: (None if k in secret else v) for k, v in self.env_vars.items()}
        return data


# What a function's settings may change in place (FUNCTION_UPDATE ``runtime``).
SETTABLE_FIELDS = ("max_attempts", "retry_delay_seconds", "retry_backoff", "keep_result", "idle_ttl_seconds",
                   "max_concurrency", "cpu_limit", "memory_limit_bytes")


def check_attempts(max_attempts: int, retry_delay_seconds: float, retry_backoff: str) -> None:
    if not 1 <= int(max_attempts) <= MAX_ATTEMPTS_LIMIT:
        raise RuntimeSpecError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}")
    if not 0 <= float(retry_delay_seconds) <= MAX_RETRY_DELAY_SECONDS:
        raise RuntimeSpecError(f"retry_delay_seconds must be between 0 and {MAX_RETRY_DELAY_SECONDS:g}")
    if retry_backoff not in RETRY_BACKOFFS:
        raise RuntimeSpecError(f"retry_backoff must be one of {', '.join(RETRY_BACKOFFS)}")


def retry_delay(attempt: int, delay_seconds: float, backoff: str) -> float:
    """Seconds to wait before attempt ``attempt + 1`` after attempt
    ``attempt`` failed (attempts count from 1)."""
    if backoff == "exponential":
        return min(MAX_RETRY_DELAY_SECONDS, float(delay_seconds) * (2 ** max(0, attempt - 1)))
    return float(delay_seconds)
