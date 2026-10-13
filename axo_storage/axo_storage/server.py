from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Union

import zmq

from axo_shared import wire
from axo_shared.errors import AxoError
from axo_shared.protocol import CommandResult

from axo_storage.log import DumbLogger, Log
from axo_storage.log.catalog import Component, Event
from axo_storage.service import StorageService

_Logger = Union[Log, DumbLogger]


class StorageRouterServer:
    """ZMQ ROUTER in front of StorageService. Clients are plain DEALERs (the
    same framing axo_endpoint's ROUTER speaks). One thread owns the ROUTER
    socket; requests run on a worker pool, and each reply comes back to the
    socket thread over an inproc PULL socket -- so the ROUTER is never
    touched from two threads, and a slow chunk read never blocks others."""

    def __init__(
        self,
        bind_address: str,
        service: StorageService,
        workers: int = 8,
        context: Optional["zmq.Context"] = None,
        logger: _Logger = None,
    ) -> None:
        self._bind_address = bind_address
        self._service = service
        self._context = context or zmq.Context.instance()
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="axo-storage")
        self._logger: _Logger = logger or DumbLogger()
        self._reply_address = f"inproc://axo-storage-replies-{uuid.uuid4().hex}"
        self._router: Optional["zmq.Socket"] = None
        self._replies: Optional["zmq.Socket"] = None
        self._local = threading.local()
        self._pushers: List["zmq.Socket"] = []
        self._pushers_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def bind_address(self) -> str:
        if self._router is None:
            return self._bind_address
        return self._router.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")

    def start(self) -> None:
        """Binds synchronously (so an ephemeral port is readable right away)
        and starts the socket thread."""
        self._router = self._context.socket(zmq.ROUTER)
        self._router.setsockopt(zmq.LINGER, 0)
        self._router.bind(self._bind_address)
        self._replies = self._context.socket(zmq.PULL)
        self._replies.bind(self._reply_address)
        self._thread = threading.Thread(target=self._run, daemon=True, name="axo-storage-router")
        self._thread.start()
        self._logger.info_event(Event.Server.STARTED, component=Component.SERVER, bind_address=self.bind_address)

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._pushers_lock:
            for sock in self._pushers:
                sock.close(linger=0)
            self._pushers.clear()
        for sock in (self._replies, self._router):
            if sock is not None:
                sock.close(linger=0)
        self._logger.info_event(Event.Server.STOPPED, component=Component.SERVER)

    def _run(self) -> None:
        poller = zmq.Poller()
        poller.register(self._router, zmq.POLLIN)
        poller.register(self._replies, zmq.POLLIN)
        while not self._shutdown.is_set():
            try:
                ready = dict(poller.poll(200))
            except zmq.ZMQError:
                break
            if self._replies in ready:
                while True:
                    try:
                        self._router.send_multipart(self._replies.recv_multipart(zmq.NOBLOCK))
                    except zmq.Again:
                        break
            if self._router in ready:
                while True:
                    try:
                        frames = self._router.recv_multipart(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    if len(frames) < 2:
                        continue
                    try:
                        self._pool.submit(self._work, frames[0], frames[1:])
                    except RuntimeError:  # pool shut down mid-stop
                        return

    def _pusher(self) -> "zmq.Socket":
        sock = getattr(self._local, "sock", None)
        if sock is None:
            sock = self._context.socket(zmq.PUSH)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(self._reply_address)
            self._local.sock = sock
            with self._pushers_lock:
                self._pushers.append(sock)
        return sock

    def _work(self, identity: bytes, body: List[bytes]) -> None:
        decoded = wire.decode_command(body)
        if decoded.is_err:
            self._logger.warning_event(
                Event.Server.FRAME_MALFORMED, component=Component.SERVER, error=str(decoded.unwrap_err()),
            )
            result = CommandResult.from_error(decoded.unwrap_err())
        else:
            try:
                result = self._service.handle(decoded.unwrap())
            except Exception as exc:  # the service never raises; this is a last guard
                self._logger.error_event(Event.Server.HANDLER_CRASHED, component=Component.SERVER, error=repr(exc))
                err = AxoError(f"internal error: {exc!r}")
                result = CommandResult.from_error(err)
        if self._shutdown.is_set():
            return
        self._pusher().send_multipart([identity, *wire.encode_command_result(result)])
