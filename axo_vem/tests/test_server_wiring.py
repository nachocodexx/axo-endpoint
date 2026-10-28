"""The small pieces of the VEM Server that join the use cases to the VE's own
settings -- each run here for real (the use case tests pass their own
stand-ins, so a slip in this glue would only show on the platform)."""
from types import SimpleNamespace

import pytest

from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import InvalidRequestError
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.server import Server

from tests.application.virtual_environment.test_reconciler import Clock, _Log


def _server_with(limits: PlanLimits):
    settings = VeSettingsService("ve", limits, _Log(Clock()))
    return SimpleNamespace(_ve_settings=settings)


FREE = PlanLimits(max_endpoints=1, runner_cpu_cores=0.5, runner_memory_mb=512,
                  runner_max_cpu_cores=0.5, runner_max_memory_mb=512)


def test_a_simple_function_registers_untouched():
    spec = {"type": "process"}
    assert Server._runner_size(_server_with(FREE), spec) == spec


def test_a_container_function_gets_the_plans_runner_size():
    out = Server._runner_size(_server_with(FREE), {"type": "container", "python_version": "3.12"})
    assert out["cpu_limit"] == 0.5 and out["memory_limit_bytes"] == 512 * 1024 * 1024


def test_a_runner_bigger_than_the_plan_is_refused():
    with pytest.raises(InvalidRequestError):
        Server._runner_size(_server_with(FREE), {"type": "container", "cpu_limit": 2.0})
