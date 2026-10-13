from __future__ import annotations

from typing import List, Optional

import cloudpickle
import pytest
import zmq

import axo_shared.client as client_module
from axo_shared import wire
from axo_shared.client import AxoClientError, AxoClientTimeout, AxoEndpointClient
from axo_shared.functions.identity import compute_function_id
from axo_shared.protocol import CommandResult
from axo_shared.wire import decode_command, encode_command_result


class _FakeSocket:
    """Stands in for the DEALER socket: records every sent frame set and
    replays a queue of canned replies, one per send. recv_multipart raises
    zmq.Again if raise_again is set, mirroring a real RCVTIMEO expiry."""

    def __init__(self, replies: Optional[List[List[bytes]]] = None, raise_again: bool = False) -> None:
        self.sent: List[List[bytes]] = []
        self._replies = list(replies or [])
        self._raise_again = raise_again

    def send_multipart(self, frames: List[bytes]) -> None:
        self.sent.append(frames)

    def recv_multipart(self) -> List[bytes]:
        if self._raise_again:
            raise zmq.Again()
        return self._replies.pop(0)


def _reply(ok: bool = True, error: str = "", metadata: Optional[dict] = None, payload: bytes = b"") -> List[bytes]:
    return encode_command_result(CommandResult(ok=ok, error=error, metadata=metadata or {}, payload=payload))


def _client_with_fake_socket(replies: Optional[List[List[bytes]]] = None, raise_again: bool = False) -> AxoEndpointClient:
    """A real AxoEndpointClient (real zmq context, DEALER connect never
    blocks even with nothing listening) with its socket swapped for a fake
    one right after construction, so no network I/O actually happens."""
    client = AxoEndpointClient(address="tcp://127.0.0.1:1", timeout_ms=100)
    client._sock = _FakeSocket(replies=replies, raise_again=raise_again)
    return client


def test_ping_sends_correct_command_and_returns_result():
    client = _client_with_fake_socket(replies=[_reply(ok=True, metadata={"pong": True})])

    result = client.ping()

    sent_command = decode_command(client._sock.sent[0]).unwrap()
    assert sent_command.operation == wire.PING
    assert sent_command.envelope == {}
    assert result.ok is True
    assert result.metadata == {"pong": True}


def test_cancel_job_sends_job_cancel_operation_with_job_id():
    client = _client_with_fake_socket(replies=[_reply(ok=True, metadata={"job_id": "job1", "status": "CANCELLED"})])

    result = client.cancel_job("job1")

    sent_command = decode_command(client._sock.sent[0]).unwrap()
    assert sent_command.operation == wire.JOB_CANCEL
    assert sent_command.envelope == {"job_id": "job1"}
    assert result.ok is True
    assert result.metadata["status"] == "CANCELLED"


def test_send_raises_axo_client_timeout_on_zmq_again():
    client = _client_with_fake_socket(raise_again=True)

    with pytest.raises(AxoClientTimeout):
        client.ping()


def test_send_raises_axo_client_error_on_malformed_reply_frames():
    client = _client_with_fake_socket(replies=[[b"only-one-frame"]])

    with pytest.raises(AxoClientError):
        client.ping()


def test_register_function_computes_function_id_and_pickles_fn_when_code_not_given():
    client = _client_with_fake_socket(replies=[_reply(ok=True, metadata={"version": 1})])

    def add_one(x: int) -> int:
        return x + 1

    result = client.register_function("u1", "ve1", "add_one", fn=add_one)

    sent_command = decode_command(client._sock.sent[0]).unwrap()
    assert sent_command.operation == wire.FUNCTION_REGISTER
    assert sent_command.content_type == "application/octet-stream"
    assert sent_command.envelope["function_id"] == compute_function_id("u1", "ve1", "add_one")
    restored = cloudpickle.loads(sent_command.payload)
    assert restored(4) == 5
    assert result.ok is True


def test_register_function_raises_value_error_when_neither_fn_nor_code_given():
    client = _client_with_fake_socket()

    with pytest.raises(ValueError):
        client.register_function("u1", "ve1", "add_one")

    assert client._sock.sent == []


def test_run_raises_axo_client_timeout_when_job_stays_pending(monkeypatch: pytest.MonkeyPatch):
    client = _client_with_fake_socket(replies=[
        _reply(ok=True, metadata={"job_id": "job1", "status": "QUEUED"}),
        _reply(ok=True, metadata={"job_id": "job1", "status": "PENDING"}),
        _reply(ok=True, metadata={"job_id": "job1", "status": "PENDING"}),
    ])
    monotonic_values = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(AxoClientTimeout):
        client.run("u1", "ve1", "add_one", 1, timeout=1.0, poll_interval=0.0)


def test_run_returns_the_terminal_result_once_the_job_leaves_pending():
    client = _client_with_fake_socket(replies=[
        _reply(ok=True, metadata={"job_id": "job1", "status": "QUEUED"}),
        _reply(ok=True, metadata={"job_id": "job1", "status": "COMPLETED", "output": {"value": 5, "type": "json"}}),
    ])

    result = client.run("u1", "ve1", "add_one", 1, poll_interval=0.0)

    assert result.ok is True
    assert result.metadata["status"] == "COMPLETED"
