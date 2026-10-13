from __future__ import annotations

import logging
import os
from typing import List, Optional


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _level(name: str, default: str) -> int:
    return getattr(logging, os.environ.get(name, default).upper(), getattr(logging, default))


def _indent(name: str) -> Optional[int]:
    raw = os.environ.get(name, "0")
    val = int(raw) if raw.isdigit() else 0
    return val if val > 0 else None


class Config:
    """Holds all AXO_ENDPOINT_* settings. The single place that reads os.environ."""

    def __init__(self) -> None:
        # Identity
        self.AXO_ENDPOINT_ID: str = os.environ.get("AXO_ENDPOINT_ID", "axo-endpoint-0")

        # Transport
        self.AXO_ENDPOINT_ROUTER_BIND: str = os.environ.get(
            "AXO_ENDPOINT_ROUTER_BIND", "tcp://0.0.0.0:5555"
        )
        self.AXO_ENDPOINT_PUB_BIND: str = os.environ.get(
            "AXO_ENDPOINT_PUB_BIND", "tcp://0.0.0.0:5556"
        )
        self.AXO_ENDPOINT_SUB_CONNECT: List[str] = [
            s for s in os.environ.get("AXO_ENDPOINT_SUB_CONNECT", "").split(",") if s
        ]

        # Heartbeat
        self.AXO_ENDPOINT_HEARTBEAT_INTERVAL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_HEARTBEAT_INTERVAL_SECONDS", "5.0")
        )
        self.AXO_ENDPOINT_HEARTBEAT_TTL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_HEARTBEAT_TTL_SECONDS", "30.0")
        )

        # Queue / dispatcher
        self.AXO_ENDPOINT_QUEUE_MAX_DEPTH: int = _int("AXO_ENDPOINT_QUEUE_MAX_DEPTH", 1000)
        self.AXO_ENDPOINT_QUEUE_WORKERS: int = _int("AXO_ENDPOINT_QUEUE_WORKERS", 4)
        # Default max_wait for a job that doesn't set its own: how long it may
        # wait for a free runner. -1 = forever, 0 = don't wait, N = seconds.
        # (The VEM passes the VE's own default in each job's envelope.)
        self.AXO_ENDPOINT_DEFAULT_MAX_WAIT_SECONDS: float = _float("AXO_ENDPOINT_DEFAULT_MAX_WAIT_SECONDS", -1.0)

        # Process runtime: idle/recycle
        self.AXO_ENDPOINT_WORKER_IDLE_TTL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_WORKER_IDLE_TTL_SECONDS", "300.0")
        )
        self.AXO_ENDPOINT_WORKER_GC_INTERVAL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_WORKER_GC_INTERVAL_SECONDS", "30.0")
        )
        self.AXO_ENDPOINT_WORKER_MAX_INVOCATIONS: int = _int(
            "AXO_ENDPOINT_WORKER_MAX_INVOCATIONS", 0
        )  # 0 = unlimited

        # Process runtime: rlimits
        self.AXO_ENDPOINT_WORKER_RLIMIT_AS_BYTES: int = _int(
            "AXO_ENDPOINT_WORKER_RLIMIT_AS_BYTES", 512 * 1024 * 1024
        )
        self.AXO_ENDPOINT_WORKER_RLIMIT_CPU_SECONDS: int = _int(
            "AXO_ENDPOINT_WORKER_RLIMIT_CPU_SECONDS", 30
        )

        # Local testing mode — peer addresses use localhost instead of peer_id as host
        self.AXO_ENDPOINT_LOCAL_MODE: bool = _bool("AXO_ENDPOINT_LOCAL_MODE", "false")

        # Consensus / replication
        self.AXO_ENDPOINT_CONSENSUS_REPLICATION_IDLE_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_CONSENSUS_REPLICATION_IDLE_SECONDS", "2.0")
        )
        self.AXO_ENDPOINT_CONSENSUS_REPLICATION_MAX_DIRTY: int = _int(
            "AXO_ENDPOINT_CONSENSUS_REPLICATION_MAX_DIRTY", 50
        )
        self.AXO_ENDPOINT_CONSENSUS_FORWARD_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_CONSENSUS_FORWARD_TIMEOUT_SECONDS", "3.0")
        )

        # Activity tracking
        self.AXO_ENDPOINT_ACTIVITY_LOG_MAX_ENTRIES: int = _int(
            "AXO_ENDPOINT_ACTIVITY_LOG_MAX_ENTRIES", 10000
        )

        # Scratch directories
        self.AXO_ENDPOINT_SCRATCH_ROOT: str = os.environ.get(
            "AXO_ENDPOINT_SCRATCH_ROOT", "/tmp/axo_endpoint/scratch"
        )
        self.AXO_ENDPOINT_SCRATCH_GC_INTERVAL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_SCRATCH_GC_INTERVAL_SECONDS", "60.0")
        )

        # Data IO -- all data lives in axo_storage; this endpoint only caches.
        # Unset STORAGE_URI = jobs' dataio calls fail with DATAIO_UNAVAILABLE.
        self.AXO_ENDPOINT_STORAGE_URI: str = os.environ.get("AXO_ENDPOINT_STORAGE_URI", "")
        # This VE's token for axo_storage (issued by the Control Plane later).
        self.AXO_ENDPOINT_STORAGE_TOKEN: str = os.environ.get("AXO_ENDPOINT_STORAGE_TOKEN", "")
        self.AXO_ENDPOINT_STORAGE_TIMEOUT_SECONDS: float = _float("AXO_ENDPOINT_STORAGE_TIMEOUT_SECONDS", 30.0)
        # A cached chunk is only served if the user's access was confirmed
        # with axo_storage this recently (same idea as axo_storage's own cache).
        self.AXO_ENDPOINT_STORAGE_ACCESS_RECHECK_SECONDS: float = _float(
            "AXO_ENDPOINT_STORAGE_ACCESS_RECHECK_SECONDS", 5.0
        )
        # Ephemeral chunk cache: least recently used chunks dropped past this
        # size; empty DIR = in memory. Wiped on start and stop.
        self.AXO_ENDPOINT_CHUNK_CACHE_MAX_BYTES: int = _int("AXO_ENDPOINT_CHUNK_CACHE_MAX_BYTES", 1024 * 1024 * 1024)
        self.AXO_ENDPOINT_CHUNK_CACHE_DIR: str = os.environ.get("AXO_ENDPOINT_CHUNK_CACHE_DIR", "")
        self.AXO_ENDPOINT_DATAIO_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_DATAIO_TIMEOUT_SECONDS", "30.0")
        )

        # Logging
        self.AXO_ENDPOINT_LOG_LEVEL: int = _level("AXO_ENDPOINT_LOG_LEVEL", "DEBUG")
        self.AXO_ENDPOINT_LOG_DISABLED: bool = _bool("AXO_ENDPOINT_LOG_DISABLED", "false")
        self.AXO_ENDPOINT_LOG_TO_FILE: bool = _bool("AXO_ENDPOINT_LOG_TO_FILE", "false")
        self.AXO_ENDPOINT_LOG_PATH: str = os.environ.get(
            "AXO_ENDPOINT_LOG_PATH", ".axo_endpoint/log"
        )
        self.AXO_ENDPOINT_LOG_FILENAME: str = os.environ.get(
            "AXO_ENDPOINT_LOG_FILENAME", "axo_endpoint"
        )
        self.AXO_ENDPOINT_LOG_OUTPUT_PATH: Optional[str] = os.environ.get(
            "AXO_ENDPOINT_LOG_OUTPUT_PATH"
        )
        self.AXO_ENDPOINT_LOG_ERROR_OUTPUT_PATH: Optional[str] = os.environ.get(
            "AXO_ENDPOINT_LOG_ERROR_OUTPUT_PATH"
        )
        self.AXO_ENDPOINT_LOG_CONSOLE_LEVEL: int = _level(
            "AXO_ENDPOINT_LOG_CONSOLE_LEVEL", "DEBUG"
        )
        self.AXO_ENDPOINT_LOG_FILE_LEVEL: int = _level("AXO_ENDPOINT_LOG_FILE_LEVEL", "INFO")
        self.AXO_ENDPOINT_LOG_ERROR_FILE: bool = _bool("AXO_ENDPOINT_LOG_ERROR_FILE", "false")
        self.AXO_ENDPOINT_LOG_ROTATION_WHEN: str = os.environ.get(
            "AXO_ENDPOINT_LOG_ROTATION_WHEN", "midnight"
        )
        self.AXO_ENDPOINT_LOG_ROTATION_INTERVAL: int = _int(
            "AXO_ENDPOINT_LOG_ROTATION_INTERVAL", 1
        )
        self.AXO_ENDPOINT_LOG_JSON_INDENT: Optional[int] = _indent("AXO_ENDPOINT_LOG_JSON_INDENT")
        self.AXO_ENDPOINT_LOG_USE_RICH: bool = _bool("AXO_ENDPOINT_LOG_USE_RICH", "false")
        self.AXO_ENDPOINT_LOG_COLORIZE: bool = _bool("AXO_ENDPOINT_LOG_COLORIZE", "true")

        # Container runtime
        self.AXO_ENDPOINT_CONTAINER_BACKEND: str = os.environ.get(
            "AXO_ENDPOINT_CONTAINER_BACKEND", "docker"
        )
        self.AXO_ENDPOINT_CONTAINER_NETWORK: str = os.environ.get(
            "AXO_ENDPOINT_CONTAINER_NETWORK", "axo-net"
        )
        self.AXO_ENDPOINT_CONTAINER_RESULT_BIND: str = os.environ.get(
            "AXO_ENDPOINT_CONTAINER_RESULT_BIND", "tcp://0.0.0.0:5557"
        )
        self.AXO_ENDPOINT_CONTAINER_JOB_PORT: int = _int("AXO_ENDPOINT_CONTAINER_JOB_PORT", 5600)
        self.AXO_ENDPOINT_CONTAINER_FASTAPI_PORT: int = _int(
            "AXO_ENDPOINT_CONTAINER_FASTAPI_PORT", 8000
        )
        self.AXO_ENDPOINT_CONTAINER_READINESS_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_CONTAINER_READINESS_TIMEOUT_SECONDS", "60.0")
        )
        self.AXO_ENDPOINT_CONTAINER_PIP_CACHE_VOLUME: str = os.environ.get(
            "AXO_ENDPOINT_CONTAINER_PIP_CACHE_VOLUME", "axo-pip-cache"
        )
        self.AXO_ENDPOINT_CONTAINER_RUNNER_IMAGE: str = os.environ.get(
            "AXO_ENDPOINT_CONTAINER_RUNNER_IMAGE", "axo-runner"
        )
        self.AXO_ENDPOINT_CONTAINER_AUTO_BUILD_IMAGE: bool = _bool(
            "AXO_ENDPOINT_CONTAINER_AUTO_BUILD_IMAGE", "true"
        )
        self.AXO_ENDPOINT_CONTAINER_MEMORY_LIMIT_BYTES: int = _int(
            "AXO_ENDPOINT_CONTAINER_MEMORY_LIMIT_BYTES", 1024 * 1024 * 1024
        )
        self.AXO_ENDPOINT_CONTAINER_CPU_LIMIT: float = float(
            os.environ.get("AXO_ENDPOINT_CONTAINER_CPU_LIMIT", "1.0")
        )

        # External API publishing -- unset by default, gates the entire
        # feature (no publisher/bridge constructed, no events built at all)
        self.AXO_ENDPOINT_API_URI: Optional[str] = os.environ.get("AXO_ENDPOINT_API_URI") or None
        self.AXO_ENDPOINT_API_PUBLISH_TIMEOUT_MS: int = _int(
            "AXO_ENDPOINT_API_PUBLISH_TIMEOUT_MS", 1000
        )
        # Reliable delivery to the API (axo_shared.messaging): every event waits
        # in a local journal until the VEM acks it. The journal is a SQLite file
        # named after AXO_ENDPOINT_ID inside this directory, so unacked usage
        # records survive a restart; ":memory:" keeps it in memory (tests/dev).
        self.AXO_ENDPOINT_OUTBOX_PATH: str = os.environ.get(
            "AXO_ENDPOINT_OUTBOX_PATH", ".axo_endpoint/outbox"
        )
        self.AXO_ENDPOINT_OUTBOX_ACK_TIMEOUT_SECONDS: float = _float(
            "AXO_ENDPOINT_OUTBOX_ACK_TIMEOUT_SECONDS", 5.0
        )
        self.AXO_ENDPOINT_OUTBOX_RETRY_BASE_SECONDS: float = _float(
            "AXO_ENDPOINT_OUTBOX_RETRY_BASE_SECONDS", 0.5
        )
        self.AXO_ENDPOINT_OUTBOX_RETRY_MAX_SECONDS: float = _float(
            "AXO_ENDPOINT_OUTBOX_RETRY_MAX_SECONDS", 30.0
        )
        # Width of the live statistics bins (seconds). Each finished bin is
        # sent to the VEM as one MetricSummaryReported.
        self.AXO_ENDPOINT_METRICS_BIN_SECONDS: int = max(1, _int("AXO_ENDPOINT_METRICS_BIN_SECONDS", 10))

        # Who pays for this endpoint's work and how big it is -- stamped on
        # every JobAttemptRecorded (billing: execution time x size). Set by
        # the VEM when it launches the endpoint.
        self.AXO_ENDPOINT_OWNER_TYPE: str = os.environ.get("AXO_ENDPOINT_OWNER_TYPE", "user")
        self.AXO_ENDPOINT_OWNER_ID: str = os.environ.get("AXO_ENDPOINT_OWNER_ID", "")
        self.AXO_ENDPOINT_CPU_CORES: float = _float("AXO_ENDPOINT_CPU_CORES", 1.0)
        self.AXO_ENDPOINT_MEMORY_MB: int = _int("AXO_ENDPOINT_MEMORY_MB", 1024)

        # Runner budget: an endpoint's size (CPU_CORES / MEMORY_MB above)
        # covers itself plus all its runner containers. When enforced, a new
        # runner only starts if its size fits in what's left after the
        # endpoint's own reserve and every other live runner; otherwise the
        # job waits. Off by default (local dev has no size); the VEM turns it
        # on for every endpoint it launches.
        self.AXO_ENDPOINT_RUNNER_BUDGET_ENFORCED: bool = _bool("AXO_ENDPOINT_RUNNER_BUDGET_ENFORCED", "false")
        self.AXO_ENDPOINT_RESERVED_CPU_CORES: float = _float("AXO_ENDPOINT_RESERVED_CPU_CORES", 0.25)
        self.AXO_ENDPOINT_RESERVED_MEMORY_MB: int = _int("AXO_ENDPOINT_RESERVED_MEMORY_MB", 256)
        # The same budget at OS level: an existing cgroup every runner is
        # nested under (docker --cgroup-parent), created and limited by
        # whoever launched this endpoint. Empty = none (Swarm ignores it).
        self.AXO_ENDPOINT_RUNNER_CGROUP_PARENT: str = os.environ.get("AXO_ENDPOINT_RUNNER_CGROUP_PARENT", "")

        # The VirtualEnvironment this endpoint belongs to, fixed for its whole
        # life (endpoints never migrate between VEs). Heartbeats and peer
        # announces from any other VE are ignored. Unset = no VE (local dev);
        # such an endpoint only meshes with peers that also have none.
        self.AXO_ENDPOINT_VIRTUAL_ENV_ID: Optional[str] = (
            os.environ.get("AXO_ENDPOINT_VIRTUAL_ENV_ID") or None
        )

        # Cluster-wide container concurrency: how the leader picks among a
        # function's existing owners when placing a job that can't be
        # satisfied by growing a new container here.
        self.AXO_ENDPOINT_LOAD_BALANCE_STRATEGY: str = os.environ.get(
            "AXO_ENDPOINT_LOAD_BALANCE_STRATEGY", "round_robin"
        )

        # Job result replication (executor -> leader -> every endpoint)
        self.AXO_ENDPOINT_RESULT_REPLICATION_MAX_RETRIES: int = _int(
            "AXO_ENDPOINT_RESULT_REPLICATION_MAX_RETRIES", 5
        )
        self.AXO_ENDPOINT_RESULT_REPLICATION_RETRY_BACKOFF_BASE_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_RESULT_REPLICATION_RETRY_BACKOFF_BASE_SECONDS", "1.0")
        )

        # Leader-owned, paginated background re-verification of every job
        # result's consistency across the cluster.
        self.AXO_ENDPOINT_RESULT_CONSISTENCY_CHECK_INTERVAL_SECONDS: float = float(
            os.environ.get("AXO_ENDPOINT_RESULT_CONSISTENCY_CHECK_INTERVAL_SECONDS", "1800.0")
        )
        self.AXO_ENDPOINT_RESULT_CONSISTENCY_CHECK_CHUNK_SIZE: int = _int(
            "AXO_ENDPOINT_RESULT_CONSISTENCY_CHECK_CHUNK_SIZE", 50
        )

    def update(self, **kwargs) -> None:
        """Overrides one or more settings after creation. Unknown setting names are ignored."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
