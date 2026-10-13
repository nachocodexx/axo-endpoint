from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from axo_shared.protocol import Command, CommandHandler, CommandResult
from axo_endpoint.core.errors import ForeignVirtualEnvironmentError
from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event
from axo_endpoint.service.transport.address import resolve_peer_address

if TYPE_CHECKING:
    from axo_endpoint.service.transport.heartbeat_zmq import ZmqHeartbeatSubscriber

_Logger = Union[Log, DumbLogger]


class PeerAnnounceHandler(CommandHandler):
    """Receives a peer's pub address and dynamically subscribes to it."""

    def __init__(
        self,
        subscriber: "ZmqHeartbeatSubscriber",
        local_mode: bool,
        logger: _Logger = None,
        virtual_environment_id: Optional[str] = None,
    ) -> None:
        self._subscriber = subscriber
        self._local_mode = local_mode
        self._virtual_environment_id = virtual_environment_id
        self._logger: _Logger = logger or DumbLogger()

    def handle(self, command: Command) -> CommandResult:
        """Connects the heartbeat subscriber to the announcing peer's PUB socket."""
        pub_bind = command.envelope.get("pub_bind", "")
        peer_id  = command.envelope.get("peer_id", "")

        if not pub_bind or not peer_id:
            return CommandResult(ok=True)  # ignore malformed announces silently

        peer_ve_id = command.envelope.get("virtual_environment_id")
        if peer_ve_id != self._virtual_environment_id:
            self._logger.warning_event(
                Event.Peer.ANNOUNCE_REJECTED,
                component=Component.HANDLER_PEER_ANNOUNCE,
                peer_id=peer_id,
                peer_virtual_environment_id=peer_ve_id,
                virtual_environment_id=self._virtual_environment_id,
            )
            return CommandResult.from_error(ForeignVirtualEnvironmentError(
                "peer belongs to a different virtual environment",
                context={"peer_id": peer_id, "peer_virtual_environment_id": peer_ve_id},
            ))

        resolved = resolve_peer_address(pub_bind, peer_id, self._local_mode)
        self._subscriber.connect(resolved)
        self._logger.info_event(
            Event.Peer.ANNOUNCE_RECEIVED,
            component=Component.HANDLER_PEER_ANNOUNCE,
            peer_id=peer_id,
            pub_bind=pub_bind,
            resolved=resolved,
        )
        return CommandResult(ok=True)
