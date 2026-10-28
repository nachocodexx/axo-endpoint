from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from axo_shared.metrics import bin_start

from axo_storage.auth import Caller

STORAGE_USAGE_REPORTED = "StorageUsageReported"
STORAGE_SIZES_REPORTED = "StorageSizesReported"
# One file version became complete (sent at once, not per minute): the CP
# passes it on to the VEs whose choreography triggers watch that bucket.
STORAGE_OBJECT_WRITTEN = "StorageObjectWritten"


def object_written(row: Any, caller: Optional[Caller]) -> Tuple[Dict[str, Any], str]:
    """(report, message id) for one complete version; the id is fixed per
    version, so the CP keeps it once."""
    data = {"bucket_key": row.bucket, "name": row.name, "version": row.version, "format": row.format,
            "size": row.total_size, "user_id": row.created_by, "source": row.source,
            "writer_ve_id": caller.virtual_environment_id if caller is not None else None,
            "completed_at": row.completed_at}
    return data, f"written:{row.bucket}:{row.name}:{row.version}"

# (bin start, bucket key, VE id or "", user id)
_Key = Tuple[float, str, str, str]


class UsageMeter:
    """Counts every bucket request that succeeded (upgrade.md 13.3): per
    clock-aligned minute, bucket (its storage key), VE (empty = a platform
    service, i.e. the Control Plane's bucket pages) and acting user -- the
    number of requests and the bytes read / written. The Control Plane works
    out who pays (the VE's owner, or the person) and what it costs.

    In memory only: a crash loses at most the current minute."""

    def __init__(self, bin_s: int = 60, now_fn: Callable[[], float] = time.time) -> None:
        self._bin_s = bin_s
        self._now_fn = now_fn
        self._lock = threading.Lock()
        self._bins: Dict[_Key, Dict[str, int]] = {}

    def record(self, bucket_key: str, caller: Caller, bytes_read: int = 0, bytes_written: int = 0,
               requests: int = 1) -> None:
        key = (bin_start(self._now_fn(), self._bin_s), bucket_key, caller.virtual_environment_id or "",
               caller.user_id)
        with self._lock:
            row = self._bins.setdefault(key, {"requests": 0, "bytes_read": 0, "bytes_written": 0})
            row["requests"] += int(requests)
            row["bytes_read"] += int(bytes_read)
            row["bytes_written"] += int(bytes_written)

    def drain(self, before: Optional[float] = None) -> Dict[float, List[Dict[str, Any]]]:
        """Takes out every finished minute (start + width <= ``before``,
        default now): {bin_start: [row, ...]}."""
        before = self._now_fn() if before is None else before
        out: Dict[float, List[Dict[str, Any]]] = {}
        with self._lock:
            for key in [k for k in self._bins if k[0] + self._bin_s <= before]:
                start, bucket, ve_id, user_id = key
                out.setdefault(start, []).append({"bucket": bucket, "ve_id": ve_id or None, "user_id": user_id,
                                                  **self._bins.pop(key)})
        return out

    @property
    def bin_s(self) -> int:
        return self._bin_s


class UsageReporter:
    """Sends the meter's finished minutes, and every hour the size of every
    bucket, to the Control Plane through a reliable sender (journal + ack,
    resent until saved). Message ids are fixed per minute / hour, so the CP
    keeps each once."""

    def __init__(self, meter: UsageMeter, sizes_fn: Callable[[], Dict[str, int]],
                 publish: Callable[[str, Dict[str, Any], str], Any], instance_id: str,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._meter = meter
        self._sizes_fn = sizes_fn
        self._publish = publish
        self._instance_id = instance_id
        self._now_fn = now_fn
        self._last_hour: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> int:
        """Sends what's due; returns how many messages were queued."""
        sent = 0
        for start, rows in sorted(self._meter.drain().items()):
            self._publish(STORAGE_USAGE_REPORTED,
                          {"instance_id": self._instance_id, "bin_start": start, "bin_width_s": self._meter.bin_s,
                           "rows": rows},
                          f"storage-usage:{self._instance_id}:{int(start)}")
            sent += 1
        hour = bin_start(self._now_fn(), 3600)
        if hour != self._last_hour:
            self._publish(STORAGE_SIZES_REPORTED,
                          {"instance_id": self._instance_id, "hour_start": hour, "sizes": self._sizes_fn()},
                          f"storage-sizes:{self._instance_id}:{int(hour)}")
            self._last_hour = hour
            sent += 1
        return sent

    def start(self, interval_s: float = 10.0) -> None:
        def loop() -> None:
            while not self._stop.wait(interval_s):
                try:
                    self.tick()
                except Exception:          # noqa: BLE001 -- the next tick tries again
                    pass
        self._thread = threading.Thread(target=loop, daemon=True, name="storage-usage")
        self._thread.start()

    def stop(self) -> None:
        """Sends the minute in progress too (a clean stop loses nothing)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for start, rows in sorted(self._meter.drain(before=float("inf")).items()):
            self._publish(STORAGE_USAGE_REPORTED,
                          {"instance_id": self._instance_id, "bin_start": start, "bin_width_s": self._meter.bin_s,
                           "rows": rows},
                          f"storage-usage:{self._instance_id}:{int(start)}")
