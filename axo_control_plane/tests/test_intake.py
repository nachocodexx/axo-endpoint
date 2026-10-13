import io
import tarfile
import time

from axo_shared.messaging import InMemoryJournal, OutboxMessage, ReliableSender, SqliteJournal, ZmqDealerTransport

from axo_control_plane.application.intake import VemIntake
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.infrastructure.intake_router import IntakeRouter


def _running_ve(platform, name="ml"):
    actor = platform.user("alice")
    ve_id = platform.buy(actor, name=name)["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"lifecycle": V.RUNNING}})
    return actor, ve_id


def _attempt(ve_id, job="j1", attempt=1):
    return {"virtual_environment_id": ve_id, "job_id": job, "attempt": attempt, "trace_id": "t",
            "outcome": "completed", "owner_type": "user", "owner_id": "u", "started_by_type": "user",
            "started_by_id": "u", "function_id": "f", "function_version": 1, "endpoint_cores": 1.0,
            "endpoint_ram_mb": 1024, "received_at": "2026-10-12T10:00:00+00:00", "execution_ms": 120.0}


def test_heartbeat_keeps_only_the_latest_and_records_changes(platform):
    intake = VemIntake(platform.db, platform.history, platform.notifications, now_fn=platform.clock)
    alice, ve_id = _running_ve(platform)
    hb = {"virtual_environment_id": ve_id, "lifecycle": "Running", "health": "Healthy", "target_endpoints": 1,
          "min_endpoints": 1, "running": 1, "idle_since": "2026-10-12T10:00:00+00:00"}
    for _ in range(3):
        assert intake.handle("VirtualEnvironmentHeartbeat", f"vem:{ve_id}", hb)
    changes = platform.history.of_type("VirtualEnvironmentStateChanged")
    assert len(changes) == 1                               # Healthy recorded once, not every beat
    assert intake.handle("VirtualEnvironmentHeartbeat", f"vem:{ve_id}", {**hb, "health": "Degraded"})
    assert len(platform.history.of_type("VirtualEnvironmentStateChanged")) == 2
    record = platform.ves.record(ve_id)
    assert record.health == "Degraded" and record.idle_since is not None
    assert any("degraded" in n["text"] for n in platform.notifications.list(alice.user_id))


def test_a_vem_cannot_report_for_another_ve(platform):
    intake = VemIntake(platform.db, platform.history, platform.notifications, now_fn=platform.clock)
    _, ve_id = _running_ve(platform)
    assert intake.handle("JobAttemptRecorded", "vem:someone-else", _attempt(ve_id))   # dropped (acked)
    assert platform.db["job_attempts"].count_documents({}) == 0


def test_job_attempts_are_saved_once(platform):
    intake = VemIntake(platform.db, platform.history, platform.notifications, now_fn=platform.clock)
    _, ve_id = _running_ve(platform)
    for _ in range(2):
        assert intake.handle("JobAttemptRecorded", f"vem:{ve_id}", _attempt(ve_id))
    assert platform.db["job_attempts"].count_documents({}) == 1
    assert len(platform.history.of_type("JobAttemptRecorded")) == 1


def test_end_to_end_reliable_delivery_from_a_vem(platform):
    intake = VemIntake(platform.db, platform.history, platform.notifications, now_fn=platform.clock)
    _, ve_id = _running_ve(platform)
    router = IntakeRouter("tcp://127.0.0.1:0", intake.handle)
    router.start()
    sender = ReliableSender(InMemoryJournal(), ZmqDealerTransport(router.bind_address), source_id=f"vem:{ve_id}")
    sender.start()
    try:
        sender.publish("JobAttemptRecorded", _attempt(ve_id, "j1"))
        sender.publish("JobAttemptRecorded", _attempt(ve_id, "j2"))
        sender.publish("MetricSummaryReported", {"virtual_environment_id": ve_id, "component_kind": "vem",
                                                 "component_id": ve_id, "bin_start": 60.0, "bin_width_s": 60,
                                                 "summaries": []})
        deadline = time.monotonic() + 5
        while sender.stats().get("pending", 1) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sender.stats()["pending"] == 0
        assert platform.db["job_attempts"].count_documents({"ve_id": ve_id}) == 2
        assert platform.db["metric_minutes"].count_documents({"ve_id": ve_id}) == 1
    finally:
        sender.stop()
        router.stop()


def test_salvaging_a_deleted_vems_outbox(platform, tmp_path):
    intake = VemIntake(platform.db, platform.history, platform.notifications, now_fn=platform.clock)
    _, ve_id = _running_ve(platform)
    journal = SqliteJournal(str(tmp_path / f"vem-{ve_id}.db"))
    for job in ("a", "b"):
        journal.append(OutboxMessage(message_id=job, event_type="JobAttemptRecorded", source_id=f"vem:{ve_id}",
                                     data=_attempt(ve_id, job), created_at=0.0, attempts=0, next_attempt_at=0.0))
    journal.close()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(str(tmp_path / f"vem-{ve_id}.db"), arcname=f"outbox/vem-{ve_id}.db")
    assert intake.salvage(ve_id, buf.getvalue()) == 2
    assert platform.db["job_attempts"].count_documents({"ve_id": ve_id}) == 2
