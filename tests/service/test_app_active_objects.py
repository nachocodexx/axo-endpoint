"""Active objects end to end on one endpoint (the leader): a class
registered as source runs in forked workers; the endpoint keeps the state."""

import threading

import pytest
import zmq

from axo.core.capture import capture
from axo_shared import wire
from axo_shared.protocol import Command

from axo_endpoint.config import Config
from axo_endpoint.service.app import App
from tests.core.objects.object_samples import Ledger
from tests.service.test_app import _poll_until_terminal, _send_command


def _cmd(operation, **envelope):
    return Command(operation=operation, content_type="application/json", envelope=envelope)


@pytest.fixture
def endpoint(tmp_path, clean_env, monkeypatch):
    monkeypatch.setenv("AXO_ENDPOINT_ROUTER_BIND", f"ipc://{tmp_path}/router.sock")
    monkeypatch.setenv("AXO_ENDPOINT_PUB_BIND", f"ipc://{tmp_path}/pub.sock")
    monkeypatch.setenv("AXO_ENDPOINT_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("AXO_ENDPOINT_OBJECT_HOT_MAX_BYTES", "64")
    app = App(Config())
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    dealer = zmq.Context.instance().socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 5000)
    dealer.connect(app.config.AXO_ENDPOINT_ROUTER_BIND)
    code = capture(Ledger).unwrap()
    registered = _send_command(dealer, Command(
        operation=wire.FUNCTION_REGISTER, content_type="application/octet-stream",
        envelope={"function_id": "ledger", "name": "Ledger", "code_format": code.format,
                  "runtime_spec": {"call_style": "object"}},
        payload=code.data))
    assert registered.ok, registered.error
    try:
        yield app, dealer
    finally:
        dealer.close()
        app.stop()
        thread.join(timeout=5.0)


def _run(dealer, op, **extra):
    submitted = _send_command(dealer, _cmd(
        wire.ACTIVE_OBJECT_CALL, op=op, object_id="obj-1", name="acme", function_id="ledger",
        class_name="Ledger", class_version=1, started_by_id="ana", **extra))
    assert submitted.ok, submitted.error
    return _poll_until_terminal(dealer, submitted.metadata["job_id"], timeout=20.0).metadata


def test_an_object_keeps_its_state_between_calls(endpoint):
    app, dealer = endpoint
    assert _run(dealer, "create", kwargs={"owner": "ana"})["result_ok"]
    assert _run(dealer, "call", params={"amount": 5})["output"]["value"] == 5
    assert _run(dealer, "call", params={"amount": 2})["output"]["value"] == 7

    assert _run(dealer, "call", method="attach", params={"size": 500})["result_ok"]
    assert _run(dealer, "call", method="blob_size")["output"]["value"] == 500   # fetched on demand

    failed = _run(dealer, "call", method="explode")
    assert not failed["result_ok"] and "boom" in failed["error"]
    state = _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_STATE, object_id="obj-1")).metadata
    assert state["attributes"]["entries"]["value"] == [5, 2]          # the failed call saved nothing
    assert state["attributes"]["blob"]["tier"] == "blob" and "value" not in state["attributes"]["blob"]
    assert state["revision"] == 4

    restored = _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_RESTORE, object_id="obj-1", revision=2))
    assert restored.ok and restored.metadata["revision"] == 5
    assert _run(dealer, "call", params={"amount": 0})["output"]["value"] == 5

    cloned = _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_CLONE, source_id="obj-1", object_id="obj-2", name="copy"))
    assert cloned.ok
    assert _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_DELETE, object_id="obj-1")).ok
    gone = _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_STATE, object_id="obj-1"))
    assert not gone.ok and gone.metadata["error_name"] == "ACTIVE_OBJECT_NOT_FOUND"
    copy = _send_command(dealer, _cmd(wire.ACTIVE_OBJECT_STATE, object_id="obj-2")).metadata
    assert copy["attributes"]["entries"]["value"] == [5, 0]


def test_creating_twice_fails_and_an_unknown_method_is_a_user_error(endpoint):
    _, dealer = endpoint
    assert _run(dealer, "create")["result_ok"]
    again = _run(dealer, "create")
    assert not again["result_ok"] and "ACTIVE_OBJECT_EXISTS" in again["error"]
    unknown = _run(dealer, "call", method="nope")
    assert not unknown["result_ok"] and "nope" in unknown["error"]


def test_a_choreography_event_reaches_the_method_as_self_event(endpoint):
    _, dealer = endpoint
    assert _run(dealer, "create")["result_ok"]
    event = {"run": {"run_id": "r1", "choreography": "etl"}, "step": "tally", "input": {"day": "mon"},
             "previous": {"count": {"kind": "function", "status": "completed", "output": {"rows": 3}}}}
    seen = _run(dealer, "call", method="event_seen", params={"tag": "t1"}, event=event)
    assert seen["result_ok"], seen.get("error")
    assert seen["output"]["value"] == {"tag": "t1", "event": event}      # the event isn't a parameter
    plain = _run(dealer, "call", method="event_seen")
    assert plain["output"]["value"] == {"tag": "none", "event": None}     # outside a choreography
