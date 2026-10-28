import json
import threading

import zmq

from axo_shared import wire
from axo_shared.functions.identity import compute_function_id

from .conftest import FAKE_USER


def _run_fake_endpoint(bind_address, reply_ok, error_message="", metadata=None):
    ctx = zmq.Context.instance()
    router = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.RCVTIMEO, 3000)
    router.bind(bind_address)

    captured = {}

    def _serve():
        frames = router.recv_multipart()
        identity, body = frames[0], frames[1:]
        command = wire.decode_command(body).unwrap()
        captured["command"] = command
        from axo_shared.protocol import CommandResult
        result = CommandResult(ok=reply_ok, error=error_message, metadata=metadata or {})
        router.send_multipart([identity, *wire.encode_command_result(result)])
        router.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return thread, captured


def _source_for(function_name: str) -> bytes:
    return (
        f"def {function_name}(a: int, b: int = 2, ctx=None):\n"
        f"    \"\"\"Adds two numbers.\"\"\"\n"
        f"    return {{'sum': a + b}}\n"
    ).encode()


VE_ID = "ve1"  # this VEM's own VE (see conftest's register_function_use_case)


def _bind_probe():
    ctx = zmq.Context.instance()
    probe = ctx.socket(zmq.ROUTER)
    probe.bind("tcp://127.0.0.1:0")
    address = probe.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")
    probe.close()
    return address


def test_register_function_validates_source_and_forwards_it_untouched(client, collections):
    address = _bind_probe()
    ve_id = VE_ID

    # endpoint_id is set to the resolvable loopback host itself -- proves the
    # use case connects to <endpoint_id>:<port>, not whatever host is
    # stored in router_bind (see test below for the case where those differ).
    collections.endpoints.insert_one({"_id": "127.0.0.1", "router_bind": address, "status": "running"})
    thread, captured = _run_fake_endpoint(address, reply_ok=True, metadata={"function_id": "add", "version": 1})

    source = _source_for("add")
    response = client.post(
        "/functions",
        data={"name": "add", "runtime_spec": '{"type": "process"}'},
        files={"code": ("add.py", source, "text/x-python")},
    )
    thread.join(timeout=2.0)

    assert response.status_code == 201
    body = response.json()
    assert body["endpoint_id"] == "127.0.0.1"
    assert body["name"] == "add"
    assert body["version"] == 1

    command = captured["command"]
    assert command.operation == wire.FUNCTION_REGISTER
    assert command.envelope["name"] == "add"
    assert command.envelope["function_id"] == compute_function_id(FAKE_USER.key, ve_id, "add")
    assert command.envelope["runtime_spec"] == {"type": "process", "call_style": "kwargs"}
    assert command.envelope["code_format"] == "source"
    # The run form's fields come from the code itself.
    assert command.envelope["params_schema"] == [
        {"name": "a", "type": "number", "required": True, "default": None},
        {"name": "b", "type": "number", "required": False, "default": 2},
    ]

    # This service never executes or pickles the upload -- the forwarded
    # payload must be the exact raw source bytes, verbatim.
    assert command.payload == source


def test_register_function_ignores_router_bind_host_and_uses_endpoint_id(client, collections):
    address = _bind_probe()
    port = address.rsplit(":", 1)[-1]
    ve_id = VE_ID

    # router_bind carries the real production shape (an unconnectable bind
    # address) while endpoint_id is the resolvable host -- this only
    # succeeds if the use case builds the connect target from endpoint_id,
    # not from router_bind's own host part.
    collections.endpoints.insert_one({
        "_id": "127.0.0.1", "router_bind": f"tcp://0.0.0.0:{port}", "status": "running",
    })
    thread, captured = _run_fake_endpoint(address, reply_ok=True, metadata={"function_id": "add", "version": 1})

    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )
    thread.join(timeout=2.0)

    assert response.status_code == 201
    assert captured["command"].operation == wire.FUNCTION_REGISTER


def test_register_function_relays_rejection_as_409(client, collections):
    address = _bind_probe()
    ve_id = VE_ID

    collections.endpoints.insert_one({"_id": "127.0.0.1", "router_bind": address, "status": "running"})
    thread, _ = _run_fake_endpoint(address, reply_ok=False, error_message="storage failure")

    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )
    thread.join(timeout=2.0)

    assert response.status_code == 409
    assert response.json()["detail"] == "storage failure"


def test_register_function_returns_409_when_no_endpoint_is_running(client):
    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )

    assert response.status_code == 409
    assert "no running endpoint" in response.json()["detail"]


def test_register_function_returns_422_for_invalid_runtime_spec_json(client):
    # 422 validation happens before any endpoint lookup.
    response = client.post(
        "/functions",
        data={"name": "add", "runtime_spec": "not-json"},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )

    assert response.status_code == 422


def test_register_function_returns_422_for_syntax_error(client):
    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", b"def add(a, b:\n    pass\n", "text/x-python")},
    )

    assert response.status_code == 422
    assert "syntax error" in response.json()["detail"]


def test_register_function_returns_422_when_name_not_defined_in_source(client):
    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", _source_for("subtract"), "text/x-python")},
    )

    assert response.status_code == 422
    assert "add" in response.json()["detail"]


def test_register_function_times_out_when_endpoint_unreachable(client, collections):
    ve_id = VE_ID
    collections.endpoints.insert_one({
        "_id": "127.0.0.1", "router_bind": "tcp://127.0.0.1:59998", "status": "running",
    })

    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )

    assert response.status_code == 504


