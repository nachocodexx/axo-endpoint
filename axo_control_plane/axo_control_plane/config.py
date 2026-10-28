from __future__ import annotations

import logging
import os
from typing import List, Optional


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


def _list(name: str, default: str) -> List[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


def _level(name: str, default: str) -> int:
    return getattr(logging, os.environ.get(name, default).upper(), getattr(logging, default))


def _indent(name: str) -> Optional[int]:
    raw = os.environ.get(name, "0")
    val = int(raw) if raw.isdigit() else 0
    return val if val > 0 else None


class Config:
    """Every AXO_CP_* setting. The only place that reads os.environ for them.

    The Control Plane runs as several identical copies sharing one Kurrent
    (history) and one Mongo (current state, leases, queues); nothing here is
    per-copy except AXO_CP_INSTANCE_ID."""

    def __init__(self) -> None:
        # ── this copy ─────────────────────────────────────────────────────────
        # Unique per running copy (used for the per-VE / cleanup leases).
        # Empty = hostname + pid.
        self.AXO_CP_INSTANCE_ID: str = os.environ.get("AXO_CP_INSTANCE_ID", "")

        # ── HTTP (the platform's front door) ──────────────────────────────────
        self.AXO_CP_HTTP_HOST: str = os.environ.get("AXO_CP_HTTP_HOST", "0.0.0.0")
        self.AXO_CP_HTTP_PORT: int = _int("AXO_CP_HTTP_PORT", 9000)
        self.AXO_CP_CORS_ORIGINS: List[str] = _list("AXO_CP_CORS_ORIGINS", "*")

        # ── ZMQ intake from the VEMs (heartbeats, job attempts, summaries) ──
        self.AXO_CP_ROUTER_BIND: str = os.environ.get("AXO_CP_ROUTER_BIND", "tcp://0.0.0.0:9100")
        # The address a VEM is given to reach this intake (AXO_VEM_CP_URI).
        self.AXO_CP_ROUTER_PUBLIC_URI: str = os.environ.get("AXO_CP_ROUTER_PUBLIC_URI", "tcp://axo-control-plane:9100")

        # ── storage of the Control Plane itself ───────────────────────────────
        self.AXO_CP_KURRENT_URI: str = os.environ.get("AXO_CP_KURRENT_URI", "esdb://localhost:2113?tls=false")
        self.AXO_CP_MONGO_URI: str = os.environ.get("AXO_CP_MONGO_URI", "mongodb://localhost:27017")
        self.AXO_CP_MONGO_DB_NAME: str = os.environ.get("AXO_CP_MONGO_DB_NAME", "axo_control_plane")

        # ── secrets ───────────────────────────────────────────────────────────
        # Each VEM's service token = HMAC(this secret, ve id): a VEM can only
        # ever be commanded with its own VE's token.
        self.AXO_CP_VEM_TOKEN_SECRET: str = os.environ.get("AXO_CP_VEM_TOKEN_SECRET", "")
        # axo_storage's per-VE token secret (same value axo_storage holds).
        self.AXO_CP_STORAGE_VE_TOKEN_SECRET: str = os.environ.get("AXO_CP_STORAGE_VE_TOKEN_SECRET", "")
        # The token axo_storage presents when it asks "may this user ...?".
        self.AXO_CP_STORAGE_CHECK_TOKEN: str = os.environ.get("AXO_CP_STORAGE_CHECK_TOKEN", "")
        # SDK keys are stored as HMAC(this pepper, key).
        self.AXO_CP_SDK_KEY_PEPPER: str = os.environ.get("AXO_CP_SDK_KEY_PEPPER", "")

        # ── axo_storage ───────────────────────────────────────────────────────
        self.AXO_CP_STORAGE_URI: str = os.environ.get("AXO_CP_STORAGE_URI", "tcp://axo-storage:7000")
        self.AXO_CP_STORAGE_HTTP_URI: str = os.environ.get("AXO_CP_STORAGE_HTTP_URI", "http://axo-storage:7080")
        self.AXO_CP_STORAGE_SERVICE_TOKEN: str = os.environ.get("AXO_CP_STORAGE_SERVICE_TOKEN", "")
        # The container name axo_storage runs as (attached to every VE network).
        self.AXO_CP_STORAGE_CONTAINER: str = os.environ.get("AXO_CP_STORAGE_CONTAINER", "axo-storage")
        self.AXO_CP_STORAGE_TIMEOUT_SECONDS: float = _float("AXO_CP_STORAGE_TIMEOUT_SECONDS", 30.0)
        self.AXO_CP_BUCKET_DEFAULT_QUOTA_BYTES: int = _int("AXO_CP_BUCKET_DEFAULT_QUOTA_BYTES", 10 * 1024 ** 3)
        self.AXO_CP_BUCKET_MAX_QUOTA_BYTES: int = _int("AXO_CP_BUCKET_MAX_QUOTA_BYTES", 1024 ** 4)

        # ── Xolo (only the Control Plane talks to it) ─────────────────────────
        self.AXO_CP_XOLO_URI: str = os.environ.get("AXO_CP_XOLO_URI", "http://xolo:10000/api/v4")
        self.AXO_CP_XOLO_ACCOUNT_ID: str = os.environ.get("AXO_CP_XOLO_ACCOUNT_ID", "axo")
        self.AXO_CP_XOLO_API_KEY: str = os.environ.get("AXO_CP_XOLO_API_KEY", "")
        self.AXO_CP_XOLO_SCOPE: str = os.environ.get("AXO_CP_XOLO_SCOPE", "AXO")
        self.AXO_CP_XOLO_LICENSE_EXPIRATION: str = os.environ.get("AXO_CP_XOLO_LICENSE_EXPIRATION", "100y")
        self.AXO_CP_SESSION_EXPIRATION: str = os.environ.get("AXO_CP_SESSION_EXPIRATION", "1h")
        self.AXO_CP_SESSION_CACHE_SECONDS: float = _float("AXO_CP_SESSION_CACHE_SECONDS", 60.0)
        self.AXO_CP_ROLE_CACHE_SECONDS: float = _float("AXO_CP_ROLE_CACHE_SECONDS", 300.0)
        # Bootstrap Platform Admin, created at the first start if missing; must
        # change the password at first login.
        self.AXO_CP_ADMIN_USERNAME: str = os.environ.get("AXO_CP_ADMIN_USERNAME", "admin")
        self.AXO_CP_ADMIN_PASSWORD: str = os.environ.get("AXO_CP_ADMIN_PASSWORD", "admin")
        self.AXO_CP_ADMIN_EMAIL: str = os.environ.get("AXO_CP_ADMIN_EMAIL", "admin@example.com")

        # ── platform capacity (what can be handed out to VEs) ────────────────
        self.AXO_CP_CAPACITY_CPU_CORES: float = _float("AXO_CP_CAPACITY_CPU_CORES", 16.0)
        self.AXO_CP_CAPACITY_MEMORY_MB: int = _int("AXO_CP_CAPACITY_MEMORY_MB", 32768)
        # Platform Admin hard caps handed to every VEM.
        self.AXO_CP_PLATFORM_MAX_QUEUE_LIMIT: Optional[int] = (
            _int("AXO_CP_PLATFORM_MAX_QUEUE_LIMIT", 0) or None)
        self.AXO_CP_PLATFORM_MAX_WAIT_SECONDS: Optional[float] = (
            _float("AXO_CP_PLATFORM_MAX_WAIT_SECONDS", 0.0) or None)
        # A VE's history past its retention is hidden at once and deleted
        # for good this many days later (every VEM gets it).
        self.AXO_CP_HISTORY_GRACE_DAYS: int = _int("AXO_CP_HISTORY_GRACE_DAYS", 7)
        # Notifications older than this are deleted for good (renewals loop).
        self.AXO_CP_NOTIFICATION_KEEP_DAYS: int = _int("AXO_CP_NOTIFICATION_KEEP_DAYS", 90)
        self.AXO_CP_CUSTOM_SIZE_MAX_CPU_CORES: float = _float("AXO_CP_CUSTOM_SIZE_MAX_CPU_CORES", 8.0)
        self.AXO_CP_CUSTOM_SIZE_MAX_MEMORY_MB: int = _int("AXO_CP_CUSTOM_SIZE_MAX_MEMORY_MB", 16384)

        # ── running VEs (containers) ──────────────────────────────────────────
        self.AXO_CP_CONTAINER_BACKEND: str = os.environ.get("AXO_CP_CONTAINER_BACKEND", "docker")
        self.AXO_CP_CONTROL_NETWORK: str = os.environ.get("AXO_CP_CONTROL_NETWORK", "axo-control-net")
        self.AXO_CP_VEM_IMAGE: str = os.environ.get("AXO_CP_VEM_IMAGE", "axo-vem:local")
        self.AXO_CP_ENDPOINT_IMAGE: str = os.environ.get("AXO_CP_ENDPOINT_IMAGE", "axo-endpoint:local")
        self.AXO_CP_KURRENT_IMAGE: str = os.environ.get("AXO_CP_KURRENT_IMAGE", "eventstore/eventstore:24.10.0-jammy")
        self.AXO_CP_MONGO_IMAGE: str = os.environ.get("AXO_CP_MONGO_IMAGE", "mongo:7")
        self.AXO_CP_REDIS_IMAGE: str = os.environ.get("AXO_CP_REDIS_IMAGE", "redis:8-alpine")
        self.AXO_CP_VEM_HTTP_PORT: int = _int("AXO_CP_VEM_HTTP_PORT", 8080)
        self.AXO_CP_VEM_REQUEST_TIMEOUT_SECONDS: float = _float("AXO_CP_VEM_REQUEST_TIMEOUT_SECONDS", 30.0)
        # Desired-state loop + per-VE lease.
        self.AXO_CP_PROVISION_INTERVAL_SECONDS: float = _float("AXO_CP_PROVISION_INTERVAL_SECONDS", 5.0)
        self.AXO_CP_LEASE_SECONDS: float = _float("AXO_CP_LEASE_SECONDS", 30.0)
        self.AXO_CP_VEM_HEARTBEAT_TIMEOUT_SECONDS: float = _float("AXO_CP_VEM_HEARTBEAT_TIMEOUT_SECONDS", 30.0)
        self.AXO_CP_VEM_DOWN_RECREATE_SECONDS: float = _float("AXO_CP_VEM_DOWN_RECREATE_SECONDS", 300.0)
        # Repair: people (Admin+) once per this per VE; the CP on its own once a
        # VE has been Unavailable this long, at most once per the last one.
        self.AXO_CP_REPAIR_EVERY_SECONDS: float = _float("AXO_CP_REPAIR_EVERY_SECONDS", 300.0)
        self.AXO_CP_AUTO_REPAIR_AFTER_SECONDS: float = _float("AXO_CP_AUTO_REPAIR_AFTER_SECONDS", 600.0)
        self.AXO_CP_AUTO_REPAIR_EVERY_SECONDS: float = _float("AXO_CP_AUTO_REPAIR_EVERY_SECONDS", 3600.0)
        # Deleting: wait for the VEM's unsent messages, then copy its outbox.
        self.AXO_CP_DELETE_FLUSH_TIMEOUT_SECONDS: float = _float("AXO_CP_DELETE_FLUSH_TIMEOUT_SECONDS", 600.0)
        self.AXO_CP_GRACE_DAYS: float = _float("AXO_CP_GRACE_DAYS", 30.0)

        # ── sleeping (Free plan) ──────────────────────────────────────────────
        self.AXO_CP_SLEEP_AFTER_IDLE_SECONDS: float = _float("AXO_CP_SLEEP_AFTER_IDLE_SECONDS", 1800.0)
        self.AXO_CP_WAKE_TIMEOUT_SECONDS: float = _float("AXO_CP_WAKE_TIMEOUT_SECONDS", 300.0)

        # ── cleanup of leftover containers ────────────────────────────────────
        self.AXO_CP_CLEANUP_INTERVAL_SECONDS: float = _float("AXO_CP_CLEANUP_INTERVAL_SECONDS", 60.0)
        # How often rental periods are checked (renewals, cancelled VEs ending).
        self.AXO_CP_RENEWAL_INTERVAL_SECONDS: float = _float("AXO_CP_RENEWAL_INTERVAL_SECONDS", 60.0)
        # A VE bill closes this long after its period ends (later usage goes
        # on the next bill).
        self.AXO_CP_BILL_CLOSE_AFTER_SECONDS: float = _float("AXO_CP_BILL_CLOSE_AFTER_SECONDS", 3600.0)
        # Statistics tiers kept by the CP, finest first ("width:keep"; the 10 s
        # live tier stays in each VEM), and how often finer bins are merged up.
        self.AXO_CP_METRIC_TIERS: str = os.environ.get("AXO_CP_METRIC_TIERS", "1m:7d,15m:90d,1d:forever")
        self.AXO_CP_METRIC_ROLLUP_INTERVAL_SECONDS: float = _float("AXO_CP_METRIC_ROLLUP_INTERVAL_SECONDS", 300.0)
        self.AXO_CP_CLEANUP_LOG_LINES: int = _int("AXO_CP_CLEANUP_LOG_LINES", 500)

        # ── logging ───────────────────────────────────────────────────────────
        self.AXO_CP_LOG_LEVEL: int = _level("AXO_CP_LOG_LEVEL", "DEBUG")
        self.AXO_CP_LOG_DISABLED: bool = _bool("AXO_CP_LOG_DISABLED", "false")
        self.AXO_CP_LOG_TO_FILE: bool = _bool("AXO_CP_LOG_TO_FILE", "false")
        self.AXO_CP_LOG_PATH: str = os.environ.get("AXO_CP_LOG_PATH", ".axo_control_plane/log")
        self.AXO_CP_LOG_FILENAME: str = os.environ.get("AXO_CP_LOG_FILENAME", "axo_control_plane")
        self.AXO_CP_LOG_CONSOLE_LEVEL: int = _level("AXO_CP_LOG_CONSOLE_LEVEL", "DEBUG")
        self.AXO_CP_LOG_FILE_LEVEL: int = _level("AXO_CP_LOG_FILE_LEVEL", "DEBUG")
        self.AXO_CP_LOG_ERROR_FILE: bool = _bool("AXO_CP_LOG_ERROR_FILE", "false")
        self.AXO_CP_LOG_ROTATION_WHEN: str = os.environ.get("AXO_CP_LOG_ROTATION_WHEN", "midnight")
        self.AXO_CP_LOG_ROTATION_INTERVAL: int = _int("AXO_CP_LOG_ROTATION_INTERVAL", 1)
        self.AXO_CP_LOG_JSON_INDENT: Optional[int] = _indent("AXO_CP_LOG_JSON_INDENT")
        self.AXO_CP_LOG_USE_RICH: bool = _bool("AXO_CP_LOG_USE_RICH", "false")
        self.AXO_CP_LOG_COLORIZE: bool = _bool("AXO_CP_LOG_COLORIZE", "true")

    def update(self, **kwargs) -> None:
        """Overrides settings after creation. Unknown names are ignored."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
