from axo_shared.protocol import Command

from axo_endpoint.core.events import InMemoryEventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_endpoint.core.storage import InMemoryStorageBackend
from axo_endpoint.service.handlers import EndpointDrainHandler, JobSubmitHandler
from axo_endpoint.service.handlers.job_forward import JobForwardHandler


def _cmd(op, **envelope):
    return Command(operation=op, content_type="application/json", envelope=envelope)


def test_drain_sets_the_flag_and_reports_running_jobs():
    state = {"draining": False}
    handler = EndpointDrainHandler(set_draining=lambda d: state.update(draining=d), active_job_count=lambda: 2)

    result = handler.handle(_cmd("ENDPOINT_DRAIN"))
    assert result.ok and result.metadata == {"draining": True, "active_jobs": 2}
    assert state["draining"] is True

    handler.handle(_cmd("ENDPOINT_DRAIN", draining=False))
    assert state["draining"] is False


def test_a_draining_endpoint_refuses_new_and_forwarded_jobs():
    bus = InMemoryEventBus()
    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=bus)
    common = dict(runtime=None, results=InMemoryStorageBackend(), event_bus=bus, function_registry=registry)

    submit = JobSubmitHandler(**common, is_draining=lambda: True)
    forward = JobForwardHandler(**common, is_draining=lambda: True)

    assert submit.handle(_cmd("JOB_SUBMIT", function_id="f", function_version=1)).error_name == "ENDPOINT_DRAINING"
    assert forward.handle(_cmd("JOB_FORWARD", job_id="j", function_id="f", function_version=1)).error_name == "ENDPOINT_DRAINING"
