import pytest

from axo_vem.domain.compute.runner_size import MB, with_runner_size
from axo_vem.domain.errors import InvalidRequestError
from axo_vem.domain.virtual_environment.settings import PlanLimits

FREE = PlanLimits(runner_cpu_cores=0.5, runner_memory_mb=512, runner_max_cpu_cores=0.5, runner_max_memory_mb=512)


def test_a_container_function_without_a_size_gets_the_plans_default():
    spec = with_runner_size({"type": "container", "python_version": "3.11"}, FREE, 1.0, 1024)
    assert spec["cpu_limit"] == 0.5 and spec["memory_limit_bytes"] == 512 * MB


def test_a_process_function_is_left_alone():
    assert with_runner_size({"type": "process"}, FREE, 1.0, 1024) == {"type": "process"}


def test_a_size_over_the_plans_most_is_refused():
    with pytest.raises(InvalidRequestError) as err:
        with_runner_size({"type": "container", "cpu_limit": 1.0}, FREE, 1.0, 1024)
    assert err.value.error_name == "RUNNER_SIZE_INVALID" and "up to 0.5 CPU" in str(err.value)


def test_a_size_that_cant_fit_in_an_endpoint_is_refused():
    # no plan rule, but a 1 CPU / 1 GB endpoint keeps 0.25 CPU / 256 MB for itself
    with pytest.raises(InvalidRequestError) as err:
        with_runner_size({"type": "container", "cpu_limit": 0.5, "memory_limit_bytes": 1024 * MB}, PlanLimits(), 1.0, 1024)
    assert "768 MB left for runners" in str(err.value)


def test_a_chosen_size_within_the_limits_is_kept():
    spec = with_runner_size({"type": "container", "cpu_limit": 0.25, "memory_limit_bytes": 256 * MB}, FREE, 1.0, 1024)
    assert spec["cpu_limit"] == 0.25 and spec["memory_limit_bytes"] == 256 * MB


def test_the_default_shrinks_to_fit_a_small_endpoint():
    standard = PlanLimits(runner_cpu_cores=1.0, runner_memory_mb=1024, runner_max_cpu_cores=4.0, runner_max_memory_mb=8192)
    spec = with_runner_size({"type": "container"}, standard, 1.0, 1024)
    assert spec["cpu_limit"] == 0.75 and spec["memory_limit_bytes"] == 768 * MB
