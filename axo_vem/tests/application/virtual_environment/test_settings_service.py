import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.domain.virtual_environment.settings import PlanLimits, VeSettings


@dataclass
class _Record:
    type: str
    data: bytes
    commit_position: int = 0


class _Log:
    """Fake Kurrent: an append-only list per stream, readable back."""

    def __init__(self):
        self.streams = {}

    def append_to_stream(self, stream_name, event_type, data):
        self.streams.setdefault(stream_name, []).append(_Record(event_type, json.dumps(data).encode()))

    def get_stream(self, stream_name):
        if stream_name not in self.streams:
            raise KeyError(stream_name)
        return self.streams[stream_name]


LIMITS = PlanLimits(max_endpoints=8, max_retention_days=30)


def test_changes_are_recorded_as_events_and_survive_a_restart():
    log = _Log()
    first = VeSettingsService("ml-prod", LIMITS, log)
    first.change({"target_endpoints": 3}, changed_by_id="luis", reason="more traffic")
    first.change({"min_endpoints": 2, "retention_days": 7}, changed_by_id="ana")

    (record_1, record_2) = log.streams["ve-settings-ml-prod"]
    data = json.loads(record_1.data)
    assert record_1.type == models.VE_SETTINGS_CHANGED
    assert data["changes"] == {"target_endpoints": 3}
    assert (data["changed_by_id"], data["reason"]) == ("luis", "more traffic")

    restarted = VeSettingsService("ml-prod", LIMITS, log)
    assert restarted.load(log) == 2
    assert restarted.current() == VeSettings(target_endpoints=3, min_endpoints=2, retention_days=7)


def test_a_new_ve_starts_with_defaults():
    service = VeSettingsService("new-ve", LIMITS, _Log())
    assert service.load(_Log()) == 0
    assert service.current() == VeSettings()


def test_invalid_changes_record_nothing():
    log = _Log()
    service = VeSettingsService("ml-prod", LIMITS, log)
    with pytest.raises(ConflictError, match="plan maximum"):
        service.change({"target_endpoints": 99})
    assert log.streams == {}


def test_a_change_to_the_same_value_records_nothing():
    log = _Log()
    service = VeSettingsService("ml-prod", LIMITS, log)
    service.change({"target_endpoints": 1})
    assert log.streams == {}


def test_listeners_hear_about_every_change():
    service = VeSettingsService("ml-prod", LIMITS, _Log())
    heard = []
    service.on_change(heard.append)
    service.change({"target_endpoints": 2}, changed_by_type="policy", changed_by_id="fast-responses")
    assert heard == [VeSettings(target_endpoints=2)]


def test_retention_cutoff_follows_the_setting_or_plan():
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    service = VeSettingsService("ml-prod", LIMITS, _Log(), now_fn=lambda: now)
    assert service.retention_cutoff() == now - timedelta(days=30)
    service.change({"retention_days": 7})
    assert service.retention_cutoff() == now - timedelta(days=7)
    unlimited = VeSettingsService("x", PlanLimits(max_endpoints=1), _Log(), now_fn=lambda: now)
    assert unlimited.retention_cutoff() is None


def test_the_quota_holds_target_times_endpoint_size():
    limits = PlanLimits(max_endpoints=10, quota_cpu_cores=4.0, quota_memory_mb=8192)
    service = VeSettingsService("ml-prod", limits, _Log())
    service.change({"target_endpoints": 2, "new_endpoint_cpu_cores": 2.0, "new_endpoint_memory_mb": 4096})
    with pytest.raises(ConflictError) as err:
        service.change({"target_endpoints": 3})
    assert "needs 6 CPU, the VE quota is 4 CPU" in str(err.value)


def test_initial_settings_apply_once_to_a_new_ve():
    log = _Log()
    initial = {"target_endpoints": 2, "min_endpoints": 1, "new_endpoint_cpu_cores": 2.0, "not_a_setting": 1}
    service = VeSettingsService("ml-prod", LIMITS, log, initial=initial)
    service.load(log)
    assert service.current().target_endpoints == 2 and service.current().new_endpoint_cpu_cores == 2.0
    service.change({"target_endpoints": 5})
    again = VeSettingsService("ml-prod", LIMITS, log, initial=initial)
    again.load(log)                                   # history exists: initial values are ignored
    assert again.current().target_endpoints == 5


def test_new_limits_apply_live_lower_the_target_and_survive_a_restart():
    log = _Log()
    service = VeSettingsService("ml-prod", PlanLimits(max_endpoints=10), log)
    service.load(log)
    service.change({"target_endpoints": 6, "min_endpoints": 4})
    service.set_limits(PlanLimits(max_endpoints=3, quota_cpu_cores=3.0, quota_memory_mb=3072), by="cp")
    assert service.current().target_endpoints == 3 and service.current().min_endpoints == 3
    restarted = VeSettingsService("ml-prod", PlanLimits(max_endpoints=10), log)
    restarted.load(log)
    assert restarted.limits.max_endpoints == 3 and restarted.limits.quota_cpu_cores == 3.0
