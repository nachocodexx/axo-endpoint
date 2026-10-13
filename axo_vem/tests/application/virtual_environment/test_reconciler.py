"""The endpoint lifecycle, driven tick by tick against a fake world."""
import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from option import Err, Ok

from axo_vem.application.virtual_environment.endpoint_ops import EndpointOps
from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.domain.virtual_environment.settings import PlanLimits


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@dataclass
class _Record:
    type: str
    data: bytes


class _Log:
    def __init__(self, clock):
        self.clock = clock
        self.streams = {}

    def append_to_stream(self, stream, event_type, data):
        # stamp events with the fake clock, like the real envelope does with wall time
        data = {**data, "created_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc).isoformat()}
        self.streams.setdefault(stream, []).append(_Record(event_type, json.dumps(data).encode()))

    def get_stream(self, stream):
        return self.streams[stream]


class FakeOps(EndpointOps):
    """Containers + what each endpoint reports, all controllable by the test."""

    def __init__(self, clock):
        self.clock = clock
        self.ids = (f"ep-{i}" for i in itertools.count(1))
        self.containers = {}     # id -> "running" | "exited"
        self.reports = {}        # id -> (status, last_seen)
        self.sizes = {}
        self.drained = {}        # id -> active jobs it reports
        self.fail_create = False

    def create(self, cpu_cores, memory_mb):
        if self.fail_create:
            return Err("no capacity")
        endpoint_id = next(self.ids)
        self.containers[endpoint_id] = "running"
        self.sizes[endpoint_id] = (cpu_cores, memory_mb)
        return Ok(endpoint_id)

    def report_in(self, *endpoint_ids):
        for endpoint_id in endpoint_ids:
            self.reports[endpoint_id] = ("running", self.clock())

    def container_state(self, endpoint_id):
        return self.containers.get(endpoint_id)

    def reported(self, endpoint_id):
        return self.reports.get(endpoint_id)

    def halt(self, endpoint_id):
        self.containers[endpoint_id] = "exited"

    def start(self, endpoint_id):
        self.containers[endpoint_id] = "running"

    def restart(self, endpoint_id):
        self.containers[endpoint_id] = "running"

    def remove(self, endpoint_id):
        self.containers.pop(endpoint_id, None)

    def drain(self, endpoint_id, draining=True):
        return self.drained.get(endpoint_id, 0)

    def cancel_job(self, endpoint_id, job_id):
        self.cancelled = getattr(self, "cancelled", []) + [(endpoint_id, job_id)]


@pytest.fixture
def world():
    clock = Clock()
    log = _Log(clock)
    settings = VeSettingsService("ve", PlanLimits(max_endpoints=10), log)
    fleet = FleetService("ve", log, now_fn=clock)
    ops = FakeOps(clock)
    jobs = JobTracker(now_fn=clock)
    reconciler = Reconciler(settings, fleet, ops, jobs, startup_timeout_s=60, drain_timeout_s=300, now_fn=clock)
    return clock, settings, fleet, ops, jobs, reconciler, log


def states(fleet):
    return {e.endpoint_id: e.state for e in fleet.all()}


def job(event_type, job_id, endpoint_id, clock):
    return event_type, {
        "job_id": job_id, "function_id": "f", "endpoint_id": endpoint_id,
        "created_at": datetime.fromtimestamp(clock(), tz=timezone.utc).isoformat(),
    }


def bring_up(clock, ops, reconciler, *ids):
    clock.advance(1)
    ops.report_in(*ids)
    reconciler.tick()


def test_reaches_the_target_with_the_ve_endpoint_size(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"target_endpoints": 2, "new_endpoint_cpu_cores": 2.0, "new_endpoint_memory_mb": 4096})
    reconciler.tick()
    assert states(fleet) == {"ep-1": "creating", "ep-2": "creating"}
    assert ops.sizes["ep-1"] == (2.0, 4096)

    bring_up(clock, ops, reconciler, "ep-1", "ep-2")
    assert states(fleet) == {"ep-1": "running", "ep-2": "running"}


def test_an_endpoint_that_never_reports_in_fails_after_the_timeout(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"auto_replace_failed": False})
    reconciler.tick()
    clock.advance(61)
    reconciler.tick()
    assert fleet.get("ep-1").state == "failed"


