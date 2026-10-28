"""The VEM -> Control Plane channel, against a fake CP that acks like the real one will."""
import threading
import time

import zmq
from axo_shared import wire
from axo_shared.events import envelope
from axo_shared.messaging import build_ack, extract_message_id
from axo_shared.metrics import StatSummary
from axo_shared.metrics import names as m

from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.domain.events import models
from axo_vem.infrastructure.control_plane.cp_link import ControlPlaneLink


class FakeControlPlane:
    def __init__(self, ack=True):
        self.ack = ack
        self.received = []
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.ROUTER)
        port = self._socket.bind_to_random_port("tcp://127.0.0.1")
        self.uri = f"tcp://127.0.0.1:{port}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)
        while not self._stop.is_set():
            if not poller.poll(50):
                continue
            identity, *frames = self._socket.recv_multipart()
            command = wire.decode_command(frames).unwrap()
            event_type, _source, data = envelope.decode_event(command).unwrap()
            self.received.append((event_type, data))
            if self.ack:
                self._socket.send_multipart([identity, *wire.encode_command_result(build_ack(extract_message_id(command)))])

    def of(self, event_type):
        return [d for t, d in self.received if t == event_type]

    def close(self):
        self._stop.set()
        self._thread.join(1)
        self._socket.close(0)


class _Log:
    def __init__(self):
        self.appended = []

    def append_to_stream(self, stream, event_type, data):
        self.appended.append((stream, event_type, data))


STATUS = {"lifecycle": "Running", "health": "Healthy", "target_endpoints": 3, "min_endpoints": 1,
          "endpoints": {"running": 2, "starting": 1, "sleeping": 0}, "idle_since": None}


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_heartbeat_attempts_and_minute_summaries_reach_the_cp_and_get_acked():
    cp = FakeControlPlane()
    clock = [3600.0 * 10 + 125]          # 2 complete minutes in the store below
    metrics = MetricsStore(now_fn=lambda: clock[0])
    for start in range(36000, 36120, 10):
        stats = StatSummary()
        stats.add(5)
        metrics.ingest({"component_id": "ep-1", "bin_start": float(start), "bin_width_s": 10,
                        "summaries": [{"metric": m.REQUESTS_RECEIVED, "dims": {}, "stats": stats.to_dict()}]})
    link = ControlPlaneLink.connect(cp.uri, ":memory:", "ve-1", status_fn=lambda: STATUS, metrics=metrics,
                                    event_log=_Log(), now_fn=lambda: clock[0])
    try:
        beat = link.heartbeat()
        assert (beat.running, beat.starting, beat.target_endpoints) == (2, 1, 3)
        assert link.job_attempt({"job_id": "j1", "attempt": 1})
        assert link.send_minute_summaries() == 2
        assert link.send_minute_summaries() == 0                  # each minute only once
        assert _wait(lambda: link.pending() == 0)
        assert cp.of(models.JOB_ATTEMPT_RECORDED) == [{"job_id": "j1", "attempt": 1}]
        minutes = cp.of(models.METRIC_SUMMARY_REPORTED)
        assert [mi["bin_start"] for mi in minutes] == [36000.0, 36060.0]
        assert minutes[0]["summaries"][0]["stats"]["count"] == 6 and minutes[0]["component_kind"] == "vem"
        assert cp.of(models.VE_HEARTBEAT)[0]["lifecycle"] == "Running"
    finally:
        link.close()
        cp.close()


def test_nothing_is_lost_while_the_cp_doesnt_ack():
    cp = FakeControlPlane(ack=False)
    link = ControlPlaneLink.connect(cp.uri, ":memory:", "ve-1", status_fn=lambda: STATUS,
                                    metrics=MetricsStore(), event_log=_Log())
    try:
        link.job_attempt({"job_id": "j1"})
        assert _wait(lambda: len(cp.received) >= 1)
        assert link.pending() == 1                                # still kept for resending
        cp.ack = True
        assert link.flush(timeout_s=40)
    finally:
        link.close()
        cp.close()


def test_without_a_cp_job_attempts_go_to_the_vem_event_log():
    log = _Log()
    link = ControlPlaneLink("ve-1", status_fn=lambda: STATUS, metrics=MetricsStore(), event_log=log)
    assert not link.connected
    assert link.job_attempt({"job_id": "j1"})
    assert log.appended == [("usage-ve-1", models.JOB_ATTEMPT_RECORDED, {"job_id": "j1"})]
    assert link.heartbeat() is None and link.send_minute_summaries() == 0


