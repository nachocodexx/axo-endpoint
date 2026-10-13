from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Union

import zmq

from axo_shared import wire
from axo_shared.events.envelope import decode_event
from axo_shared.messaging import RecentIds, build_ack, extract_message_id

from axo_control_plane.log import DumbLogger, Log
from axo_control_plane.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


class IntakeRouter:
    """ZMQ ROUTER receiving what the VEMs send (their ReliableSender's
    EVENT_PUBLISH + message_id). Each message is acked only after ``handle``
    saved it (returned True); otherwise the VEM resends it later. A message
    already acked once (lost ack) is recognized and acked again."""

    def __init__(self, bind_address: str, handle: Callable[[str, str, Dict], bool],
                 context: Optional["zmq.Context"] = None, logger: _Logger = None) -> None:
        self._bind_address = bind_address
        self._handle = handle
        self._context = context or zmq.Context.instance()
        self._socket: Optional["zmq.Socket"] = None
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logger: _Logger = logger or DumbLogger()
        self._recent_ids = RecentIds()

    @property
    def bind_address(self) -> str:
        if self._socket is None:
            return self._bind_address
        return self._socket.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")

    def start(self) -> None:
        self._socket = self._context.socket(zmq.ROUTER)
        self._socket.setsockopt(zmq.RCVTIMEO, 200)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self._bind_address)
        self._thread = threading.Thread(target=self._run, daemon=True, name="cp-intake")
        self._thread.start()
        self._logger.info_event(Event.Intake.STARTED, component=Component.INTAKE, bind_address=self.bind_address)

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._socket is not None:
            self._socket.close()

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                frames = self._socket.recv_multipart()
            except zmq.Again:
                continue
            except zmq.ZMQError:
                break
            self.handle_frames(frames)

    def handle_frames(self, frames) -> None:
        if len(frames) < 2:
            return
        identity, body = frames[0], frames[1:]
        command = wire.decode_command(body)
        if command.is_err:
            self._logger.warning_event(Event.Intake.FRAME_MALFORMED, component=Component.INTAKE,
                                       error=str(command.unwrap_err()))
            return
        command = command.unwrap()
        event = decode_event(command)
        if event.is_err:
            self._logger.warning_event(Event.Intake.ENVELOPE_MALFORMED, component=Component.INTAKE,
                                       error=str(event.unwrap_err()))
            return
        event_type, source_id, data = event.unwrap()
        message_id = extract_message_id(command)
        if message_id is not None and self._recent_ids.seen(message_id):
            self._logger.debug_event(Event.Intake.DUPLICATE_SKIPPED, component=Component.INTAKE,
                                     message_id=message_id)
            self._ack(identity, message_id)
            return
        try:
            saved = self._handle(event_type, source_id, data)
        except Exception as exc:
            saved = False
            self._logger.error_event(Event.Intake.SAVE_FAILED, component=Component.INTAKE,
                                     event_type=event_type, source_id=source_id, error=repr(exc))
        if not saved:
            if message_id is not None:
                self._recent_ids.forget(message_id)
            return
        if message_id is not None:
            self._ack(identity, message_id)

    def _ack(self, identity: bytes, message_id: str) -> None:
        self._socket.send_multipart([identity, *wire.encode_command_result(build_ack(message_id))])