def test_failed_endpoint_is_restarted_then_replaced_then_given_up(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1")

    ops.containers["ep-1"] = "exited"          # 1st failure -> restart, same endpoint
    reconciler.tick()
    assert fleet.get("ep-1").state == "starting" and fleet.get("ep-1").failures == 1
    bring_up(clock, ops, reconciler, "ep-1")

    ops.containers["ep-1"] = "exited"          # 2nd failure -> replaced by a new endpoint, same size
    reconciler.tick()
    assert fleet.get("ep-1").state == "removed"
    assert fleet.get("ep-2").replaces == "ep-1" and fleet.get("ep-2").failures == 2
    bring_up(clock, ops, reconciler, "ep-2")

    ops.containers["ep-2"] = "exited"          # 3rd failure -> give up, keep it failed
    reconciler.tick()
    reconciler.tick()
    assert fleet.get("ep-2").state == "failed" and fleet.get("ep-2").reason.startswith("gave up")
    assert "ep-3" not in states(fleet)


def test_scale_down_drains_running_endpoints_and_never_kills_a_job(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"target_endpoints": 2})
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1", "ep-2")
    jobs.observe(*job(models.JOB_STARTED, "j1", "ep-1", clock))   # ep-1 is busy

    settings.change({"target_endpoints": 1})
    reconciler.tick()
    assert fleet.get("ep-2").state == "draining"                  # the idle one is picked, and drains
    assert fleet.get("ep-1").state == "running"
    reconciler.tick()
    assert fleet.get("ep-2").state == "removed"                   # nothing running on it: removed

    settings.change({"min_endpoints": 0, "target_endpoints": 0})
    ops.drained["ep-1"] = 1
    reconciler.tick()
    assert fleet.get("ep-1").state == "draining"                  # busy: finishes its job first
    jobs.observe(*job(models.JOB_COMPLETED, "j1", "ep-1", clock))
    ops.drained["ep-1"] = 0
    reconciler.tick()
    assert fleet.get("ep-1").state == "removed"


def test_idle_endpoints_sleep_above_min_and_wake_only_when_jobs_wait(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"target_endpoints": 3, "endpoint_idle_seconds": 600, "wake_wait_seconds": 2})
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1", "ep-2", "ep-3")

    clock.advance(601)
    reconciler.tick()
    sleeping = [e for e in fleet.all() if e.state == "sleeping"]
    assert len(sleeping) == 2                                      # min_endpoints (1) stays awake
    assert reconciler.tick() is None and len(fleet.in_states("sleeping")) == 2   # not recreated

    jobs.observe(*job(models.JOB_QUEUED, "j1", "ep-x", clock))
    clock.advance(1)
    reconciler.tick()
    assert len(fleet.in_states("sleeping")) == 2                   # waited only 1 s
    clock.advance(1.5)
    reconciler.tick()
    assert len(fleet.in_states("sleeping")) == 1                   # waited 2.5 s -> wake ONE
    assert len(fleet.in_states("starting")) == 1


def test_with_nothing_running_the_first_waiting_job_wakes_one_immediately(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"min_endpoints": 0, "endpoint_idle_seconds": 60})
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1")
    clock.advance(61)
    reconciler.tick()
    assert fleet.get("ep-1").state == "sleeping"

    jobs.observe(*job(models.JOB_QUEUED, "j1", "ep-1", clock))
    reconciler.tick()
    assert fleet.get("ep-1").state == "starting"


def test_create_command_raises_the_target_and_uses_the_given_size(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    reconciler.tick()
    created = reconciler.create(2, cpu_cores=4.0, memory_mb=8192, by="luis")
    assert [e.endpoint_id for e in created] == ["ep-2", "ep-3"]
    assert ops.sizes["ep-3"] == (4.0, 8192)
    assert settings.current().target_endpoints == 3
    reconciler.tick()
    assert len(fleet.in_states("creating")) == 3                  # target met, nothing extra


def test_remove_command_takes_that_endpoint_and_respects_min(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.change({"target_endpoints": 2})
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1", "ep-2")
    reconciler.remove("ep-1", by="luis")
    assert settings.current().target_endpoints == 1
    assert fleet.get("ep-1").state == "draining"
    reconciler.tick()
    assert fleet.get("ep-1").state == "removed"
    with pytest.raises(ConflictError, match="min_endpoints"):
        reconciler.remove("ep-2", by="luis")


def test_the_fleet_survives_a_vem_restart(world):
    clock, settings, fleet, ops, jobs, reconciler, log = world
    settings.change({"target_endpoints": 2})
    reconciler.tick()
    bring_up(clock, ops, reconciler, "ep-1", "ep-2")

    rebuilt = FleetService("ve", log, now_fn=clock)
    assert rebuilt.load(log) > 0
    assert states(rebuilt) == {"ep-1": "running", "ep-2": "running"}


def test_creation_errors_are_reported_and_retried(world):
    clock, settings, fleet, ops, jobs, reconciler, _ = world
    ops.fail_create = True
    reconciler.tick()
    assert reconciler.last_errors == ["creating an endpoint: no capacity"]
    ops.fail_create = False
    reconciler.tick()
    assert fleet.get("ep-1").state == "creating"


def test_adding_endpoints_beyond_the_quota_is_refused(world):
    from axo_vem.domain.errors import ConflictError

    clock, settings, fleet, ops, jobs, reconciler, _ = world
    settings.set_limits(PlanLimits(max_endpoints=10, quota_cpu_cores=3.0, quota_memory_mb=8192))
    reconciler.tick()                                             # ep-1 at the default 1 CPU
    reconciler.create(1, cpu_cores=2.0, memory_mb=2048, by="alice")
    with pytest.raises(ConflictError) as err:
        reconciler.create(1, cpu_cores=2.0, memory_mb=2048, by="alice")
    assert "quota is 3 CPU" in str(err.value)
