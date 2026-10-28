import zmq

from axo_shared import wire
from axo_shared.events import envelope, models

from axo_vem.infrastructure.transport.zmq_ingestion.router_server import IngestionRouterServer


class _FakeAppender:
    def __init__(self):
        self.appended = []

    def append_to_stream(self, stream_name, event_type, data):
        self.appended.append((stream_name, event_type, data))


def _send_event(dealer, event_type, endpoint_id, data):
    command = envelope.encode_event(event_type, endpoint_id, data)
    dealer.send_multipart(wire.encode_command(command))


def test_router_appends_decoded_event_to_the_right_stream():
    ctx = zmq.Context.instance()
    appender = _FakeAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx)
    server.start()

    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 2000)
    dealer.connect(server.bind_address)

    try:
        event = models.EndpointStarted(
            endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
        )
        _send_event(dealer, models.ENDPOINT_STARTED, "n0", event.model_dump(mode="json"))

        # Confirm the best-effort ack arrives too.
        frames = dealer.recv_multipart()
        result = wire.decode_command_result(frames).unwrap()
        assert result.ok is True

        assert len(appender.appended) == 1
        stream_name, event_type, data = appender.appended[0]
        assert stream_name == "endpoints-n0"
        assert event_type == models.ENDPOINT_STARTED
        assert data["router_bind"] == "tcp://0.0.0.0:5555"
    finally:
        dealer.close()
        server.stop()


def test_router_ignores_malformed_frames_without_crashing():
    ctx = zmq.Context.instance()
    appender = _FakeAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx)
    server.start()

    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 300)
    dealer.connect(server.bind_address)

    try:
        dealer.send_multipart([b"not", b"a", b"valid", b"command", b"envelope"])

        # Send a real, valid event afterwards to confirm the server is still alive.
        event = models.EndpointMetricsReported(endpoint_id="n0", metrics={})
        _send_event(dealer, models.ENDPOINT_METRICS_REPORTED, "n0", event.model_dump(mode="json"))
        frames = dealer.recv_multipart()
        result = wire.decode_command_result(frames).unwrap()
        assert result.ok is True
        assert len(appender.appended) == 1
    finally:
        dealer.close()
        server.stop()


def test_router_appends_function_registered_to_functions_stream():
    ctx = zmq.Context.instance()
    appender = _FakeAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx)
    server.start()

    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 2000)
    dealer.connect(server.bind_address)

    try:
        event = models.FunctionRegistered(endpoint_id="n0", function_id="add", version=1, runtime_spec=None)
        _send_event(dealer, models.FUNCTION_REGISTERED, "n0", event.model_dump(mode="json"))
        dealer.recv_multipart()  # drain ack

        stream_name, event_type, _data = appender.appended[0]
        assert stream_name == "functions-add-1"
        assert event_type == models.FUNCTION_REGISTERED
    finally:
        dealer.close()
        server.stop()


def test_reliable_message_is_acked_with_its_id_and_duplicates_are_not_appended_twice():
    from axo_shared.messaging import acked_message_id

    ctx = zmq.Context.instance()
    appender = _FakeAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx)
    server.start()
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 2000)
    dealer.connect(server.bind_address)
    try:
        event = models.EndpointStopped(endpoint_id="n0", uptime_ms=1.0)
        command = envelope.encode_event(models.ENDPOINT_STOPPED, "n0", event.model_dump(mode="json"),
                                        message_id=event.event_id)
        for _ in range(2):  # original + a resend after a lost ack
            dealer.send_multipart(wire.encode_command(command))
            result = wire.decode_command_result(dealer.recv_multipart()).unwrap()
            assert acked_message_id(result).unwrap() == event.event_id
        assert len(appender.appended) == 1
    finally:
        dealer.close()
        server.stop()


def test_failed_append_is_not_acked_so_the_sender_resends():
    class _FlakyAppender(_FakeAppender):
        def __init__(self):
            super().__init__()
            self.fail = True

        def append_to_stream(self, stream_name, event_type, data):
            if self.fail:
                raise RuntimeError("kurrent down")
            super().append_to_stream(stream_name, event_type, data)

    ctx = zmq.Context.instance()
    appender = _FlakyAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx,
                                   max_attempts=1, base_delay_seconds=0.0, max_delay_seconds=0.0)
    server.start()
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 500)
    dealer.connect(server.bind_address)
    try:
        event = models.EndpointStopped(endpoint_id="n0", uptime_ms=1.0)
        command = envelope.encode_event(models.ENDPOINT_STOPPED, "n0", event.model_dump(mode="json"),
                                        message_id=event.event_id)
        dealer.send_multipart(wire.encode_command(command))
        try:
            dealer.recv_multipart()
            assert False, "a failed append must not be acked"
        except zmq.Again:
            pass
        appender.fail = False
        dealer.setsockopt(zmq.RCVTIMEO, 2000)
        dealer.send_multipart(wire.encode_command(command))   # the resend goes through
        assert wire.decode_command_result(dealer.recv_multipart()).unwrap().ok
        assert len(appender.appended) == 1
    finally:
        dealer.close()
        server.stop()


