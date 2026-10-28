import pytest
from option import Err, Ok

from axo_shared.errors import AxoError
from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import CommandResult

from axo_vem.application.compute import register_function as register_function_module
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.register_function import RegisterFunctionUseCase
from axo_vem.domain.compute.endpoint import Endpoint
from axo_vem.domain.errors import ConflictError, UpstreamTimeoutError


class _FakeEndpointRepository:
    """list_all() mirrors the real Mongo query's newest-first order."""

    def __init__(self, endpoints=None):
        self._endpoints = endpoints or []

    def list_all(self):
        return list(self._endpoints)

    def get(self, endpoint_id):
        return next((e for e in self._endpoints if e.endpoint_id == endpoint_id), None)


def _endpoint(endpoint_id="n0", status="running", seen="2026-01-01T00:00:00"):
    return Endpoint(endpoint_id=endpoint_id, router_bind="tcp://0.0.0.0:5555", status=status, last_seen_at=seen)


def _use_case(endpoints=None):
    return RegisterFunctionUseCase("ve1", EndpointPicker(_FakeEndpointRepository(endpoints)), 0.3)


def test_execute_raises_conflict_when_no_running_endpoint():
    use_case = _use_case(endpoints=[_endpoint(status="stopped")])
    with pytest.raises(ConflictError, match="no running endpoint"):
        use_case.execute(current_user_id="user-1", name="add", code=b"...")


def test_execute_picks_the_most_recently_seen_running_endpoint(monkeypatch):
    newer = _endpoint("n0", seen="2026-01-02T00:00:00")
    older = _endpoint("n1", seen="2026-01-01T00:00:00")
    use_case = _use_case(endpoints=[newer, older])
    captured = {}

    def fake_send_command(rpc_uri, command, timeout):
        captured["rpc_uri"], captured["command"] = rpc_uri, command
        return Ok(CommandResult(ok=True, metadata={"function_id": "add", "version": 1}))

    monkeypatch.setattr(register_function_module, "send_command", fake_send_command)
    result = use_case.execute(current_user_id="user-1", name="add", code=b"...")

    assert result["endpoint_id"] == "n0"
    assert captured["rpc_uri"] == "tcp://n0:5555"
    # the function id is scoped to this VEM's own VE
    assert captured["command"].envelope["function_id"] == compute_function_id("user-1", "ve1", "add")


def test_execute_raises_upstream_timeout_on_send_error(monkeypatch):
    use_case = _use_case(endpoints=[_endpoint()])
    monkeypatch.setattr(
        register_function_module, "send_command",
        lambda rpc_uri, command, timeout: Err(AxoError("timed out")),
    )
    with pytest.raises(UpstreamTimeoutError):
        use_case.execute(current_user_id="user-1", name="add", code=b"...")


def test_execute_raises_conflict_on_command_rejection(monkeypatch):
    use_case = _use_case(endpoints=[_endpoint()])
    monkeypatch.setattr(
        register_function_module, "send_command",
        lambda rpc_uri, command, timeout: Ok(CommandResult(ok=False, error="storage failure")),
    )
    with pytest.raises(ConflictError, match="storage failure"):
        use_case.execute(current_user_id="user-1", name="add", code=b"...")


def test_picker_skips_sleeping_endpoints_and_wakes_one_when_nothing_runs():
    from axo_vem.domain.virtual_environment.fleet import ManagedEndpoint

    class _Fleet:
        def __init__(self):
            self.state = "sleeping"

        def all(self):
            return [ManagedEndpoint(endpoint_id="n0", state=self.state, cpu_cores=1, memory_mb=1024, since="")]

    fleet = _Fleet()
    woken = []

    def waker(reason):
        woken.append(reason)
        fleet.state = "running"   # the reconciler would get it there
        return "n0"

    picker = EndpointPicker(_FakeEndpointRepository([_endpoint("n0")]), fleet=fleet, waker=waker, sleep_fn=lambda s: None)
    assert picker.pick().endpoint_id == "n0"
    assert len(woken) == 1


class _RecordingFunctions:
    def __init__(self):
        self.applied = []

    def find_by_code_hash(self, function_id, code_hash):
        return None

    def apply_event_data(self, data):
        self.applied.append(data)


def test_a_registered_function_is_in_the_read_model_before_the_answer(monkeypatch):
    # A job submitted right after registering must find it, before the
    # endpoint's FunctionRegistered event reaches the projector.
    functions = _RecordingFunctions()
    use_case = RegisterFunctionUseCase("ve1", EndpointPicker(_FakeEndpointRepository([_endpoint()])), 0.3,
                                       function_repository=functions)
    monkeypatch.setattr(register_function_module, "send_command", lambda rpc_uri, command, timeout: Ok(
        CommandResult(ok=True, metadata={"function_id": "fid", "version": 3})))
    use_case.execute(current_user_id="user-1", name="add", code=b"...", code_hash="h1", call_style="kwargs")

    [saved] = functions.applied
    assert saved["function_id"] == compute_function_id("user-1", "ve1", "add")
    assert (saved["version"], saved["name"], saved["code_hash"], saved["endpoint_id"]) == (3, "add", "h1", "n0")
    assert saved["runtime_spec"] == {"call_style": "kwargs"}
