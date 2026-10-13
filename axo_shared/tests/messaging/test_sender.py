import time
from typing import List

import zmq
from option import Err, Ok

from axo_shared import wire
from axo_shared.errors import TransportSendError
from axo_shared.events.envelope import decode_event
from axo_shared.protocol import CommandResult
from axo_shared.messaging import (
    AckTransport,
    InMemoryJournal,
    RecentIds,
    ReliableSender,
    ZmqDealerTransport,
    acked_message_id,
    backoff_delay,
    build_ack,
    extract_message_id,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeTransport(AckTransport):
    """Delivers to an in-memory 'receiver'. While down, send() fails; the first
    ``drop_acks`` acks are lost, simulating a reply that never comes back."""

    def __init__(self, drop_acks: int = 0) -> None:
        self.down = False
        self.drop_acks = drop_acks
        self.received: List[str] = []
        self._pending_acks: List[str] = []
        self.closed = False

    def send(self, message):
        if self.down:
            return Err(TransportSendError("receiver down"))
        self.received.append(message.message_id)
        if self.drop_acks > 0:
            self.drop_acks -= 1
        else:
            self._pending_acks.append(message.message_id)
        return Ok(None)

    def poll_acks(self, timeout_s):
        acks, self._pending_acks = self._pending_acks, []
        return acks

    def close(self):
        self.closed = True


def _sender(transport, journal=None, clock=None, **kwargs):
    return ReliableSender(
        journal or InMemoryJournal(),
        transport,
        source_id="ep-0",
        base_delay_s=1.0,
        max_delay_s=8.0,
        ack_timeout_s=5.0,
        clock=clock or FakeClock(),
        rng=lambda: 1.0,  # no jitter: delays are exactly the capped value
        **kwargs,
    )


def test_backoff_doubles_and_caps():
    no_jitter = lambda: 1.0  # noqa: E731
    delays = [backoff_delay(a, 1.0, 8.0, no_jitter) for a in range(1, 7)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    assert backoff_delay(3, 1.0, 8.0, lambda: 0.0) == 2.0  # half fixed, half jitter


def test_publish_uses_event_id_and_never_touches_the_network():
    transport = FakeTransport()
    transport.down = True
    sender = _sender(transport)
    assert sender.publish("JobAttemptRecorded", {"event_id": "e-1"}).unwrap() is True
    assert sender.publish("JobAttemptRecorded", {"event_id": "e-1"}).unwrap() is False
    assert transport.received == []
    assert sender.stats()["pending"] == 1


def test_happy_path_acks_and_empties_the_journal():
    transport = FakeTransport()
    sender = _sender(transport)
    for i in range(3):
        sender.publish("E", {"event_id": f"e-{i}"})
    sender.run_once()
    assert transport.received == ["e-0", "e-1", "e-2"]
    assert sender.stats() == {"sent": 3, "acked": 3, "retries": 0, "send_failures": 0, "pending": 0}


def test_lost_ack_is_resent_after_timeout_and_acked_once():
    clock = FakeClock()
    transport = FakeTransport(drop_acks=1)
    retries = []
    sender = _sender(transport, clock=clock, on_delivery_retry=lambda m, a, e: retries.append((m.message_id, a, e)))
    sender.publish("E", {"event_id": "e-1"})

    sender.run_once()
    assert sender.stats()["pending"] == 1  # sent, ack lost

    clock.now += 5.0  # ack timeout passed, but backoff (1 s) not yet
    sender.run_once()
    assert transport.received == ["e-1"]

    clock.now += 1.0
    sender.run_once()
    assert transport.received == ["e-1", "e-1"]  # redelivered: receiver dedups
    assert sender.stats()["pending"] == 0
    assert sender.stats()["retries"] == 1
    assert retries == [("e-1", 2, None)]


def test_receiver_down_backs_off_then_delivers():
    clock = FakeClock()
    transport = FakeTransport()
    transport.down = True
    sender = _sender(transport, clock=clock)
    sender.publish("E", {"event_id": "e-1"})

    sender.run_once()  # attempt 1 fails -> retry in 1 s
    clock.now += 1.0
    sender.run_once()  # attempt 2 fails -> retry in 2 s
    clock.now += 1.0
    sender.run_once()  # not due yet
    assert sender.stats()["send_failures"] == 2

    transport.down = False
    clock.now += 1.0
    sender.run_once()
    assert transport.received == ["e-1"]
    assert sender.stats()["pending"] == 0


def test_background_thread_delivers_and_stop_closes_transport():
    transport = FakeTransport()
    sender = ReliableSender(InMemoryJournal(), transport, source_id="ep-0", poll_interval_s=0.01)
    sender.start()
    sender.publish("E", {"event_id": "e-1"})
    deadline = time.monotonic() + 2.0
    while sender.stats()["pending"] and time.monotonic() < deadline:
        time.sleep(0.01)
    sender.stop(flush_timeout_s=0.5)
    assert transport.received == ["e-1"]
    assert transport.closed


def test_ack_helpers_round_trip():
    assert acked_message_id(build_ack("m-1")).unwrap() == "m-1"
    assert acked_message_id(CommandResult(ok=True)).is_err


def test_recent_ids_detects_duplicates_and_forgets_oldest():
    ids = RecentIds(maxlen=2)
    assert ids.seen("a") is False
    assert ids.seen("a") is True
    ids.seen("b")
    ids.seen("c")  # evicts "a"
    assert ids.seen("a") is False


def test_zmq_round_trip_with_a_real_router():
    context = zmq.Context.instance()
    router = context.socket(zmq.ROUTER)
    router.setsockopt(zmq.LINGER, 0)
    port = router.bind_to_random_port("tcp://127.0.0.1")
    transport = ZmqDealerTransport(f"tcp://127.0.0.1:{port}", context=context)
    sender = ReliableSender(InMemoryJournal(), transport, source_id="ep-0", poll_interval_s=0.05)
    sender.publish("JobAttemptRecorded", {"event_id": "e-1", "job_id": "j-1"})

    received_events = []
    deadline = time.monotonic() + 3.0
    while sender.stats()["pending"] and time.monotonic() < deadline:
        sender.run_once()
        if router.poll(10):
            identity, *body = router.recv_multipart()
            command = wire.decode_command(body).unwrap()
            received_events.append(decode_event(command).unwrap())
            router.send_multipart([identity, *wire.encode_command_result(build_ack(extract_message_id(command)))])

    sender.stop(flush_timeout_s=0)
    router.close()
    assert received_events[0] == ("JobAttemptRecorded", "ep-0", {"event_id": "e-1", "job_id": "j-1"})
    assert sender.stats()["pending"] == 0


def test_recent_ids_forget_lets_the_id_through_again():
    ids = RecentIds()
    ids.seen("a")
    ids.forget("a")
    assert ids.seen("a") is False
