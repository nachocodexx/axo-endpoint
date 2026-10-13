from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from axo_vem.application.virtual_environment.endpoint_ops import EndpointOps
from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.domain.virtual_environment.fleet import (
    CREATING,
    DRAINING,
    FAILED,
    PRESENT,
    REMOVED,
    REMOVING,
    RUNNING,
    SLEEPING,
    STARTING,
    STOPPED,
    ManagedEndpoint,
)

_GAVE_UP = "gave up"


def _epoch(iso: str, fallback: float = 0.0) -> float:
    """ISO time to epoch; accepts the trailing "Z" pydantic writes for UTC
    (Python 3.10's fromisoformat doesn't)."""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return fallback


class Reconciler:
    """Keeps this VE's endpoints matching its settings. One tick:

    1. creating/starting endpoints become running once they report in, or
       failed if they don't within ``startup_timeout_s``;
    2. a running endpoint whose container stopped, or that stopped
       answering, is failed (an unplanned failure -- planned ones go through
       sleeping/draining instead);
    3. failed endpoints (auto-replace on): first a restart, then a
       replacement with the same size, giving up after ``max_failures``
       consecutive failures of that slot;
    4. draining endpoints finish their jobs (or hit ``drain_timeout_s``),
       then are removed -- scale-down never kills a running job;
    5. present endpoints are brought to target_endpoints: new ones get the
       VE's endpoint size; extra ones are removed, sleeping ones first;
    6. running endpoints idle for endpoint_idle_seconds go to sleep, never
       below min_endpoints;
    7. jobs waiting longer than wake_wait_seconds wake ONE sleeping endpoint
       (immediately when nothing is running).

    API commands (create / remove / restart / wake) go through the same lock
    as the tick, so they never race it."""

    def __init__(
        self,
        settings: VeSettingsService,
        fleet: FleetService,
        ops: EndpointOps,
        jobs: JobTracker,
        startup_timeout_s: float = 180.0,
        drain_timeout_s: float = 600.0,
        stable_after_s: float = 600.0,
        max_failures: int = 3,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._fleet = fleet
        self._ops = ops
        self._jobs = jobs
        self._startup_timeout_s = startup_timeout_s
        self._drain_timeout_s = drain_timeout_s
        self._stable_after_s = stable_after_s
        self._max_failures = max_failures
        self._now_fn = now_fn
        self._lock = threading.RLock()
        self._paused = False          # True while the VE is stopping / stopped
        self.last_errors: List[str] = []
        self._after_tick: List[Callable[[], None]] = []

    # ── the loop ─────────────────────────────────────────────────────────────
    def tick(self) -> None:
        with self._lock:
            self.last_errors = []
            now = self._now_fn()
            self._progress_starting(now)
            self._detect_failures(now)
            if self._paused:
                return
            self._handle_failed()
            self._progress_draining(now)
            self._scale_to_target()
            self._sleep_idle(now)
            self._wake_if_waiting()

    def on_after_tick(self, hook: Callable[[], None]) -> None:
        self._after_tick.append(hook)

    def run_forever(self, stop_event: threading.Event, interval_s: float = 2.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.tick()
                for hook in self._after_tick:
                    hook()
            except Exception as exc:  # one bad tick must not kill the loop
                self.last_errors.append(repr(exc))

    def pause(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused

    # ── commands (UI → CP → VEM) ─────────────────────────────────────────────
    def create(
        self, count: int, cpu_cores: float, memory_mb: int, by: Optional[str], reason: Optional[str] = None,
    ) -> List[ManagedEndpoint]:
        """"Add N endpoints of size X": target + N, and those N are created
        right away with that size (the plan limit is checked by the settings)."""
        if count < 1:
            raise ConflictError("count must be ≥ 1")
        with self._lock:
            live = [e for e in self._fleet.all() if e.state in PRESENT]
            problem = self._settings.limits.quota_problem(
                sum(e.cpu_cores for e in live) + count * cpu_cores,
                sum(e.memory_mb for e in live) + count * memory_mb,
            )
            if problem:
                raise ConflictError(f"can't add {count} endpoint(s) of {cpu_cores:g} CPU / "
                                    f"{memory_mb / 1024:g} GB: {problem}")
            settings = self._settings.current()
            self._settings.change(
                {"target_endpoints": settings.target_endpoints + count},
                changed_by_type="user", changed_by_id=by, reason=reason or f"create {count} endpoint(s)",
            )
            created = [self._launch(cpu_cores, memory_mb, reason or f"created by {by}") for _ in range(count)]
            return [e for e in created if e is not None]

    def remove(self, endpoint_id: str, by: Optional[str], reason: Optional[str] = None) -> ManagedEndpoint:
        """Target − 1, and THIS endpoint is the one that goes (Draining first)."""
        with self._lock:
            endpoint = self._require(endpoint_id)
            if endpoint.state in (DRAINING, REMOVING, REMOVED):
                return endpoint
            settings = self._settings.current()
            new_target = max(settings.min_endpoints, settings.target_endpoints - 1)
            if endpoint.state in PRESENT and new_target == settings.target_endpoints:
                raise ConflictError(f"can't go below min_endpoints ({settings.min_endpoints})")
            if new_target != settings.target_endpoints:
                self._settings.change(
                    {"target_endpoints": new_target}, changed_by_type="user", changed_by_id=by,
                    reason=reason or f"remove {endpoint_id}",
                )
            return self._retire(endpoint, reason or f"removed by {by}")

    def restart(self, endpoint_id: str, by: Optional[str]) -> ManagedEndpoint:
        with self._lock:
            endpoint = self._require(endpoint_id)
            error = self._ops.restart(endpoint_id)
            if error:
                raise ConflictError(f"restart failed: {error}")
            self._jobs.touch(endpoint_id)
            return self._fleet.record(endpoint_id, STARTING, f"restarted by {by}")

    def wake_one(self, reason: str) -> Optional[str]:
        """Wakes one sleeping endpoint unless one is already waking. Returns
        the endpoint being woken (or already waking), or None if none sleeps."""
        with self._lock:
            waking = [e for e in self._fleet.in_states(STARTING) if (e.reason or "").startswith("waking")]
            if waking:
                return waking[0].endpoint_id
            for sleeper in reversed(self._fleet.in_states(SLEEPING)):  # most recently slept first
                error = self._ops.start(sleeper.endpoint_id)
                if error:
                    self.last_errors.append(f"waking {sleeper.endpoint_id}: {error}")
                    continue
                self._jobs.touch(sleeper.endpoint_id)
                self._fleet.record(sleeper.endpoint_id, STARTING, f"waking: {reason}")
                return sleeper.endpoint_id
            return None

    # ── steps ────────────────────────────────────────────────────────────────
    def _progress_starting(self, now: float) -> None:
        for endpoint in self._fleet.in_states(CREATING, STARTING):
            since = _epoch(endpoint.since, now)
            reported = self._ops.reported(endpoint.endpoint_id)
            if reported is not None and reported[0] == "running" and reported[1] >= since - 1.0:
                self._jobs.touch(endpoint.endpoint_id)
                self._fleet.record(endpoint.endpoint_id, RUNNING, "reported in")
                continue
            container = self._ops.container_state(endpoint.endpoint_id)
            if container in (None, "exited", "dead") and now - since > 5.0:
                self._fail(endpoint, f"container {container or 'missing'} while {endpoint.state}")
            elif now - since > self._startup_timeout_s:
                self._fail(endpoint, f"didn't report in within {self._startup_timeout_s:g} s")

    def _detect_failures(self, now: float) -> None:
        for endpoint in self._fleet.in_states(RUNNING):
            container = self._ops.container_state(endpoint.endpoint_id)
            reported = self._ops.reported(endpoint.endpoint_id)
            if container != "running":
                self._fail(endpoint, f"container {container or 'missing'}")
            elif reported is not None and reported[0] == "unreachable":
                self._fail(endpoint, "stopped answering")
            elif endpoint.failures and now - _epoch(endpoint.since, now) > self._stable_after_s:
                self._fleet.record(endpoint.endpoint_id, RUNNING, "stable again", failures=0)

    def _handle_failed(self) -> None:
        if not self._settings.current().auto_replace_failed:
            return
        for endpoint in self._fleet.in_states(FAILED):
            if (endpoint.reason or "").startswith(_GAVE_UP):
                continue
            if endpoint.failures >= self._max_failures:
                self._fleet.record(
                    endpoint.endpoint_id, FAILED,
                    f"{_GAVE_UP} after {endpoint.failures} failures in a row",
                )
                continue
            if endpoint.failures <= 1 and not self._ops.restart(endpoint.endpoint_id):
                self._jobs.touch(endpoint.endpoint_id)
                self._fleet.record(endpoint.endpoint_id, STARTING, "restarting after a failure")
                continue
            # Restart didn't help (or failed): replace it with the same size.
            self._ops.remove(endpoint.endpoint_id)
            self._fleet.record(endpoint.endpoint_id, REMOVED, "replaced after failing")
            self._launch(
                endpoint.cpu_cores, endpoint.memory_mb, f"replacing {endpoint.endpoint_id}",
                failures=endpoint.failures, replaces=endpoint.endpoint_id,
            )

    def _progress_draining(self, now: float) -> None:
        for endpoint in self._fleet.in_states(DRAINING):
            reported_active = self._ops.drain(endpoint.endpoint_id)
            active = max(reported_active or 0, len(self._jobs.active_jobs(endpoint.endpoint_id)))
            timed_out = now - _epoch(endpoint.since, now) > self._drain_timeout_s
            if active == 0 or timed_out:
                self._finish_removal(endpoint, "drained" if active == 0 else f"drain timed out with {active} job(s)")

    def _scale_to_target(self) -> None:
        settings = self._settings.current()
        # A failed endpoint keeps holding its slot -- being restarted /
        # replaced, or given up on (the VE shows Degraded until someone
        # removes it). Otherwise a slot that keeps failing would be
        # recreated forever.
        failed = self._fleet.in_states(FAILED)
        present = self._fleet.in_states(*PRESENT)
        count = len(present) + len(failed)
        if count < settings.target_endpoints:
            for _ in range(settings.target_endpoints - count):
                self._launch(
                    settings.new_endpoint_cpu_cores, settings.new_endpoint_memory_mb,
                    f"reaching target {settings.target_endpoints}",
                )
        elif count > settings.target_endpoints:
            for endpoint in self._removal_order(present)[: count - settings.target_endpoints]:
                self._retire(endpoint, f"down to target {settings.target_endpoints}")

    def _sleep_idle(self, now: float) -> None:
        settings = self._settings.current()
        if settings.endpoint_idle_seconds is None or self._jobs.waiting_count():
            return
        running = self._fleet.in_states(RUNNING)
        awake = len(running) + len(self._fleet.in_states(CREATING, STARTING))
        for endpoint in sorted(running, key=lambda e: self._jobs.last_activity(e.endpoint_id) or 0.0):
            if awake <= settings.min_endpoints:
                break
            if self._jobs.active_jobs(endpoint.endpoint_id):
                continue
            last = self._jobs.last_activity(endpoint.endpoint_id) or _epoch(endpoint.since, now)
            idle_for = now - last
            if idle_for < settings.endpoint_idle_seconds:
                continue
            error = self._ops.halt(endpoint.endpoint_id)
            if error:
                self.last_errors.append(f"sleeping {endpoint.endpoint_id}: {error}")
                continue
            self._fleet.record(endpoint.endpoint_id, SLEEPING, f"idle {idle_for:.0f} s")
            awake -= 1

    def _wake_if_waiting(self) -> None:
        settings = self._settings.current()
        if not self._jobs.waiting_count() or not self._fleet.in_states(SLEEPING):
            return
        waited = self._jobs.oldest_wait_seconds()
        nothing_running = not self._fleet.in_states(RUNNING)
        if nothing_running or waited >= settings.wake_wait_seconds:
            self.wake_one(f"jobs waiting {waited:.1f} s")

    # ── helpers ──────────────────────────────────────────────────────────────
    def _launch(
        self, cpu_cores: float, memory_mb: int, reason: str, failures: int = 0, replaces: Optional[str] = None,
    ) -> Optional[ManagedEndpoint]:
        created = self._ops.create(cpu_cores, memory_mb)
        if created.is_err:
            self.last_errors.append(f"creating an endpoint: {created.unwrap_err()}")
            return None
        endpoint_id = created.unwrap()
        self._jobs.touch(endpoint_id)
        return self._fleet.record(
            endpoint_id, CREATING, reason, cpu_cores=cpu_cores, memory_mb=memory_mb,
            failures=failures, replaces=replaces,
        )

    def _fail(self, endpoint: ManagedEndpoint, reason: str) -> None:
        self._fleet.record(endpoint.endpoint_id, FAILED, reason, failures=endpoint.failures + 1)

    def _retire(self, endpoint: ManagedEndpoint, reason: str) -> ManagedEndpoint:
        """Remove one endpoint: running ones drain first; ones doing nothing
        (sleeping, starting, failed, stopped) go straight away."""
        if endpoint.state == RUNNING:
            self._ops.drain(endpoint.endpoint_id)
            return self._fleet.record(endpoint.endpoint_id, DRAINING, reason)
        return self._finish_removal(endpoint, reason)

    def _finish_removal(self, endpoint: ManagedEndpoint, reason: str) -> ManagedEndpoint:
        self._fleet.record(endpoint.endpoint_id, REMOVING, reason)
        error = self._ops.remove(endpoint.endpoint_id)
        if error:
            self.last_errors.append(f"removing {endpoint.endpoint_id}: {error}")
        return self._fleet.record(endpoint.endpoint_id, REMOVED, reason)

    def _removal_order(self, present: List[ManagedEndpoint]) -> List[ManagedEndpoint]:
        """Who goes first on scale-down: sleeping, then still starting, then
        running with no jobs, then busy -- newest first within each group."""
        def rank(e: ManagedEndpoint) -> tuple:
            if e.state == SLEEPING:
                group = 0
            elif e.state in (CREATING, STARTING):
                group = 1
            elif not self._jobs.active_jobs(e.endpoint_id):
                group = 2
            else:
                group = 3
            return group, -_epoch(e.since)
        return sorted(present, key=rank)

    def _require(self, endpoint_id: str) -> ManagedEndpoint:
        endpoint = self._fleet.get(endpoint_id)
        if endpoint is None:
            raise NotFoundError(f"endpoint {endpoint_id} is not managed by this VE")
        return endpoint

    # ── stop / start of the whole VE (used by the VE lifecycle) ─────────────
    def endpoints_by_state(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for endpoint in self._fleet.all():
            counts[endpoint.state] = counts.get(endpoint.state, 0) + 1
        return counts

    def stop_all(self, reason: str) -> None:
        """VE stop, after draining: every container halted but kept."""
        with self._lock:
            for endpoint in self._fleet.in_states(*PRESENT, DRAINING, FAILED):
                self._ops.halt(endpoint.endpoint_id)
                self._fleet.record(endpoint.endpoint_id, STOPPED, reason)

    def start_all(self, reason: str) -> None:
        """VE start: bring back every stopped endpoint."""
        with self._lock:
            for endpoint in self._fleet.in_states(STOPPED):
                error = self._ops.start(endpoint.endpoint_id)
                if error:
                    self._fail(endpoint, f"couldn't start: {error}")
                    continue
                self._jobs.touch(endpoint.endpoint_id)
                self._fleet.record(endpoint.endpoint_id, STARTING, reason)
