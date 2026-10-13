from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

import zmq
from option import Err, Ok, Result

from axo_shared import wire
from axo_shared.errors import AxoError, TransportSendError
from axo_shared.events.envelope import encode_event
from axo_shared.messaging.models import OutboxMessage
from axo_shared.messaging.receiver import acked_message_id


class AckTransport(ABC):
    """Moves OutboxMessages to the receiver and brings their acks back.
    Only ever used from the ReliableSender's own thread."""

    @abstractmethod
    def send(self, message: OutboxMessage) -> Result[None, AxoError]:
        """Hand one message to the network without blocking."""

    @abstractmethod
    def poll_acks(self, timeout_s: float) -> List[str]:
        """Wait up to timeout_s, then return every message_id acked since the last call."""

    def close(self) -> None:
        """Release the connection."""


class ZmqDealerTransport(AckTransport):
    """DEALER socket speaking the existing EVENT_PUBLISH wire shape, with the
    message_id carried in the envelope and echoed back in each ack.

    ``IMMEDIATE`` makes send() fail fast while the receiver is unreachable
    instead of piling messages into ZMQ's own buffer -- the journal is the
    buffer, and its retry schedule (not ZMQ's) decides when to try again.
    """

    def __init__(
        self,
        uri: str,
        context: Optional["zmq.Context"] = None,
        send_hwm: int = 1000,
    ) -> None:
        self._context = context or zmq.Context.instance()
        self._socket = self._context.socket(zmq.DEALER)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.IMMEDIATE, 1)
        self._socket.setsockopt(zmq.SNDHWM, send_hwm)
        self._socket.connect(uri)

    def send(self, message: OutboxMessage) -> Result[None, AxoError]:
        command = encode_event(message.event_type, message.source_id, message.data, message_id=message.message_id)
        try:
            self._socket.send_multipart(wire.encode_command(command), flags=zmq.NOBLOCK)
            return Ok(None)
        except zmq.ZMQError as exc:
            return Err(TransportSendError(str(exc), context={"message_id": message.message_id}))

    def poll_acks(self, timeout_s: float) -> List[str]:
        acked: List[str] = []
        if not self._socket.poll(int(timeout_s * 1000), zmq.POLLIN):
            return acked
        while True:
            try:
                frames = self._socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                return acked
            decoded = wire.decode_command_result(frames)
            if decoded.is_err:
                continue  # garbage reply: the message stays unacked and is resent later
            message_id = acked_message_id(decoded.unwrap())
            if message_id.is_ok:
                acked.append(message_id.unwrap())

    def close(self) -> None:
        self._socket.close()
