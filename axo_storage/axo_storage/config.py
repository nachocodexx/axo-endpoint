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
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


def _level(name: str, default: str) -> int:
    return getattr(logging, os.environ.get(name, default).upper(), getattr(logging, default))


def _indent(name: str) -> Optional[int]:
    raw = os.environ.get(name, "0")
    val = int(raw) if raw.isdigit() else 0
    return val if val > 0 else None


class Config:
    """Holds all AXO_STORAGE_* settings. The single place that reads os.environ."""

    def __init__(self) -> None:
        # ZMQ (chunk protocol). The HTTP API for bucket pages comes in phase 5.
        self.AXO_STORAGE_ROUTER_BIND: str = os.environ.get("AXO_STORAGE_ROUTER_BIND", "tcp://0.0.0.0:7000")
        self.AXO_STORAGE_WORKERS: int = max(1, _int("AXO_STORAGE_WORKERS", 8))

        # StorageLayer for chunk bytes: "fs" or "memory" (S3/MinIO later).
        self.AXO_STORAGE_BACKEND: str = os.environ.get("AXO_STORAGE_BACKEND", "fs")
        self.AXO_STORAGE_FS_ROOT: str = os.environ.get("AXO_STORAGE_FS_ROOT", ".axo_storage/data")
        # SQLite catalog (objects, versions, chunk presence). ":memory:" for tests.
        self.AXO_STORAGE_CATALOG_PATH: str = os.environ.get("AXO_STORAGE_CATALOG_PATH", ".axo_storage/catalog.db")

        # Credentials, until the Control Plane issues them (phase 5):
        # VE tokens as "ve-id=token,ve-id=token"; service tokens comma-separated.
        self.AXO_STORAGE_VE_TOKENS: str = os.environ.get("AXO_STORAGE_VE_TOKENS", "")
        self.AXO_STORAGE_SERVICE_TOKENS: List[str] = _list("AXO_STORAGE_SERVICE_TOKENS", "")
        # Or: every VE's token is HMAC(secret, ve_id) -- shared with the VEM,
        # which hands each endpoint it launches its VE's token.
        self.AXO_STORAGE_VE_TOKEN_SECRET: str = os.environ.get("AXO_STORAGE_VE_TOKEN_SECRET", "")

        # Who answers "may this user read/write this bucket?":
        # "control_plane" = the Control Plane (the real one, with Xolo behind);
        # "static" = rules from AXO_STORAGE_ACCESS_FILE (JSON, see
        # access.StaticAccessChecker); "allow_all" = local dev only.
        self.AXO_STORAGE_ACCESS_MODE: str = os.environ.get("AXO_STORAGE_ACCESS_MODE", "static")
        self.AXO_STORAGE_ACCESS_FILE: str = os.environ.get("AXO_STORAGE_ACCESS_FILE", "")
        self.AXO_STORAGE_ACCESS_CACHE_SECONDS: float = _float("AXO_STORAGE_ACCESS_CACHE_SECONDS", 5.0)
        # "control_plane" mode: where the CP answers access questions, and the
        # token this service presents there (= the CP's AXO_CP_STORAGE_CHECK_TOKEN).
        self.AXO_STORAGE_CP_URI: str = os.environ.get("AXO_STORAGE_CP_URI", "")
        self.AXO_STORAGE_CP_CHECK_TOKEN: str = os.environ.get("AXO_STORAGE_CP_CHECK_TOKEN", "")

        # Logging
        self.AXO_STORAGE_LOG_LEVEL: int = _level("AXO_STORAGE_LOG_LEVEL", "INFO")
        self.AXO_STORAGE_LOG_DISABLED: bool = _bool("AXO_STORAGE_LOG_DISABLED", "false")
        self.AXO_STORAGE_LOG_TO_FILE: bool = _bool("AXO_STORAGE_LOG_TO_FILE", "false")
        self.AXO_STORAGE_LOG_PATH: str = os.environ.get("AXO_STORAGE_LOG_PATH", ".axo_storage/log")
        self.AXO_STORAGE_LOG_FILENAME: str = os.environ.get("AXO_STORAGE_LOG_FILENAME", "axo_storage")
        self.AXO_STORAGE_LOG_CONSOLE_LEVEL: int = _level("AXO_STORAGE_LOG_CONSOLE_LEVEL", "INFO")
        self.AXO_STORAGE_LOG_FILE_LEVEL: int = _level("AXO_STORAGE_LOG_FILE_LEVEL", "DEBUG")
        self.AXO_STORAGE_LOG_JSON_INDENT: Optional[int] = _indent("AXO_STORAGE_LOG_JSON_INDENT")
        self.AXO_STORAGE_LOG_USE_RICH: bool = _bool("AXO_STORAGE_LOG_USE_RICH", "false")
