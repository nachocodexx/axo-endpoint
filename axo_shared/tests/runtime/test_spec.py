from axo_shared.runtime.spec import RuntimeSpec


def test_defaults():
    spec = RuntimeSpec()
    assert spec.type == "process"
    assert spec.python_version == "3.11"
    assert spec.requirements == []
    assert spec.image is None
    assert spec.env_vars == {}
    assert spec.idle_ttl_seconds is None
    assert spec.max_invocations == 0
    assert spec.memory_limit_bytes is None
    assert spec.cpu_limit is None
    assert spec.max_concurrency == 1
    assert spec.max_duration_seconds == 0
    assert spec.max_attempts == 1 and spec.retry_backoff == "fixed" and spec.keep_result is None
    assert spec.call_style == "kwargs"


def test_to_dict_round_trips_through_from_dict():
    spec = RuntimeSpec(
        type="container",
        python_version="3.10",
        requirements=["pandas"],
        image="axo-runner:3.10",
        env_vars={"FOO": "bar"},
        idle_ttl_seconds=60.0,
        max_invocations=5,
        memory_limit_bytes=536870912,
        cpu_limit=0.5,
        max_concurrency=4,
        max_duration_seconds=120.0,
        max_attempts=4,
        retry_delay_seconds=2.5,
        retry_backoff="exponential",
        keep_result="30d",
        call_style="object",
    )
    assert RuntimeSpec.from_dict(spec.to_dict()) == spec


def test_from_dict_fills_in_defaults_for_missing_keys():
    spec = RuntimeSpec.from_dict({})
    assert spec == RuntimeSpec()


def test_from_dict_partial_overrides_only_given_keys():
    spec = RuntimeSpec.from_dict({"type": "container", "python_version": "3.10"})
    assert spec.type == "container"
    assert spec.python_version == "3.10"
    assert spec.requirements == []
    assert spec.idle_ttl_seconds is None


def test_the_old_params_ctx_style_is_refused_with_a_hint():
    import pytest
    from axo_shared.runtime.spec import RuntimeSpecError

    with pytest.raises(RuntimeSpecError, match="named parameters"):
        RuntimeSpec.from_dict({"call_style": "params_ctx"})


def test_attempts_are_checked_and_delays_grow_when_exponential():
    import pytest
    from axo_shared.runtime.spec import RuntimeSpecError, retry_delay

    for bad in ({"max_attempts": 0}, {"max_attempts": 11}, {"retry_delay_seconds": -1},
                {"retry_backoff": "random"}):
        with pytest.raises(RuntimeSpecError):
            RuntimeSpec.from_dict(bad)
    assert [retry_delay(a, 2, "exponential") for a in (1, 2, 3)] == [2, 4, 8]
    assert [retry_delay(a, 2, "fixed") for a in (1, 2, 3)] == [2, 2, 2]


def test_keep_result_choices_are_capped_by_the_longest():
    from axo_shared.runtime.results import KEEP_RESULT_CHOICES, capped, keep_seconds

    assert KEEP_RESULT_CHOICES == ("1h", "1d", "7d", "30d", "90d", "1y", "never")
    assert capped("never", "30d") == "30d" and capped("1d", "30d") == "1d"
    assert keep_seconds("7d") == 7 * 86400 and keep_seconds("never") is None