def test_delete_function_forwards_command_and_relays_success(client, collections):
    address = _bind_probe()
    collections.endpoints.insert_one({"_id": "127.0.0.1", "router_bind": address})
    collections.functions.insert_one({
        "_id": "add:1", "function_id": "add", "version": 1, "endpoint_id": ["127.0.0.1"],
    })
    thread, captured = _run_fake_endpoint(address, reply_ok=True, metadata={"function_id": "add", "version": 1})

    response = client.delete("/functions/add/1")
    thread.join(timeout=2.0)

    assert response.status_code == 200
    assert response.json() == {"function_id": "add", "version": 1}

    command = captured["command"]
    assert command.operation == wire.FUNCTION_DELETE
    assert command.envelope == {"function_id": "add", "version": 1}


def test_delete_function_relays_rejection_as_409(client, collections):
    address = _bind_probe()
    collections.endpoints.insert_one({"_id": "127.0.0.1", "router_bind": address})
    collections.functions.insert_one({
        "_id": "add:1", "function_id": "add", "version": 1, "endpoint_id": ["127.0.0.1"],
    })
    thread, _ = _run_fake_endpoint(address, reply_ok=False, error_message="function not found")

    response = client.delete("/functions/add/1")
    thread.join(timeout=2.0)

    assert response.status_code == 409
    assert response.json()["detail"] == "function not found"


def test_delete_function_returns_404_for_unknown_function(client):
    response = client.delete("/functions/missing/1")
    assert response.status_code == 404


def test_delete_function_times_out_when_endpoint_unreachable(client, collections):
    collections.endpoints.insert_one({"_id": "127.0.0.1", "router_bind": "tcp://127.0.0.1:59998"})
    collections.functions.insert_one({
        "_id": "add:1", "function_id": "add", "version": 1, "endpoint_id": ["127.0.0.1"],
    })

    response = client.delete("/functions/add/1")

    assert response.status_code == 504


def test_delete_function_routes_via_recorded_endpoint_despite_ve_mismatched_endpoint_present(client, collections):
    """Regression test for the original bug: deleting used to re-derive
    function_id from a caller-picked endpoint's virtual_environment_id, so
    picking (or defaulting to) an endpoint with a different VE than the one
    used at register time permanently failed with "no FunctionRecord found".
    Now routing depends only on the function's own recorded endpoint_id --
    a second, VE-mismatched endpoint existing in the cluster must have no
    effect on the outcome."""
    address = _bind_probe()
    # The function's real, recorded endpoint.
    collections.endpoints.insert_one({
        "_id": "127.0.0.1", "router_bind": address, "virtual_environment_id": "ve-a",
    })
    # A decoy endpoint in a totally different VE -- must never be contacted.
    collections.endpoints.insert_one({
        "_id": "decoy", "router_bind": "tcp://127.0.0.1:59997", "virtual_environment_id": "ve-b",
    })
    collections.functions.insert_one({
        "_id": "add:1", "function_id": "add", "version": 1, "endpoint_id": ["127.0.0.1"],
    })
    thread, captured = _run_fake_endpoint(address, reply_ok=True, metadata={"function_id": "add", "version": 1})

    response = client.delete("/functions/add/1")
    thread.join(timeout=2.0)

    assert response.status_code == 200
    assert captured["command"].operation == wire.FUNCTION_DELETE


def test_an_old_style_function_is_refused_with_a_clear_message(client):
    response = client.post(
        "/functions",
        data={"name": "add"},
        files={"code": ("add.py", b"def add(params, ctx):\n    return params['a']\n", "text/x-python")},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "FUNCTION_STYLE_UNSUPPORTED"
    assert "named parameters" in response.json()["message"]


def test_the_live_check_answers_problems_and_the_run_form_without_an_endpoint(client):
    bad = client.post("/functions/check", json={"name": "add", "source": "def add(a, b:\n    pass\n"}).json()
    assert not bad["ok"] and bad["problems"][0]["line"] == 1
    good = client.post("/functions/check", json={"name": "add", "source": _source_for("add").decode()}).json()
    assert good["ok"] and [p["name"] for p in good["params_schema"]] == ["a", "b"]
    assert good["description"] == "Adds two numbers." and good["existing_version"] is None


def test_save_as_a_new_version_needs_an_existing_function_of_that_name(client, collections):
    missing = client.post("/functions", data={"name": "add", "function_id": "nope"},
                          files={"code": ("add.py", _source_for("add"), "text/x-python")})
    assert missing.status_code == 404
    collections.functions.insert_one({"_id": "f1:1", "function_id": "f1", "version": 1, "name": "sum"})
    renamed = client.post("/functions", data={"name": "add", "function_id": "f1"},
                          files={"code": ("add.py", _source_for("add"), "text/x-python")})
    assert renamed.status_code == 422


def test_a_container_runner_too_big_for_the_plan_is_refused_before_any_endpoint(client, register_function_use_case):
    from axo_vem.domain.compute.runner_size import with_runner_size
    from axo_vem.domain.virtual_environment.settings import PlanLimits

    free = PlanLimits(runner_cpu_cores=0.5, runner_memory_mb=512, runner_max_cpu_cores=0.5, runner_max_memory_mb=512)
    register_function_use_case._runner_size = lambda spec: with_runner_size(spec, free, 1.0, 1024)
    response = client.post(
        "/functions",
        data={"name": "add", "runtime_spec": json.dumps({"type": "container", "python_version": "3.11", "cpu_limit": 2})},
        files={"code": ("add.py", _source_for("add"), "text/x-python")},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "RUNNER_SIZE_INVALID" and response.json()["error_code"] == 9630