def _start(appender, **kwargs):
    ctx = zmq.Context.instance()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx, **kwargs)
    server.start()
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 2000)
    dealer.connect(server.bind_address)
    return server, dealer


def _reliable(event_type, data, message_id):
    return wire.encode_command(envelope.encode_event(event_type, "n0", data, message_id=message_id))


def test_locally_handled_types_skip_the_event_log_and_are_acked_when_handled():
    """Job attempts and 10 s metric bins are the VEM's own business (outbox to
    the CP / in-memory metrics), not event-log entries."""
    from axo_shared.messaging import acked_message_id

    appender, handled = _FakeAppender(), []
    server, dealer = _start(appender, local_handlers={
        models.METRIC_SUMMARY_REPORTED: lambda data: handled.append(data) or True,
    })
    try:
        data = {"component_kind": "endpoint", "component_id": "n0", "bin_start": 0.0, "bin_width_s": 10, "summaries": []}
        dealer.send_multipart(_reliable(models.METRIC_SUMMARY_REPORTED, data, "m-1"))
        result = wire.decode_command_result(dealer.recv_multipart()).unwrap()
        assert acked_message_id(result).unwrap() == "m-1"
        assert len(handled) == 1 and appender.appended == []
    finally:
        dealer.close()
        server.stop()


def test_a_local_handler_that_fails_is_not_acked():
    appender = _FakeAppender()
    server, dealer = _start(appender, local_handlers={models.METRIC_SUMMARY_REPORTED: lambda data: False})
    dealer.setsockopt(zmq.RCVTIMEO, 500)
    try:
        dealer.send_multipart(_reliable(models.METRIC_SUMMARY_REPORTED, {"bin_start": 0}, "m-2"))
        try:
            dealer.recv_multipart()
            assert False, "must not ack"
        except zmq.Again:
            pass
    finally:
        dealer.close()
        server.stop()


def test_unknown_event_types_are_acked_and_dropped_without_stopping_ingestion():
    """Regression: an event type with no stream name used to raise inside the
    receive thread and stop ingestion for good."""
    from axo_shared.messaging import acked_message_id

    appender = _FakeAppender()
    server, dealer = _start(appender)
    try:
        dealer.send_multipart(_reliable("SomethingFromTheFuture", {"x": 1}, "u-1"))
        assert acked_message_id(wire.decode_command_result(dealer.recv_multipart()).unwrap()).unwrap() == "u-1"
        event = models.EndpointStopped(endpoint_id="n0", uptime_ms=1.0)
        dealer.send_multipart(_reliable(models.ENDPOINT_STOPPED, event.model_dump(mode="json"), event.event_id))
        assert wire.decode_command_result(dealer.recv_multipart()).unwrap().ok   # still alive
        assert len(appender.appended) == 1
    finally:
        dealer.close()
        server.stop()


def test_router_keeps_receiving_after_a_message_it_fails_to_handle():
    """A crash while handling one message (or a receive error) used to end
    the receive loop silently: every endpoint's events then went unheard."""
    ctx = zmq.Context.instance()

    appender = _FakeAppender()
    server = IngestionRouterServer(bind_address="tcp://127.0.0.1:0", appender=appender, context=ctx)
    server.start()
    real_handle = server._handle_frames
    calls = {"n": 0}

    def flaky(frames):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a bug in handling")
        real_handle(frames)

    server._handle_frames = flaky
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 2000)
    dealer.connect(server.bind_address)
    try:
        event = models.EndpointStarted(endpoint_id="n0", router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556")
        _send_event(dealer, models.ENDPOINT_STARTED, "n0", event.model_dump(mode="json"))   # the one that crashes
        _send_event(dealer, models.ENDPOINT_STARTED, "n1", event.model_dump(mode="json"))
        assert wire.decode_command_result(dealer.recv_multipart()).unwrap().ok is True
        assert [a[0] for a in appender.appended] == ["endpoints-n1"]
    finally:
        dealer.close()
        server.stop()
