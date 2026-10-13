import time

import zmq

from axo_shared import wire
from axo_shared.events import envelope, models
from axo_shared.messaging import InMemoryJournal, build_ack, extract_message_id

from axo_endpoint.service.transport.reliable_event_publisher import ReliableEventPublisher, open_journal


def _router():
    ctx = zmq.Context.instance()
    router = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.LINGER, 0)
    router.bind("tcp://127.0.0.1:0")
    return ctx, router, router.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")


def _receive(router, ack=True, timeout_s=3.0):
    """One EVENT_PUBLISH off the ROUTER, acked like the VEM ingestion server does."""
    if not router.poll(int(timeout_s * 1000)):
        return None
    identity, *body = router.recv_multipart()
    command = wire.decode_command(body).unwrap()
    if ack:
        router.send_multipart([identity, *wire.encode_command_result(build_ack(extract_message_id(command)))])
    return command


def _wait(predicate, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


def test_publish_is_delivered_with_message_id_and_acked():
    ctx, router, address = _router()
    publisher = ReliableEventPublisher(api_uri=address, endpoint_id="n0", context=ctx)
    try:
        event = models.EndpointStarted(endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556")
        publisher.publish(models.ENDPOINT_STARTED, event.model_dump(mode="json"))

        command = _receive(router)
        assert command.operation == wire.EVENT_PUBLISH
        assert extract_message_id(command) == event.event_id
        event_type, endpoint_id, data = envelope.decode_event(command).unwrap()
        assert (event_type, endpoint_id, data["router_bind"]) == (models.ENDPOINT_STARTED, "n0", "tcp://0.0.0.0:5555")
        assert _wait(lambda: publisher.stats()["pending"] == 0)
    finally:
        publisher.close(flush_timeout_s=0)
        router.close()


def test_unacked_event_is_resent_until_acked():
    ctx, router, address = _router()
    publisher = ReliableEventPublisher(api_uri=address, endpoint_id="n0", context=ctx,
                                       ack_timeout_s=0.2, retry_base_s=0.05, retry_max_s=0.1)
    try:
        event = models.EndpointStopped(endpoint_id="n0", uptime_ms=42.0)
        publisher.publish(models.ENDPOINT_STOPPED, event.model_dump(mode="json"))
        first = _receive(router, ack=False)       # receiver "loses" the ack
        second = _receive(router, ack=True)       # resent copy, acked this time
        assert extract_message_id(first) == extract_message_id(second) == event.event_id
        assert _wait(lambda: publisher.stats()["pending"] == 0)
        assert publisher.stats()["retries"] >= 1
    finally:
        publisher.close(flush_timeout_s=0)
        router.close()


def test_publish_never_blocks_and_keeps_events_while_nothing_listens():
    journal = InMemoryJournal()
    publisher = ReliableEventPublisher(api_uri="tcp://127.0.0.1:59991", endpoint_id="n0", journal=journal)
    try:
        started = time.monotonic()
        for _ in range(5):
            event = models.EndpointMetricsReported(endpoint_id="n0", metrics={})
            publisher.publish(models.ENDPOINT_METRICS_REPORTED, event.model_dump(mode="json"))
        assert time.monotonic() - started < 0.5
    finally:
        publisher.close(flush_timeout_s=0)
    assert journal.size().unwrap() == 5   # still waiting for an ack, not lost


def test_open_journal_memory_or_sqlite(tmp_path):
    assert isinstance(open_journal(":memory:", "n0"), InMemoryJournal)
    journal = open_journal(str(tmp_path / "outbox"), "n0")
    assert (tmp_path / "outbox" / "n0.db").exists()
    journal.close()
