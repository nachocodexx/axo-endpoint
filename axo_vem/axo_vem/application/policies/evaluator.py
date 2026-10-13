from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.application.policies.policy_service import PolicyService
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.errors import DomainError
from axo_vem.domain.policies.policy import COMPARATORS, ENDPOINT, RUNNER, ScalingPolicy

# (function_id, version) -> current max_concurrency
CurrentRunners = Callable[[str, int], Optional[int]]
# (function_id, version, new max_concurrency) -> None; raises DomainError on failure
SetRunners = Callable[[str, int, int], None]


class PolicyEvaluator:
    """Runs the VE's scaling policies, every few seconds, in the VEM:

    - a policy fires when its condition has held for ``duration_s`` and its
      cooldown has passed since it last acted;
    - endpoint policies change target_endpoints through the settings (the
      same path as a manual change, recorded as changed by the policy);
      runner policies change one function's max_concurrency in place;
    - when several policies fire for the same target, scale-up wins;
    - targets stay within the policy's own min/max, the VE's min_endpoints
      and the plan maximum; scale-down always goes through Draining (the
      reconciler);
    - dry-run policies only record what they would have done;
    - nothing happens while policies are paused (e.g. after a manual change)
      or when the plan has no policies (Free);
    - only while the VE is running (not stopped, sleeping, starting,
      restoring or being deleted).
    Every decision is recorded with its reason."""

    def __init__(
        self,
        policies: PolicyService,
        settings: VeSettingsService,
        metrics: MetricsStore,
        current_runners: CurrentRunners,
        set_runners: SetRunners,
        now_fn: Callable[[], float] = time.time,
        is_active: Callable[[], bool] = lambda: True,
    ) -> None:
        self._policies = policies
        self._is_active = is_active
        self._settings = settings
        self._metrics = metrics
        self._current_runners = current_runners
        self._set_runners = set_runners
        self._now_fn = now_fn
        self._holding_since: Dict[str, float] = {}
        self._last_acted: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.last_errors: List[str] = []

    def run_forever(self, stop_event: threading.Event, interval_s: float = 10.0) -> None:
        while not stop_event.wait(interval_s):
            try:
                self.tick()
            except Exception as exc:  # one bad tick must not stop policies for good
                self.last_errors.append(repr(exc))

    def tick(self) -> None:
        with self._lock:
            self.last_errors = []
            if not self._policies.allowed or self._policies.is_paused() or not self._is_active():
                # Also forget conditions in progress: after a stop/start the
                # duration starts counting again from fresh data.
                self._holding_since.clear()
                return
            now = self._now_fn()
            firing = [(p, v) for p, v in self._firing(now)]
            self._act_on_endpoints([(p, v) for p, v in firing if p.kind == ENDPOINT], now)
            by_function: Dict[Tuple[str, int], List[Tuple[ScalingPolicy, float]]] = {}
            for policy, value in firing:
                if policy.kind == RUNNER:
                    by_function.setdefault((policy.function_id, policy.function_version), []).append((policy, value))
            for (function_id, version), group in by_function.items():
                self._act_on_runners(function_id, version, group, now)

    def _firing(self, now: float) -> List[Tuple[ScalingPolicy, float]]:
        """Policies whose condition has held long enough and whose cooldown is over."""
        firing = []
        for policy in self._policies.list():
            if not policy.enabled:
                self._holding_since.pop(policy.policy_id, None)
                continue
            value = self._metrics.value(policy.metric, policy.statistic, policy.window_s, policy.metric_dims())
            if value is None or not COMPARATORS[policy.comparator](value, policy.threshold):
                self._holding_since.pop(policy.policy_id, None)
                continue
            since = self._holding_since.setdefault(policy.policy_id, now)
            if now - since < policy.duration_s:
                continue
            if now - self._last_acted.get(policy.policy_id, float("-inf")) < policy.cooldown_s:
                continue
            firing.append((policy, value))
        return firing

    def _pick(self, group: List[Tuple[ScalingPolicy, float]]) -> Tuple[ScalingPolicy, float]:
        """Scale-up wins; among ups the biggest step, among downs the smallest."""
        ups = [item for item in group if item[0].delta > 0]
        if ups:
            return max(ups, key=lambda item: item[0].delta)
        return max(group, key=lambda item: item[0].delta)

    def _reason(self, policy: ScalingPolicy, value: float, before: int, after: int, what: str) -> str:
        verb = "added" if after > before else "removed"
        return (f"{policy.name} {verb} {abs(after - before)} {what}: {policy.statistic} {policy.metric} = "
                f"{value:.4g} {policy.comparator} {policy.threshold:g} for {policy.duration_s:g} s")

    def _done(self, policy: ScalingPolicy, now: float) -> None:
        self._last_acted[policy.policy_id] = now
        self._holding_since.pop(policy.policy_id, None)

    def _act_on_endpoints(self, firing: List[Tuple[ScalingPolicy, float]], now: float) -> None:
        if not firing:
            return
        policy, value = self._pick(firing)
        settings = self._settings.current()
        limits = self._settings.limits
        before = settings.target_endpoints
        upper = min(policy.max_value, limits.max_endpoints)
        lower = max(policy.min_value, settings.min_endpoints)
        after = max(lower, min(upper, before + policy.delta))
        if after == before:
            self._done(policy, now)  # at its limit: wait a cooldown before re-checking
            return
        reason = self._reason(policy, value, before, after, "endpoint" if abs(after - before) == 1 else "endpoints")
        if not policy.dry_run:
            changes: Dict[str, object] = {"target_endpoints": after}
            if after > before and policy.new_endpoint_cpu_cores and policy.new_endpoint_memory_mb:
                changes["new_endpoint_cpu_cores"] = policy.new_endpoint_cpu_cores
                changes["new_endpoint_memory_mb"] = policy.new_endpoint_memory_mb
            try:
                self._settings.change(changes, changed_by_type="policy", changed_by_id=policy.policy_id, reason=reason)
            except DomainError as exc:
                self.last_errors.append(f"{policy.name}: {exc}")
                return
        self._policies.record_decision(
            policy_id=policy.policy_id, policy_name=policy.name, kind=ENDPOINT,
            before=before, after=after, dry_run=policy.dry_run, reason=reason,
        )
        self._done(policy, now)

    def _act_on_runners(
        self, function_id: str, version: int, firing: List[Tuple[ScalingPolicy, float]], now: float,
    ) -> None:
        policy, value = self._pick(firing)
        before = self._current_runners(function_id, version) or 1
        after = max(max(1, policy.min_value), min(policy.max_value, before + policy.delta))
        if after == before:
            self._done(policy, now)
            return
        reason = self._reason(policy, value, before, after, "runner" if abs(after - before) == 1 else "runners")
        if not policy.dry_run:
            try:
                self._set_runners(function_id, version, after)
            except DomainError as exc:
                self.last_errors.append(f"{policy.name}: {exc}")
                return
        self._policies.record_decision(
            policy_id=policy.policy_id, policy_name=policy.name, kind=RUNNER,
            function_id=function_id, function_version=version,
            before=before, after=after, dry_run=policy.dry_run, reason=reason,
        )
        self._done(policy, now)
