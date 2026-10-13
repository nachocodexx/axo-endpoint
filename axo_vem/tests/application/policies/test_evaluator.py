"""Scaling policies, end to end against real settings/metrics and a fake clock."""
import pytest
from axo_shared.metrics import StatSummary
from axo_shared.metrics import names as m

from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.application.policies.evaluator import PolicyEvaluator
from axo_vem.application.policies.policy_service import PolicyService
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.policies.policy import ScalingPolicy, TEMPLATES, validate
from axo_vem.domain.virtual_environment.settings import PlanLimits

from tests.application.virtual_environment.test_reconciler import Clock, _Log


def p95_slow(**overrides):
    base = dict(name="fast-responses", kind="endpoint", metric=m.JOB_TOTAL_MS, statistic="p95", comparator=">",
                threshold=500, duration_s=120, delta=1, min_value=1, max_value=4, cooldown_s=300)
    return ScalingPolicy(**{**base, **overrides})


@pytest.fixture
def setup():
    clock = Clock()
    log = _Log(clock)
    settings = VeSettingsService("ve", PlanLimits(max_endpoints=6, policies_allowed=True), log)
    policies = PolicyService("ve", log, policies_allowed=True, now_fn=clock)
    metrics = MetricsStore(now_fn=clock)
    runners = {("f1", 1): 2}
    evaluator = PolicyEvaluator(
        policies, settings, metrics,
        current_runners=lambda f, v: runners.get((f, v)),
        set_runners=lambda f, v, n: runners.__setitem__((f, v), n),
        now_fn=clock,
    )

    def response_times(*values, dims=None, metric=m.JOB_TOTAL_MS):
        stats = StatSummary()
        for v in values:
            stats.add(v)
        metrics.ingest({"component_id": "ep-1", "bin_start": clock() - 10, "bin_width_s": 10,
                        "summaries": [{"metric": metric, "dims": dims or {}, "stats": stats.to_dict()}]})

    return clock, settings, policies, evaluator, response_times, runners, log


def test_fires_only_after_the_condition_holds_for_its_duration(setup):
    clock, settings, policies, evaluator, slow, *_ = setup
    policies.save(p95_slow(), by="luis")
    slow(800, 820)
    evaluator.tick()
    assert settings.current().target_endpoints == 1               # just started holding
    clock.advance(121)
    slow(810)
    evaluator.tick()
    assert settings.current().target_endpoints == 2
    (decision,) = policies.decisions()
    assert decision["reason"].startswith("fast-responses added 1 endpoint: p95 job.total_ms = ")
    assert "> 500 for 120 s" in decision["reason"]


def test_cooldown_and_max_are_respected(setup):
    clock, settings, policies, evaluator, slow, *_ = setup
    policies.save(p95_slow(duration_s=0, cooldown_s=300, max_value=2), by="luis")
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 2
    clock.advance(60)
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 2               # cooling down
    clock.advance(300)
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 2               # at the policy's max


def test_scale_up_wins_over_scale_down(setup):
    clock, settings, policies, evaluator, slow, *_ = setup
    settings.change({"target_endpoints": 3})
    policies.save(p95_slow(duration_s=0), by="luis")
    policies.save(p95_slow(name="shrink", duration_s=0, delta=-1, comparator=">", threshold=0), by="luis")
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 4


def test_dry_run_records_but_changes_nothing(setup):
    clock, settings, policies, evaluator, slow, *_ = setup
    policies.save(p95_slow(duration_s=0, dry_run=True), by="luis")
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 1
    assert policies.decisions()[0]["dry_run"] is True and policies.decisions()[0]["after"] == 2


def test_pause_stops_policies_until_it_ends(setup):
    clock, settings, policies, evaluator, slow, *_ = setup
    policies.save(p95_slow(duration_s=0), by="luis")
    policies.pause(1800, "manual change")
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 1
    clock.advance(1801)
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 2


def test_runner_policy_changes_one_functions_runners_in_place(setup):
    clock, settings, policies, evaluator, slow, runners, _ = setup
    policies.save(p95_slow(name="busy-f1", kind="runner", function_id="f1", function_version=1,
                           metric=m.JOB_WAIT_MS, statistic="avg", threshold=1000, duration_s=0, max_value=8), by="ana")
    slow(1500, dims={m.DIM_FUNCTION: "f1"}, metric=m.JOB_WAIT_MS)
    evaluator.tick()
    assert runners[("f1", 1)] == 3
    assert policies.decisions()[0]["kind"] == "runner"


def test_free_plan_has_no_policies(setup):
    clock, settings, policies, evaluator, slow, runners, log = setup
    free = PolicyService("ve", log, policies_allowed=False, now_fn=clock)
    with pytest.raises(ConflictError, match="plan"):
        free.save(p95_slow(), by="luis")


def test_policies_survive_a_vem_restart(setup):
    clock, settings, policies, evaluator, slow, runners, log = setup
    saved = policies.save(p95_slow(), by="luis")
    policies.pause(600, "manual change")
    again = PolicyService("ve", log, policies_allowed=True, now_fn=clock)
    again.load(log)
    assert again.get(saved.policy_id).name == "fast-responses"
    assert again.is_paused()


def test_every_template_is_a_valid_policy():
    for name, template in TEMPLATES.items():
        extra = {"function_id": "f1", "function_version": 1} if template["policy"]["kind"] == "runner" else {}
        assert validate(ScalingPolicy(name=name, **template["policy"], **extra)) == [], name


def test_policies_do_nothing_while_the_ve_is_not_running(setup):
    clock, settings, policies, _evaluator, slow, runners, _log = setup
    running = [False]
    metrics = _evaluator._metrics
    evaluator = PolicyEvaluator(policies, settings, metrics, lambda f, v: runners.get((f, v)),
                                lambda f, v, n: None, now_fn=clock, is_active=lambda: running[0])
    policies.save(p95_slow(duration_s=0), by="luis")
    slow(900)
    evaluator.tick()
    assert settings.current().target_endpoints == 1 and policies.decisions() == []
    running[0] = True
    evaluator.tick()
    assert settings.current().target_endpoints == 2
