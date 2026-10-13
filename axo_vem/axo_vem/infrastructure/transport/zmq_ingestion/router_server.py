from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Union

import zmq

from axo_shared import wire
from axo_shared.messaging import RecentIds, build_ack, extract_message_id
from axo_shared.protocol import CommandResult

from axo_vem.domain.events import envelope
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.events.stream_naming import stream_name_for
from axo_vem.infrastructure.resilience.errors import classify_kurrent_error
from axo_vem.infrastructure.resilience.retry import retry_with_backoff
from axo_vem.log import DumbLogger, Log
from axo_vem.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


class IngestionRouterServer:
    """ZMQ ROUTER that receives EVENT_PUBLISH commands from axo_endpoint
    nodes and appends them to Kurrent. Mirrors axo_endpoint/runner/server.py's
    RunnerServer._run_zmq pattern: background daemon thread, RCVTIMEO-based
    recv loop checked against a threading.Event for shutdown. The socket is
    bound synchronously in start() (not inside the thread) so a caller using
    an ephemeral port (tcp://127.0.0.1:0, as tests do) can read the resolved
    bind address back immediately.

    Each event is acked (echoing its message_id) only after the Kurrent
    append succeeds. Endpoints deliver through axo_shared.messaging's
    ReliableSender, which resends anything unacked -- so a failed append just
    means "no ack, try again later", and a redelivered duplicate is skipped
    via RecentIds and acked again.

    Moved from ingestion/router_server.py -- appender is now typed against
    the domain.events.publisher.EventPublisher ABC instead of a local
    Protocol, and stream_name_for/decode_event come from the domain layer.
    """

    def __init__(
        self,
        bind_address: str,
        appender: EventPublisher,
        context: Optional["zmq.Context"] = None,
        logger: _Logger = None,
        max_attempts: int = 10,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
        local_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], bool]]] = None,
    ) -> None:
        self._bind_address = bind_address
        # Event types the VEM handles itself instead of appending to its event
        # log (job attempts -> durable outbox to the Control Plane; 10 s
        # metric bins -> in-memory metrics). Acked only if the handler
        # returns True, so the endpoint resends otherwise.
        self._local_handlers = dict(local_handlers or {})
        self._appender = appender
        self._context = context or zmq.Context.instance()
        self._socket: Optional["zmq.Socket"] = None
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logger: _Logger = logger or DumbLogger()
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        # Endpoints resend until acked (axo_shared.messaging), so a message
        # whose ack was lost arrives again: skip the append, ack again.
        self._recent_ids = RecentIds()

    @property
    def bind_address(self) -> str:
        """The actual bound address -- resolves ephemeral ("...:0") ports
        once start() has run."""
        if self._socket is None:
            return self._bind_address
        return self._socket.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")

    def start(self) -> None:
        """Binds the socket and starts the background receive loop."""
        self._socket = self._context.socket(zmq.ROUTER)
        self._socket.setsockopt(zmq.RCVTIMEO, 200)
        self._socket.bind(self._bind_address)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._logger.info_event(
            Event.Ingestion.STARTED,
            component=Component.INGESTION,
            bind_address=self.bind_address,
        )

    def stop(self) -> None:
        """Stops the receive loop and closes the socket."""
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
            self._handle_frames(frames)

    def _handle_frames(self, frames) -> None:
        if len(frames) < 2:
            return
        identity, body = frames[0], frames[1:]

        command_result = wire.decode_command(body)
        if command_result.is_err:
            self._logger.warning_event(
                Event.Ingestion.FRAME_MALFORMED,
                component=Component.INGESTION,
                error=str(command_result.unwrap_err()),
            )
            return
        command = command_result.unwrap()

        event_result = envelope.decode_event(command)
        if event_result.is_err:
            self._logger.warning_event(
                Event.Ingestion.ENVELOPE_MALFORMED,
                component=Component.INGESTION,
                error=str(event_result.unwrap_err()),
            )
            return
        event_type, endpoint_id, data = event_result.unwrap()
        message_id = extract_message_id(command)

        if message_id is not None and self._recent_ids.seen(message_id):
            self._logger.debug_event(
                Event.Ingestion.DUPLICATE_SKIPPED,
                component=Component.INGESTION,
                event_type=event_type,
                message_id=message_id,
            )
            self._ack(identity, message_id)
            return

        local = self._local_handlers.get(event_type)
        if local is not None:
            try:
                handled = local(data)
            except Exception as exc:  # a handler bug must not stop ingestion
                self._logger.error_event(
                    Event.Ingestion.LOCAL_HANDLER_FAILED, component=Component.INGESTION,
                    event_type=event_type, error=repr(exc),
                )
                handled = False
            if handled:
                self._ack(identity, message_id)
            elif message_id is not None:
                self._recent_ids.forget(message_id)
            return

        try:
            stream_name = stream_name_for(event_type, endpoint_id, data)
        except ValueError:
            # An event type this VEM doesn't know (e.g. a newer endpoint).
            # Ack it: resending would never succeed and would clog the
            # sender's journal.
            self._logger.warning_event(
                Event.Ingestion.UNKNOWN_EVENT_TYPE, component=Component.INGESTION,
                event_type=event_type, endpoint_id=endpoint_id,
            )
            self._ack(identity, message_id)
            return
        if not self._append_with_retry(stream_name, event_type, data):
            if message_id is not None:
                self._recent_ids.forget(message_id)  # not stored: let the resend through
            return  # exhausted retries -- already logged, no ack, so the sender resends
        self._ack(identity, message_id)

    def _append_with_retry(self, stream_name: str, event_type: str, data) -> bool:
        """Returns True on success, False if retries were exhausted (and
        already logged) -- callers drop the frame rather than letting the
        exception propagate and kill the receive thread, which is what
        happened before this existed (any append failure, not just a
        startup race, took ingestion down permanently)."""
        def on_attempt_failed(attempt: int, exc: Exception) -> None:
            error_code, description = classify_kurrent_error(exc)
            self._logger.warning_event(
                Event.Ingestion.APPEND_RETRYING,
                component=Component.INGESTION,
                attempt=attempt,
                max_attempts=self._max_attempts,
                stream_name=stream_name,
                event_type=event_type,
                error_code=error_code,
                description=description,
            )

        t0 = time.monotonic()
        try:
            retry_with_backoff(
                lambda: self._appender.append_to_stream(stream_name, event_type, data),
                max_attempts=self._max_attempts,
                base_delay_seconds=self._base_delay_seconds,
                max_delay_seconds=self._max_delay_seconds,
                on_attempt_failed=on_attempt_failed,
            )
        except Exception as exc:
            error_code, description = classify_kurrent_error(exc)
            self._logger.error_event(
                Event.Ingestion.APPEND_GIVING_UP,
                component=Component.INGESTION,
                max_attempts=self._max_attempts,
                stream_name=stream_name,
                event_type=event_type,
                error_code=error_code,
                description=description,
            )
            return False

        self._logger.info_event(
            Event.Ingestion.EVENT_APPENDED,
            component=Component.INGESTION,
            stream_name=stream_name,
            event_type=event_type,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        return True

    def _ack(self, identity: bytes, message_id: Optional[str] = None) -> None:
        """Sent only after the event is durably appended. Reliable senders
        drop the message from their journal when they see its message_id
        echoed back; older fire-and-forget senders simply ignore the reply."""
        result = build_ack(message_id) if message_id is not None else CommandResult(ok=True)
        try:
            self._socket.send_multipart(
                [identity, *wire.encode_command_result(result)],
                flags=zmq.NOBLOCK,
            )
        except zmq.ZMQError:
            pass  # best-effort -- the node never waits for this
