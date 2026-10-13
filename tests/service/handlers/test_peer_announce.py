from axo_shared import wire
from axo_shared.protocol import Command

from axo_endpoint.service.handlers.peer_announce import PeerAnnounceHandler


class _Subscriber:
    def __init__(self):
        self.connected = []

    def connect(self, address):
        self.connected.append(address)


def _announce(ve_id):
    envelope = {"pub_bind": "tcp://0.0.0.0:5556", "peer_id": "ep-1"}
    if ve_id is not None:
        envelope["virtual_environment_id"] = ve_id
    return Command(operation=wire.PEER_ANNOUNCE, content_type="application/json", envelope=envelope)


def test_announce_from_same_virtual_environment_subscribes():
    subscriber = _Subscriber()
    handler = PeerAnnounceHandler(subscriber, local_mode=True, virtual_environment_id="ve-a")
    assert handler.handle(_announce("ve-a")).ok
    assert subscriber.connected == ["tcp://localhost:5556"]


def test_announce_from_another_virtual_environment_is_rejected():
    subscriber = _Subscriber()
    handler = PeerAnnounceHandler(subscriber, local_mode=True, virtual_environment_id="ve-a")
    result = handler.handle(_announce("ve-b"))
    assert not result.ok and result.error_name == "FOREIGN_VIRTUAL_ENVIRONMENT"
    assert not handler.handle(_announce(None)).ok
    assert subscriber.connected == []
