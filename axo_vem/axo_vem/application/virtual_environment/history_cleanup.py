from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional


class HistoryCleanup:
    """Deletes this VE's history for good once it's past the VE's retention
    plus the grace days (``cutoff_fn``, from VeSettingsService.deletion_cutoff):
    the activity rows (Activity page, endpoint / function / object history),
    finished jobs, finished choreography runs and the Events list. Before that, rows past the
    retention are only hidden, so a mistaken lower setting or an upgrade
    within the grace days loses nothing. Bills and billing records live in
    the Control Plane and are never touched."""

    def __init__(self, activity, jobs, runs, cutoff_fn: Callable[[], Optional[datetime]], events=None) -> None:
        self._activity = activity
        self._jobs = jobs
        self._runs = runs
        self._cutoff_fn = cutoff_fn
        self._events = events

    def run(self) -> Dict[str, int]:
        """How many of each were deleted (nothing when there's no retention)."""
        cutoff = self._cutoff_fn()
        if cutoff is None:
            return {"activity": 0, "jobs": 0, "runs": 0, "events": 0}
        return {
            "activity": self._activity.delete_before(cutoff),
            "jobs": self._jobs.delete_finished_before(cutoff.isoformat()),
            "runs": self._runs.delete_finished_before(cutoff),
            "events": self._events.delete_before(cutoff.isoformat()) if self._events is not None else 0,
        }
