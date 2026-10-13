"""Stop / start / prepare-delete of the whole VE, plus lifecycle and health."""
from datetime import datetime, timezone

import pytest

from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.lifecycle_service import VeLifecycleService
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.restorer import RestoreProgress
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.domain.virtual_environment.settings import PlanLimits

from .test_reconciler import Clock, FakeOps, _Log


class _Restorer:
    def __init__(self):
        self.calls = 0

    def restore(self):
        self.calls += 1
        return RestoreProgress(total=2, restored=2)

    def progress(self):
        return RestoreProgress(total=2, restored=2 if self.calls else 0)


@pytest.fixture
def ve():
    clock = Clock()
    log = _Log(clock)
    settings = VeSettingsService("ve", PlanLimits(max_endpoints=5), log)
    fleet = FleetService("ve", log, now_fn=clock)
    ops = FakeOps(clock)
    jobs = JobTracker(now_fn=clock)
    reconciler = Reconciler(settings, fleet, ops, jobs, now_fn=clock)
    restorer = _Restorer()

    def tick_sleep(seconds):
        """While an operation waits, time passes and starting endpoints report in."""
        clock.advance(seconds)
        ops.report_in(*[e.endpoint_id for e in fleet.in_states("starting", "creating")])

    service = VeLifecycleService(
        settings, fleet, reconciler, ops, jobs, log, restorer=restorer,
        drain_timeout_s=30, start_timeout_s=60, poll_s=1, now_fn=clock, sleep_fn=tick_sleep,
    )
    settings.change({"target_endpoints": 2})
    reconciler.tick()
    clock.advance(1)
    ops.report_in("ep-1", "ep-2")
    reconciler.tick()
    service.after_tick()
    return clock, settings, fleet, ops, jobs, reconciler, service, restorer, log


def _job(event_type, job_id, endpoint_id, clock):
    return event_type, {"job_id": job_id, "function_id": "f", "endpoint_id": endpoint_id,
                        "created_at": datetime.fromtimestamp(clock(), tz=timezone.utc).isoformat()}


def test_a_new_ve_becomes_running_and_healthy(ve):
    *_, service, _restorer, _log = ve
    status = service.status()
    assert status["lifecycle"] == "Running" and status["health"] == "Healthy"
    assert status["endpoints"] == {"running": 2}


def test_stop_drains_waits_for_jobs_then_keeps_the_containers(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, _r, log = ve
    jobs.observe(*_job(models.JOB_STARTED, "j1", "ep-1", clock))
    ops.drained["ep-1"] = 1
    waits = []

    def job_finishes_during_the_drain(seconds):
        waits.append(seconds)
        clock.advance(seconds)
        jobs.observe(*_job(models.JOB_COMPLETED, "j1", "ep-1", clock))
        ops.drained["ep-1"] = 0

    service._sleep_fn = job_finishes_during_the_drain
    service.stop(by="luis")
    service.wait(5)

    assert waits                                                      # it did wait for the job

    assert service.lifecycle() == "Stopped"
    assert {e.state for e in fleet.all()} == {"stopped"}
    assert set(ops.containers.values()) == {"exited"}                 # kept, not removed
    assert getattr(ops, "cancelled", []) == []
    assert service.status()["health"] is None                        # no health while stopped


def test_stop_cancels_jobs_still_running_at_the_drain_timeout(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, _r, _log = ve
    jobs.observe(*_job(models.JOB_STARTED, "stuck", "ep-2", clock))
    service.stop(by="luis")
    service.wait(5)
    assert ops.cancelled == [("ep-2", "stuck")]
    assert service.lifecycle() == "Stopped"


def test_start_brings_endpoints_back_restores_functions_then_runs(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, restorer, _log = ve
    service.stop(by="luis")
    service.wait(5)

    service.start(by="luis")
    service.wait(5)

    assert service.lifecycle() == "Running"
    assert restorer.calls == 1
    assert {e.state for e in fleet.all()} == {"running"}
    assert service.status()["restore"] == {"total": 2, "restored": 2, "failed": [], "running": False}


def test_start_is_refused_unless_stopped(ve):
    *_, service, _r, _log = ve
    with pytest.raises(ConflictError, match="not stopped"):
        service.start(by="luis")


def test_prepare_delete_removes_every_endpoint_and_signals_ready(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, _r, _log = ve
    service.prepare_delete(by="owner")
    service.wait(5)
    assert service.lifecycle() == "Deleting"
    assert ops.containers == {}
    assert service.status()["ready_for_removal"] is True


def test_a_restarted_vem_remembers_it_was_stopped(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, restorer, log = ve
    service.stop(by="luis")
    service.wait(5)
    again = VeLifecycleService(settings, fleet, reconciler, ops, jobs, log, now_fn=clock)
    again.load(log)
    assert again.lifecycle() == "Stopped"


def test_health_degraded_on_failure_and_scaling_while_moving(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, _r, _log = ve
    settings.change({"auto_replace_failed": False})
    ops.containers["ep-2"] = "exited"
    reconciler.tick()
    assert service.status()["health"] == "Degraded"
    settings.change({"target_endpoints": 3})
    reconciler.tick()
    assert service.status()["lifecycle"] == "Scaling"


def test_idle_since_is_reported_for_the_free_plan_sleep(ve):
    clock, settings, fleet, ops, jobs, reconciler, service, _r, _log = ve
    assert service.status()["idle_since"] is not None
    jobs.observe(*_job(models.JOB_STARTED, "j", "ep-1", clock))
    service.after_tick()
    assert service.status()["idle_since"] is None
