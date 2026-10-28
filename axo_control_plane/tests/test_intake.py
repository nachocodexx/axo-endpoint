import io
import tarfile
import time

from axo_shared.messaging import InMemoryJournal, OutboxMessage, ReliableSender, SqliteJournal, ZmqDealerTransport

from axo_control_plane.application.intake import VemIntake
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.infrastructure.intake_router import IntakeRouter


def _intake(platform):
    return VemIntake(platform.db, platform.history, platform.notifications, platform.catalog, now_fn=platform.clock)


def _running_ve(platform, name="ml"):
    actor = platform.user("alice")
    ve_id = platform.buy(actor, name=name)["ve"]["ve_id"]
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"lifecycle": V.RUNNING}})
    return actor, ve_id


def _attempt(ve_id, job="j1", attempt=1, **changes):
    return {**{"virtual_environment_id": ve_id, "job_id": job, "attempt": attempt, "trace_id": "t",
            "outcome": "completed", "owner_type": "user", "owner_id": "u", "started_by_type": "user",
            "started_by_id": "u", "function_id": "f", "function_version": 1, "endpoint_cores": 1.0,
            "endpoint_ram_mb": 1024, "received_at": "2026-10-12T10:00:00+00:00", "execution_ms": 120.0}, **changes}


def test_a_heartbeat_after_the_vem_was_down_clears_its_reason(platform):
    intake = _intake(platform)
    _, ve_id = _running_ve(platform)
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {
        "health": "Unavailable", "lifecycle_reason": V.VEM_DOWN_REASON, "vem_down_since": platform.clock()}})
    hb = {"virtual_environment_id": ve_id, "lifecycle": "Running", "health": "Healthy", "target_endpoints": 1,
          "min_endpoints": 1, "running": 1}
    assert intake.handle("VirtualEnvironmentHeartbeat", f"vem:{ve_id}", hb)
    record = platform.ves.record(ve_id)
    assert record.lifecycle_reason == "" and record.vem_down_since is None and record.health == "Healthy"


def test_heartbeat_keeps_only_the_latest_and_records_changes(platform):
    intake = _intake(platform)
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
    intake = _intake(platform)
    _, ve_id = _running_ve(platform)
    assert intake.handle("JobAttemptRecorded", "vem:someone-else", _attempt(ve_id))   # dropped (acked)
    assert platform.db["job_attempts"].count_documents({}) == 0


def test_job_attempts_are_saved_once(platform):
    intake = _intake(platform)
    _, ve_id = _running_ve(platform)
    for _ in range(2):
        assert intake.handle("JobAttemptRecorded", f"vem:{ve_id}", _attempt(ve_id))
    assert platform.db["job_attempts"].count_documents({}) == 1
    assert len(platform.history.of_type("JobAttemptRecorded")) == 1


def test_end_to_end_reliable_delivery_from_a_vem(platform):
    intake = _intake(platform)
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
    intake = _intake(platform)
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


def test_each_attempt_is_priced_once_at_the_price_of_its_finish_time(platform):
    intake = _intake(platform)
    _, ve_id = _running_ve(platform)
    finished = platform.clock() - 3600
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}",
                  _attempt(ve_id, "j1", execution_ms=2400.0, endpoint_cores=2.0, endpoint_ram_mb=4096,
                           finished_at=finished))
    # A price change after the job finished doesn't touch it, nor a resend.
    prices = platform.catalog.prices()
    platform.catalog.set_prices(prices.__class__.from_dict({**prices.to_dict(), "per_core_second_ucents": 9_999}),
                                by="admin")
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}", _attempt(ve_id, "j1", execution_ms=2400.0))
    cost = platform.db["job_attempts"].find_one({"_id": "j1:1"})["cost"]
    assert (cost["core_seconds"], cost["gb_seconds"]) == (4.8, 9.6)
    assert cost["request_ucents"] == prices.per_request_ucents
    assert cost["compute_ucents"] == round(4.8 * prices.per_core_second_ucents)
    assert cost["memory_ucents"] == round(9.6 * prices.per_gb_second_ucents)
    assert cost["total_ucents"] == cost["request_ucents"] + cost["compute_ucents"] + cost["memory_ucents"]
    # A job finishing after the change gets the new price.
    intake.handle("JobAttemptRecorded", f"vem:{ve_id}",
                  _attempt(ve_id, "j2", execution_ms=1000.0, finished_at=platform.clock()))
    assert platform.db["job_attempts"].find_one({"_id": "j2:1"})["cost"]["compute_ucents"] == 9_999


def test_platform_failures_and_queue_timeouts_cost_nothing(platform):
    intake = _intake(platform)
    _, ve_id = _running_ve(platform)
    for job, outcome in (("p", "failed_platform"), ("q", "timed_out_in_queue"), ("c", "cancelled")):
        intake.handle("JobAttemptRecorded", f"vem:{ve_id}", _attempt(ve_id, job, outcome=outcome))
    costs = {d["job_id"]: d["cost"] for d in platform.db["job_attempts"].find()}
    assert costs["p"]["total_ucents"] == costs["q"]["total_ucents"] == 0
    assert not costs["p"]["billable"] and costs["p"]["core_seconds"] == 0.12
    assert costs["c"]["billable"] and costs["c"]["total_ucents"] > 0     # cancelled: billed for the time it ran
