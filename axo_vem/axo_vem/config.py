from __future__ import annotations

import logging
import os
from typing import List, Optional


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> List[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _optional_int(name: str) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _optional_float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


def _level(name: str, default: str) -> int:
    return getattr(logging, os.environ.get(name, default).upper(), getattr(logging, default))


def _indent(name: str) -> Optional[int]:
    raw = os.environ.get(name, "0")
    val = int(raw) if raw.isdigit() else 0
    return val if val > 0 else None


class Config:
    """Holds all AXO_VEM_* settings. The single place that reads os.environ."""

    def __init__(self) -> None:
        # ZMQ ingestion (node -> API, internal only)
        self.AXO_VEM_ROUTER_BIND: str = os.environ.get(
            "AXO_VEM_ROUTER_BIND", "tcp://0.0.0.0:6000"
        )

        # Who this VEM is -- set by the Control Plane when it launches the VE.
        # A VEM manages exactly ONE VE.
        self.AXO_VEM_VE_ID: str = os.environ.get("AXO_VEM_VE_ID", "")
        # The Control Plane's token: every HTTP call must carry it (plus the
        # acting user). Unset = every call refused.
        self.AXO_VEM_SERVICE_TOKEN: str = os.environ.get("AXO_VEM_SERVICE_TOKEN", "")
        # Hard limits from the VE's plan and the platform admin (set by the
        # Control Plane at launch); settings can never go past them.
        self.AXO_VEM_PLAN_MAX_ENDPOINTS: int = _int("AXO_VEM_PLAN_MAX_ENDPOINTS", 1)
        self.AXO_VEM_PLAN_POLICIES_ALLOWED: bool = _bool("AXO_VEM_PLAN_POLICIES_ALLOWED", "false")
        self.AXO_VEM_PLAN_MAX_RETENTION_DAYS: Optional[int] = _optional_int("AXO_VEM_PLAN_MAX_RETENTION_DAYS")
        # The VE quota: every endpoint's size together must fit. Empty = none.
        self.AXO_VEM_QUOTA_CPU_CORES: Optional[float] = _optional_float("AXO_VEM_QUOTA_CPU_CORES")
        self.AXO_VEM_QUOTA_MEMORY_MB: Optional[int] = _optional_int("AXO_VEM_QUOTA_MEMORY_MB")
        # JSON of the settings chosen when the VE was bought (target, min,
        # endpoint size); used only while the VE has no settings history.
        self.AXO_VEM_INITIAL_SETTINGS: str = os.environ.get("AXO_VEM_INITIAL_SETTINGS", "")
        self.AXO_VEM_PLATFORM_MAX_QUEUE_LIMIT: Optional[int] = _optional_int("AXO_VEM_PLATFORM_MAX_QUEUE_LIMIT")
        self.AXO_VEM_PLATFORM_MAX_WAIT_SECONDS: Optional[float] = _optional_float("AXO_VEM_PLATFORM_MAX_WAIT_SECONDS")
        self.AXO_VEM_PLATFORM_ALLOW_WAIT_FOREVER: bool = _bool("AXO_VEM_PLATFORM_ALLOW_WAIT_FOREVER", "true")

        # Endpoint lifecycle (Reconciler): how often it runs, how long a new
        # or woken endpoint may take to report in, how long draining waits
        # for running jobs, how many failures in a row before auto-replace
        # gives up, and how long a request waits for a sleeping endpoint.
        self.AXO_VEM_RECONCILE_INTERVAL_SECONDS: float = float(os.environ.get("AXO_VEM_RECONCILE_INTERVAL_SECONDS", "2.0"))
        self.AXO_VEM_ENDPOINT_STARTUP_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_STARTUP_TIMEOUT_SECONDS", "180.0")
        )
        self.AXO_VEM_ENDPOINT_DRAIN_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_DRAIN_TIMEOUT_SECONDS", "600.0")
        )
        self.AXO_VEM_ENDPOINT_MAX_FAILURES: int = _int("AXO_VEM_ENDPOINT_MAX_FAILURES", 3)
        # Scaling policies: evaluation cadence, and how long a manual target
        # change pauses them (so a person's choice isn't undone at once).
        self.AXO_VEM_POLICY_INTERVAL_SECONDS: float = float(os.environ.get("AXO_VEM_POLICY_INTERVAL_SECONDS", "10.0"))
        self.AXO_VEM_POLICY_PAUSE_AFTER_MANUAL_SECONDS: float = float(
            os.environ.get("AXO_VEM_POLICY_PAUSE_AFTER_MANUAL_SECONDS", "1800")
        )
        # The Control Plane link: heartbeats, job attempts (billing) and
        # 1-minute summaries, through a journal resent until the CP acks.
        # Empty = no CP (job attempts then go to this VEM's own event log).
        self.AXO_VEM_CP_URI: str = os.environ.get("AXO_VEM_CP_URI", "")
        self.AXO_VEM_CP_OUTBOX_PATH: str = os.environ.get("AXO_VEM_CP_OUTBOX_PATH", "/var/lib/axo_vem/outbox")
        self.AXO_VEM_CP_HEARTBEAT_SECONDS: float = float(os.environ.get("AXO_VEM_CP_HEARTBEAT_SECONDS", "5.0"))
        self.AXO_VEM_CP_FLUSH_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_CP_FLUSH_TIMEOUT_SECONDS", "60.0")
        )
        # How long 10 s metric bins stay in memory (the live tier).
        self.AXO_VEM_METRICS_KEEP_SECONDS: float = float(os.environ.get("AXO_VEM_METRICS_KEEP_SECONDS", "3600"))
        self.AXO_VEM_ENDPOINT_WAKE_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_WAKE_TIMEOUT_SECONDS", "120.0")
        )

        # axo_storage, where every bucket and object lives. Choreographies
        # (and the function code bucket) call it as a platform service with
        # this token, acting for a user.
        self.AXO_VEM_STORAGE_URI: str = os.environ.get("AXO_VEM_STORAGE_URI", "")
        self.AXO_VEM_STORAGE_SERVICE_TOKEN: str = os.environ.get("AXO_VEM_STORAGE_SERVICE_TOKEN", "")
        self.AXO_VEM_STORAGE_TIMEOUT_SECONDS: float = float(os.environ.get("AXO_VEM_STORAGE_TIMEOUT_SECONDS", "30.0"))
        # Handed to every endpoint the VEM launches: where it reaches
        # axo_storage, and this VE's finished axo_storage token (given by the
        # Control Plane -- the VEM never holds the secret tokens come from).
        self.AXO_VEM_NODE_STORAGE_URI: str = os.environ.get("AXO_VEM_NODE_STORAGE_URI", "")
        self.AXO_VEM_VE_STORAGE_TOKEN: str = os.environ.get("AXO_VEM_VE_STORAGE_TOKEN", "")

        # Outbound commands (VEM -> endpoint)
        self.AXO_VEM_ENDPOINT_COMMAND_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_COMMAND_TIMEOUT_SECONDS", "10.0")
        )

        # HTTP (external, the only exposed surface)
        self.AXO_VEM_HTTP_HOST: str = os.environ.get(
            "AXO_VEM_HTTP_HOST", "0.0.0.0"
        )
        self.AXO_VEM_HTTP_PORT: int = _int("AXO_VEM_HTTP_PORT", 8080)

        # CORS (browser-facing frontend origins allowed to call the HTTP API
        # directly -- e.g. axo-ui's dev server). Empty means no CORSMiddleware
        # is installed at all, not "allow nothing".
        self.AXO_VEM_CORS_ORIGINS: List[str] = _list(
            "AXO_VEM_CORS_ORIGINS", "*"
        )

        # Kurrent (write side / source of truth)
        self.AXO_VEM_KURRENT_URI: str = os.environ.get(
            "AXO_VEM_KURRENT_URI", "esdb://localhost:2113?tls=false"
        )

        # MongoDB (read side / materialized view)
        self.AXO_VEM_MONGO_URI: str = os.environ.get(
            "AXO_VEM_MONGO_URI", "mongodb://localhost:27017"
        )
        self.AXO_VEM_MONGO_DB_NAME: str = os.environ.get(
            "AXO_VEM_MONGO_DB_NAME", "axo_vem"
        )

        # Retry/backoff for Mongo/Kurrent connection attempts -- shared by the
        # eager startup probe (server.py), KurrentSubscriber's reconnect loop,
        # and IngestionRouterServer's append retry (see infrastructure/resilience/retry.py).
        self.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS: int = _int(
            "AXO_VEM_DB_CONNECT_MAX_ATTEMPTS", 10
        )
        self.AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS: float = float(
            os.environ.get("AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS", "1.0")
        )
        self.AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS: float = float(
            os.environ.get("AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS", "30.0")
        )
        # Backstop for a KurrentSubscriber subscription that goes silently
        # idle (no exception, no data) after a network disruption -- see
        # infrastructure/database/kurrent/subscriber.py's watchdog.
        self.AXO_VEM_PROJECTOR_STALE_AFTER_SECONDS: float = float(
            os.environ.get("AXO_VEM_PROJECTOR_STALE_AFTER_SECONDS", "60.0")
        )

        # Node deployment (launching new axo_endpoint node containers via
        # the shared axo_shared.container.ContainerSpawner -- see
        # application/nodes/launch_endpoint_node.py)
        self.AXO_VEM_NODE_IMAGE: str = os.environ.get(
            "AXO_VEM_NODE_IMAGE", "axo-endpoint:local"
        )
        self.AXO_VEM_NODE_NETWORK: str = os.environ.get(
            "AXO_VEM_NODE_NETWORK", "axo-net"
        )
        self.AXO_VEM_DOCKER_MODE: str = os.environ.get(
            "AXO_VEM_DOCKER_MODE", "docker"
        )
        # The resolvable address a newly-launched node should use for its own
        # AXO_ENDPOINT_API_URI -- this node's own bind address isn't
        # connectable from another container (same resolvability gap as a
        # peer's heartbeat rpc_uri, see root CLAUDE.md), so it's a separate,
        # explicitly-configured value rather than derived from ROUTER_BIND.
        self.AXO_VEM_NODE_API_URI: str = os.environ.get(
            "AXO_VEM_NODE_API_URI", "tcp://axo-vem:6000"
        )
        # How often EndpointStatsPoller polls Docker container stats for
        # every known endpoint and broadcasts them over /ws/endpoints --
        # see infrastructure/container/endpoint_stats_poller.py.
        self.AXO_VEM_STATS_POLL_INTERVAL_SECONDS: float = float(
            os.environ.get("AXO_VEM_STATS_POLL_INTERVAL_SECONDS", "3.0")
        )


        # Endpoint liveness detection -- EndpointLivenessWorker (see
        # infrastructure/transport/zmq_command/endpoint_liveness_worker.py).
        # X: fixed, system-wide staleness threshold -- no per-user override,
        # the worker has no request-scoped current user to resolve one
        # against.
        self.AXO_VEM_ENDPOINT_STALE_AFTER_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_STALE_AFTER_SECONDS", "90.0")
        )
        self.AXO_VEM_ENDPOINT_LIVENESS_TICK_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_LIVENESS_TICK_SECONDS", "30.0")
        )
        self.AXO_VEM_ENDPOINT_PING_TIMEOUT_SECONDS: float = float(
            os.environ.get("AXO_VEM_ENDPOINT_PING_TIMEOUT_SECONDS", "3.0")
        )
        # Y's fallback default (see Preferences.endpoint_purge_eligible_after_minutes,
        # axo_shared/axo_shared/events/models.py) -- resolved per-request by
        # _purge_eligible_after_for() in infrastructure/transport/api/controllers/
        # endpoints.py when the viewing user has no profile / no explicit
        # override, mirroring history.py's _since_for -- except sourced from
        # this env var rather than a hardcoded Python constant.
        self.AXO_VEM_ENDPOINT_PURGE_ELIGIBLE_AFTER_MINUTES: int = _int(
            "AXO_VEM_ENDPOINT_PURGE_ELIGIBLE_AFTER_MINUTES", 60
        )

        # Logging
        self.AXO_VEM_LOG_LEVEL: int = _level("AXO_VEM_LOG_LEVEL", "DEBUG")
        self.AXO_VEM_LOG_DISABLED: bool = _bool("AXO_VEM_LOG_DISABLED", "false")
        self.AXO_VEM_LOG_TO_FILE: bool = _bool("AXO_VEM_LOG_TO_FILE", "false")
        self.AXO_VEM_LOG_PATH: str = os.environ.get(
            "AXO_VEM_LOG_PATH", ".axo_vem/log"
        )
        self.AXO_VEM_LOG_FILENAME: str = os.environ.get(
            "AXO_VEM_LOG_FILENAME", "axo_vem"
        )
        self.AXO_VEM_LOG_OUTPUT_PATH: Optional[str] = os.environ.get(
            "AXO_VEM_LOG_OUTPUT_PATH"
        )
        self.AXO_VEM_LOG_ERROR_OUTPUT_PATH: Optional[str] = os.environ.get(
            "AXO_VEM_LOG_ERROR_OUTPUT_PATH"
        )
        self.AXO_VEM_LOG_CONSOLE_LEVEL: int = _level(
            "AXO_VEM_LOG_CONSOLE_LEVEL", "DEBUG"
        )
        self.AXO_VEM_LOG_FILE_LEVEL: int = _level(
            "AXO_VEM_LOG_FILE_LEVEL", "DEBUG"
        )
        self.AXO_VEM_LOG_ERROR_FILE: bool = _bool(
            "AXO_VEM_LOG_ERROR_FILE", "false"
        )
        self.AXO_VEM_LOG_ROTATION_WHEN: str = os.environ.get(
            "AXO_VEM_LOG_ROTATION_WHEN", "midnight"
        )
        self.AXO_VEM_LOG_ROTATION_INTERVAL: int = _int(
            "AXO_VEM_LOG_ROTATION_INTERVAL", 1
        )
        self.AXO_VEM_LOG_JSON_INDENT: Optional[int] = _indent(
            "AXO_VEM_LOG_JSON_INDENT"
        )
        self.AXO_VEM_LOG_USE_RICH: bool = _bool("AXO_VEM_LOG_USE_RICH", "false")
        self.AXO_VEM_LOG_COLORIZE: bool = _bool("AXO_VEM_LOG_COLORIZE", "true")

    def update(self, **kwargs) -> None:
        """Overrides one or more settings after creation. Unknown setting names are ignored."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
