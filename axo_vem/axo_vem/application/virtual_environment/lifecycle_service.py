from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from axo_shared.virtual_environment.states import compute_health

from axo_vem.application.virtual_environment.endpoint_ops import EndpointOps
from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.restorer import VeRestorer
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.virtual_environment.fleet import (
    CREATING,
    DRAINING,
    FAILED,
    REMOVING,
    RUNNING,
    SLEEPING,
    STARTING,
)

CREATING_VE = "Creating"
RUNNING_VE = "Running"
SCALING_VE = "Scaling"
STOPPING_VE = "Stopping"
STOPPED_VE = "Stopped"
STARTING_VE = "Starting"
RESTORING_VE = "Restoring"
DELETING_VE = "Deleting"
FAILED_VE = "Failed"


def stream_name(virtual_environment_id: str) -> str:
    return f"ve-lifecycle-{virtual_environment_id}"


class VeLifecycleService:
    """What the whole VE is doing, and the three VE-wide operations the
    Control Plane asks for. Each runs in the background; the CP polls
    ``status()`` (GET /ve) until it's done:

    - stop: no new jobs → endpoints drain (running jobs finish, up to the
      drain timeout, then leftovers are cancelled) → endpoint containers
      halted but KEPT → Stopped ("ready to stop": the CP then stops the VEM,
      Kurrent and Mongo);
    - start: endpoints started again (they come up empty) → Restoring
      re-registers every function from the code bucket → Running;
    - prepare_delete: like stop, but endpoint containers are removed for
      good and the VE stays Deleting with ready_for_removal set.

    Every lifecycle change is an event in the VEM's own log, so a VEM started
    again knows where it left off."""

    def __init__(
        self,
        settings: VeSettingsService,
        fleet: FleetService,
        reconciler: Reconciler,
        ops: EndpointOps,
        jobs: JobTracker,
        event_publisher: EventPublisher,
        restorer: Optional[VeRestorer] = None,
        has_leader: Callable[[], bool] = lambda: True,
        drain_timeout_s: float = 600.0,
        start_timeout_s: float = 300.0,
        poll_s: float = 1.0,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        before_settled: Callable[[], None] = lambda: None,
    ) -> None:
        self._settings = settings
        self._fleet = fleet
        self._reconciler = reconciler
        self._ops = ops
        self._jobs = jobs
        self._events = event_publisher
        self._restorer = restorer
        self._has_leader = has_leader
        self._drain_timeout_s = drain_timeout_s
        self._start_timeout_s = start_timeout_s
        self._poll_s = poll_s
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        # Runs right before Stopped / ready_for_removal is reported, i.e.
        # before the CP shuts this VEM down -- e.g. flushing the usage
        # records still waiting for the CP's ack.
        self._before_settled = before_settled
        self._lifecycle = CREATING_VE
        self._ready_for_removal = False
        self._idle_since: Optional[float] = None
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # ── state ────────────────────────────────────────────────────────────────
    def load(self, reader) -> None:
        try:
            records = reader.get_stream(stream_name(self._settings.virtual_environment_id))
        except Exception:
            records = []
        for record in records or []:
            if record.type == models.VE_LIFECYCLE_CHANGED:
                self._lifecycle = json.loads(record.data.decode("utf-8"))["lifecycle"]
        if self._lifecycle in (STOPPING_VE, DELETING_VE):
            # The VEM restarted mid-operation: hold the endpoints still; the
            # CP repeats the operation, which is safe to repeat.
            self._reconciler.pause(True)

    def lifecycle(self) -> str:
        with self._lock:
            return self._lifecycle

    def _record(self, lifecycle: str, reason: str) -> None:
        with self._lock:
            if self._lifecycle == lifecycle:
                return
            self._lifecycle = lifecycle
        event = models.VirtualEnvironmentLifecycleChanged(
            virtual_environment_id=self._settings.virtual_environment_id, lifecycle=lifecycle, reason=reason,
            created_at=datetime.fromtimestamp(self._now_fn(), tz=timezone.utc),
        )
        self._events.append_to_stream(
            stream_name(self._settings.virtual_environment_id), models.VE_LIFECYCLE_CHANGED,
            event.model_dump(mode="json"),
        )

    def after_tick(self) -> None:
        """Called after every reconciler tick: a new VE becomes Running once
        enough endpoints run; tracks when the VE went idle (for the Free plan
        sleep, which the CP decides)."""
        settings = self._settings.current()
        running = len(self._fleet.in_states(RUNNING))
        if self.lifecycle() == CREATING_VE and running >= max(1, settings.min_endpoints):
            self._record(RUNNING_VE, "endpoints running")
        busy = self._jobs.waiting_count() or any(
            self._jobs.active_jobs(e.endpoint_id) for e in self._fleet.in_states(RUNNING, DRAINING)
        )
        with self._lock:
            if busy:
                self._idle_since = None
            elif self._idle_since is None:
                self._idle_since = self._now_fn()

    def status(self) -> Dict[str, Any]:
        settings = self._settings.current()
        lifecycle = self.lifecycle()
        counts = self._reconciler.endpoints_by_state()
        moving = counts.get(CREATING, 0) + counts.get(DRAINING, 0) + counts.get(REMOVING, 0) + sum(
            1 for e in self._fleet.in_states(STARTING) if not (e.reason or "").startswith("waking")
        )
        if lifecycle == RUNNING_VE and moving:
            lifecycle = SCALING_VE
        health = compute_health(
            lifecycle, vem_up=True, has_leader=self._has_leader(),
            running=counts.get(RUNNING, 0) + counts.get(SLEEPING, 0),  # sleeping = planned, not missing
            failed=counts.get(FAILED, 0), min_endpoints=settings.min_endpoints,
        ) if lifecycle not in (STOPPING_VE, STARTING_VE, RESTORING_VE, DELETING_VE, CREATING_VE) else None
        with self._lock:
            idle_since = self._idle_since
            ready = self._ready_for_removal
        return {
            "virtual_environment_id": self._settings.virtual_environment_id,
            "lifecycle": lifecycle,
            "health": health,
            "target_endpoints": settings.target_endpoints,
            "min_endpoints": settings.min_endpoints,
            "endpoints": counts,
            "restore": self._restorer.progress().to_dict() if self._restorer is not None else None,
            "idle_since": datetime.fromtimestamp(idle_since, tz=timezone.utc).isoformat() if idle_since else None,
            "ready_for_removal": ready,
            "errors": list(self._reconciler.last_errors),
        }

    # ── operations ───────────────────────────────────────────────────────────
    def stop(self, by: Optional[str]) -> None:
        if self.lifecycle() in (STOPPED_VE, STOPPING_VE):
            return
        if self.lifecycle() == DELETING_VE:
            raise ConflictError("the VE is being deleted")
        self._record(STOPPING_VE, f"stop requested by {by}")
        self._run(lambda: self._wind_down(remove=False))

    def prepare_delete(self, by: Optional[str]) -> None:
        if self.lifecycle() == DELETING_VE:
            return
        self._record(DELETING_VE, f"delete requested by {by}")
        self._run(lambda: self._wind_down(remove=True))

    def start(self, by: Optional[str]) -> None:
        if self.lifecycle() not in (STOPPED_VE, FAILED_VE):
            raise ConflictError(f"the VE is {self.lifecycle()}, not stopped")
        self._record(STARTING_VE, f"start requested by {by}")
        self._run(self._bring_up)

    def wait(self, timeout: Optional[float] = None) -> None:
        """For tests and orderly shutdown: wait for the current operation."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _run(self, target: Callable[[], None]) -> None:
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _wind_down(self, remove: bool) -> None:
        self._reconciler.pause(True)
        working = self._fleet.in_states(RUNNING, CREATING, STARTING, DRAINING)
        for endpoint in working:
            self._ops.drain(endpoint.endpoint_id)
        deadline = self._now_fn() + self._drain_timeout_s
        while self._now_fn() < deadline:
            busy = [e for e in working if self._jobs.active_jobs(e.endpoint_id) or (self._ops.drain(e.endpoint_id) or 0)]
            if not busy:
                break
            self._sleep_fn(self._poll_s)
        for endpoint in working:
            for job_id in self._jobs.active_jobs(endpoint.endpoint_id):
                self._ops.cancel_job(endpoint.endpoint_id, job_id)   # billed for the time it used
        if remove:
            for endpoint in self._fleet.all():
                if endpoint.state != "removed":
                    self._ops.remove(endpoint.endpoint_id)
                    self._fleet.record(endpoint.endpoint_id, "removed", "VE deleted")
            self._before_settled()
            with self._lock:
                self._ready_for_removal = True
        else:
            self._reconciler.stop_all("VE stopped")
            self._before_settled()
            self._record(STOPPED_VE, "all endpoints stopped")

    def _bring_up(self) -> None:
        self._reconciler.start_all("VE starting")
        self._reconciler.pause(False)
        needed = max(1, self._settings.current().min_endpoints)
        deadline = self._now_fn() + self._start_timeout_s
        while len(self._fleet.in_states(RUNNING)) < needed and self._now_fn() < deadline:
            self._reconciler.tick()
            self._sleep_fn(self._poll_s)
        if len(self._fleet.in_states(RUNNING)) < 1:
            self._record(FAILED_VE, f"no endpoint came up within {self._start_timeout_s:g} s")
            return
        self._record(RESTORING_VE, "re-registering functions")
        if self._restorer is not None:
            self._restorer.restore()
        self._record(RUNNING_VE, "restored")
