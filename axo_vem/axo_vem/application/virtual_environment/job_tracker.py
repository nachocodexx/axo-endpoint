from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Set

from axo_vem.domain.events import models

_JOB_EVENTS = frozenset({models.JOB_QUEUED, models.JOB_STARTED, models.JOB_COMPLETED, models.JOB_FAILED})


def _epoch(raw: Any, fallback: float) -> float:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return fallback


class JobTracker:
    """Live view of this VE's jobs, fed by the job events the VEM already
    receives (JobQueued → JobStarted → JobCompleted/JobFailed): which jobs
    are waiting and since when (to wake a sleeping endpoint when the wait
    passes the VE's wake setting), which endpoint runs what (to drain), and
    when each endpoint last did anything (to put idle ones to sleep). Lives
    in memory only -- a restarted VEM simply starts counting again."""

    def __init__(self, now_fn: Callable[[], float] = time.time) -> None:
        self._now_fn = now_fn
        self._waiting: Dict[str, float] = {}            # job_id -> queued at
        self._running: Dict[str, str] = {}              # job_id -> endpoint_id
        self._last_activity: Dict[str, float] = {}      # endpoint_id -> epoch
        self._lock = threading.Lock()

    def observe(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type not in _JOB_EVENTS:
            return
        job_id = data.get("job_id")
        endpoint_id = data.get("endpoint_id")
        at = _epoch(data.get("created_at"), self._now_fn())
        with self._lock:
            if endpoint_id:
                self._last_activity[endpoint_id] = max(at, self._last_activity.get(endpoint_id, 0.0))
            if event_type == models.JOB_QUEUED:
                if job_id not in self._running:
                    self._waiting.setdefault(job_id, at)
            elif event_type == models.JOB_STARTED:
                self._waiting.pop(job_id, None)
                if endpoint_id:
                    self._running[job_id] = endpoint_id
            else:
                self._waiting.pop(job_id, None)
                self._running.pop(job_id, None)

    def oldest_wait_seconds(self) -> float:
        """How long the longest-waiting not-yet-started job has waited (0 if none)."""
        with self._lock:
            if not self._waiting:
                return 0.0
            return max(0.0, self._now_fn() - min(self._waiting.values()))

    def waiting_count(self) -> int:
        with self._lock:
            return len(self._waiting)

    def active_jobs(self, endpoint_id: str) -> Set[str]:
        with self._lock:
            return {job for job, ep in self._running.items() if ep == endpoint_id}

    def last_activity(self, endpoint_id: str) -> Optional[float]:
        with self._lock:
            return self._last_activity.get(endpoint_id)

    def touch(self, endpoint_id: str) -> None:
        """An endpoint (re)started: its idle clock starts now."""
        with self._lock:
            self._last_activity[endpoint_id] = self._now_fn()
