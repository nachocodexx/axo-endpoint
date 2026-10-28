"""Function code is kept in axo_storage (a real, in-process one here) and
re-registered at its exact version into empty endpoints after a start."""
import pytest
from option import Err, Ok

from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials
from axo_shared.errors import AxoError
from axo_shared.protocol import CommandResult
from axo_storage.access import AllowAllAccessChecker
from axo_storage.auth import Authenticator
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import MemoryChunkStore
from axo_storage.server import StorageRouterServer
from axo_storage.service import StorageService

from axo_vem.application.compute import register_function as register_module
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_code_store import FunctionCodeStore, code_bucket_name
from axo_vem.application.compute.register_function import RegisterFunctionUseCase
from axo_vem.application.virtual_environment import restorer as restorer_module
from axo_vem.application.virtual_environment.restorer import VeRestorer
from axo_vem.domain.compute.endpoint import Endpoint
from axo_vem.domain.compute.function import Function


@pytest.fixture
def code_store():
    service = StorageService(
        catalog=StorageCatalog(":memory:"), chunks=MemoryChunkStore(),
        authenticator=Authenticator({}, ["vem-token"]), access=AllowAllAccessChecker(),
    )
    server = StorageRouterServer("tcp://127.0.0.1:0", service, workers=2)
    server.start()
    client = AxoStorageClient(server.bind_address, ServiceCredentials("vem-token"))
    yield FunctionCodeStore(client, "ML_Prod")
    client.close()
    server.stop()


class _Endpoints:
    def list_all(self):
        return [Endpoint(endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", status="running")]


class _Functions:
    def __init__(self, functions):
        self._functions = functions

    def list_active(self):
        return list(self._functions)


def test_code_bucket_name_is_a_platform_bucket_name():
    """Owned by the platform ("axo/..."); the Control Plane lets only this VE use it."""
    assert code_bucket_name("ML_Prod") == "axo/ve-ml-prod-functions"


def test_register_keeps_the_code_under_the_functions_own_version(code_store, monkeypatch):
    from axo_shared.functions.identity import compute_function_id

    monkeypatch.setattr(
        register_module, "send_command",
        lambda rpc_uri, command, timeout: Ok(CommandResult(ok=True, metadata={"version": 3})),
    )
    use_case = RegisterFunctionUseCase("ML_Prod", EndpointPicker(_Endpoints()), 0.3, code_store=code_store)
    use_case.execute(current_user_id="luis", name="add", code=b"def add(p, c): return 1\n")

    function_id = compute_function_id("luis", "ML_Prod", "add")
    assert code_store.load(function_id, 3) == b"def add(p, c): return 1\n"


def test_restore_re_registers_every_live_function_at_its_exact_version(code_store, monkeypatch):
    code_store.save("f1", 1, b"code-1")
    code_store.save("f1", 2, b"code-2")
    sent = []

    def fake_send(rpc_uri, command, timeout):
        sent.append(command)
        return Ok(CommandResult(ok=True, metadata={"version": command.envelope["version"]}))

    monkeypatch.setattr(restorer_module, "send_command", fake_send)
    functions = [
        Function(function_id="f1", version=1, name="add", runtime_spec={"type": "process"}),
        Function(function_id="f1", version=2, name="add", params_schema=[{"name": "x", "type": "number"}]),
    ]
    progress = VeRestorer(_Functions(functions), code_store, EndpointPicker(_Endpoints())).restore()

    assert progress.to_dict() == {"total": 2, "restored": 2, "failed": [], "running": False}
    assert [(c.envelope["version"], c.envelope["restore"], c.payload) for c in sent] == [
        (1, True, b"code-1"), (2, True, b"code-2"),
    ]
    assert sent[0].envelope["runtime_spec"] == {"type": "process"}
    assert sent[1].envelope["params_schema"] == [{"name": "x", "type": "number"}]


def test_restore_is_safe_to_repeat_and_reports_what_failed(code_store, monkeypatch):
    code_store.save("f1", 1, b"code-1")
    monkeypatch.setattr(
        restorer_module, "send_command",
        lambda rpc_uri, command, timeout: Ok(CommandResult(
            ok=False, error="already there", metadata={"error_name": "FUNCTION_VERSION_EXISTS"},
        )),
    )
    functions = [Function(function_id="f1", version=1, name="add"), Function(function_id="lost", version=1)]
    progress = VeRestorer(_Functions(functions), code_store, EndpointPicker(_Endpoints())).restore()

    assert progress.restored == 1                      # already back counts as restored
    assert progress.failed[0]["function_id"] == "lost"  # no saved code


def test_restoring_sends_back_the_code_format_and_hash(code_store, monkeypatch):
    """A pickled function or an Axo class comes back exactly as it came."""
    code_store.save("cls-1", 2, b"pickled-bytes")
    sent = []
    monkeypatch.setattr(restorer_module, "send_command",
                        lambda rpc_uri, command, timeout: sent.append(command) or Ok(CommandResult(ok=True)))
    function = Function(function_id="cls-1", version=2, name="Counter", code_format="cloudpickle",
                        code_hash="abc", runtime_spec={"call_style": "object"})
    VeRestorer(_Functions([function]), code_store, EndpointPicker(_Endpoints())).restore()
    envelope = sent[0].envelope
    assert (envelope["code_format"], envelope["code_hash"], envelope["runtime_spec"]) == (
        "cloudpickle", "abc", {"call_style": "object"})
    assert sent[0].payload == b"pickled-bytes"
