import pytest

from axo_vem.domain.virtual_environment.settings import PlanLimits, VeSettings, validate_changes

LIMITS = PlanLimits(max_endpoints=8, max_retention_days=90, max_queue_limit=1000, max_wait_seconds=600)


def errors(changes, current=VeSettings(), limits=LIMITS):
    return validate_changes(current, changes, limits)


def test_valid_changes_pass():
    assert errors({"target_endpoints": 4, "min_endpoints": 2, "endpoint_idle_seconds": 1200}) == []


@pytest.mark.parametrize("changes, fragment", [
    ({"target_endpoints": 9}, "plan maximum (8)"),
    ({"min_endpoints": 3, "target_endpoints": 2}, "below min_endpoints"),
    ({"target_endpoints": -1}, "≥ 0"),
    ({"endpoint_idle_seconds": 0}, "endpoint_idle_seconds"),
    ({"wake_wait_seconds": 0}, "wake_wait_seconds"),
    ({"default_max_wait_seconds": -2}, "default_max_wait_seconds"),
    ({"default_max_wait_seconds": 601}, "platform maximum (600 s)"),
    ({"endpoint_queue_limit": 5000}, "platform maximum (1000)"),
    ({"retention_days": 120}, "plan maximum (90)"),
    ({"auto_replace_failed": "yes"}, "true or false"),
    ({"colour": "red"}, "unknown setting"),
])
def test_invalid_changes_are_explained(changes, fragment):
    assert any(fragment in e for e in errors(changes)), errors(changes)


def test_waiting_forever_can_be_disallowed_by_the_platform():
    strict = PlanLimits(max_endpoints=8, allow_wait_forever=False)
    current = VeSettings(default_max_wait_seconds=10)
    assert "not allowed" in errors({"default_max_wait_seconds": -1}, current, strict)[0]


def test_effective_retention_falls_back_to_the_plan_maximum():
    assert VeSettings().effective_retention_days(LIMITS) == 90
    assert VeSettings(retention_days=7).effective_retention_days(LIMITS) == 7