class _Sender:
    def __init__(self):
        self.sent = []

    def publish(self, event_type, data):
        from option import Ok
        self.sent.append((event_type, data))
        return Ok(None)

    def stats(self):
        return {"pending": 0}


def test_object_state_is_sent_once_per_finished_hour_and_catches_up_after_a_restart():
    import mongomock

    from axo_vem.infrastructure.database.mongo.object_repository import MongoActiveObjectRepository

    objects = MongoActiveObjectRepository(mongomock.MongoClient()["vem"]["active_objects"])
    clock = {"t": 10 * 3600 + 120.0}
    sender = _Sender()
    link = ControlPlaneLink("ve-1", status_fn=lambda: STATUS, metrics=MetricsStore(), event_log=_Log(),
                            sender=sender, objects=objects, now_fn=lambda: clock["t"])
    assert link.send_object_state_hours() == 0                   # nothing reported yet
    objects.apply_sizes({"obj-1": 10}, stored_bytes=2048)
    assert link.send_object_state_hours() == 1
    assert sender.sent[-1] == (models.OBJECT_STATE_HOUR_REPORTED, {
        **sender.sent[-1][1], "virtual_environment_id": "ve-1", "hour_start": 9 * 3600.0, "stored_bytes": 2048})
    assert link.send_object_state_hours() == 0                   # same hour: not again
    clock["t"] += 3 * 3600                                       # down for 3 hours
    assert link.send_object_state_hours() == 3
    assert [d["hour_start"] for _t, d in sender.sent] == [9 * 3600.0, 10 * 3600.0, 11 * 3600.0, 12 * 3600.0]
    clock["t"] += 90 * 86400                                     # asleep for 90 days: 31 days at most
    assert link.send_object_state_hours() == 31 * 24


def test_the_heartbeat_lists_the_buckets_the_triggers_watch():
    import mongomock

    from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository

    col = mongomock.MongoClient()["vem"]["choreographies"]
    col.insert_many([
        {"_id": "c1", "deleted_at": None, "triggers": [{"bucket": "alice/raw", "created_by": "u1"},
                                                        {"bucket": "alice/raw", "created_by": "u1"}]},
        {"_id": "c2", "deleted_at": None, "triggers": [{"bucket": "team/in", "created_by": "u2"}]},
        {"_id": "c3", "deleted_at": "2026-10-01", "triggers": [{"bucket": "old/gone", "created_by": "u3"}]},
    ])
    repository = MongoChoreographyRepository(col)
    sender = _Sender()
    link = ControlPlaneLink("ve-1", status_fn=lambda: STATUS, metrics=MetricsStore(), event_log=_Log(),
                            sender=sender, triggers_fn=repository.trigger_buckets)
    link.heartbeat()
    assert sender.sent[-1][1]["triggers"] == [{"bucket": "alice/raw", "created_by": "u1"},
                                              {"bucket": "team/in", "created_by": "u2"}]


def test_a_stopping_vem_sends_the_minutes_still_in_memory_even_the_one_going_on():
    clock = {"t": 36000 + 95.0}                                   # 35 s into the second minute
    metrics = MetricsStore(now_fn=lambda: clock["t"])
    for start in (36000, 36060, 36070):
        stats = StatSummary()
        stats.add(5)
        metrics.ingest({"component_id": "ep-1", "bin_start": float(start), "bin_width_s": 10,
                        "summaries": [{"metric": m.JOB_TOTAL_MS, "dims": {"outcome": "completed"},
                                       "stats": stats.to_dict()}]})
    sender = _Sender()
    link = ControlPlaneLink("ve-1", status_fn=lambda: STATUS, metrics=metrics, event_log=_Log(),
                            sender=sender, now_fn=lambda: clock["t"])
    assert link.send_minute_summaries() == 1                      # only the finished minute, normally
    assert link.settle(timeout_s=1, reports_wait_s=0)
    minutes = [d for t, d in sender.sent if t == models.METRIC_SUMMARY_REPORTED]
    assert [mi["bin_start"] for mi in minutes] == [36000.0, 36060.0]
    assert minutes[1]["summaries"][0]["stats"]["count"] == 2     # the partial minute, not lost with the process
    parts = {mi["part"] for mi in minutes}
    assert len(parts) == 1 and None not in parts                 # one part per process
    restarted = ControlPlaneLink("ve-1", status_fn=lambda: STATUS, metrics=MetricsStore(), event_log=_Log(),
                                 sender=_Sender(), now_fn=lambda: clock["t"])
    assert restarted._part not in parts                          # a restarted VEM adds a second part
