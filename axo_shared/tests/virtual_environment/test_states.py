import pytest

from axo_shared.virtual_environment import ENDPOINT_STATES, VE_HEALTHS, VE_LIFECYCLES, compute_health

BASE = dict(vem_up=True, has_leader=True, running=5, failed=0, min_endpoints=2)


@pytest.mark.parametrize("overrides,expected", [
    ({}, "Healthy"),
    ({"failed": 2, "running": 3}, "Degraded"),          # some failed, still >= min
    ({"running": 1}, "Unavailable"),                    # below min
    ({"running": 1, "failed": 4}, "Unavailable"),       # below min wins over degraded
    ({"vem_up": False}, "Unavailable"),
    ({"has_leader": False}, "Unavailable"),
    ({"running": 2, "min_endpoints": 2}, "Healthy"),    # exactly min is fine
])
def test_health_rules(overrides, expected):
    assert compute_health("Running", **{**BASE, **overrides}) == expected


def test_scaling_up_with_new_endpoints_starting_is_not_degraded():
    # Target 6, 3 running, 1 starting, 2 failed: degraded only because of the 2 failures.
    assert compute_health("Scaling", **{**BASE, "running": 3, "failed": 2}) == "Degraded"
    # Same scale-up without failures: starting endpoints are neutral.
    assert compute_health("Scaling", **{**BASE, "running": 3, "failed": 0}) == "Healthy"


@pytest.mark.parametrize("lifecycle", ["Stopped", "Sleeping"])
def test_no_health_while_stopped_or_sleeping(lifecycle):
    assert compute_health(lifecycle, **{**BASE, "vem_up": False, "running": 0}) is None


def test_state_vocabularies():
    assert "Restoring" in VE_LIFECYCLES and "Sleeping" in VE_LIFECYCLES
    assert VE_HEALTHS == ("Healthy", "Degraded", "Unavailable")
    assert "Draining" in ENDPOINT_STATES
