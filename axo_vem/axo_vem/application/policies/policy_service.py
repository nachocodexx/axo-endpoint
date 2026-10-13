from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from axo_vem.domain.errors import ConflictError, NotFoundError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.policies.policy import ScalingPolicy, stream_name, validate


class PolicyService:
    """This VE's scaling policies, their pause, and the history of what they
    did -- all events in one stream of the VEM's own log, rebuilt on start.
    Policies are evaluated in the VEM (PolicyEvaluator), so they keep working
    while the Control Plane is down."""

    def __init__(
        self,
        virtual_environment_id: str,
        event_publisher: EventPublisher,
        policies_allowed: bool,
        now_fn: Callable[[], float] = time.time,
        keep_decisions: int = 500,
    ) -> None:
        self._ve_id = virtual_environment_id
        self._events = event_publisher
        self._allowed = policies_allowed
        self._now_fn = now_fn
        self._keep = keep_decisions
        self._policies: Dict[str, ScalingPolicy] = {}
        self._paused_until = 0.0
        self._pause_reason: Optional[str] = None
        self._decisions: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def allowed(self) -> bool:
        return self._allowed

    # ── persistence ──────────────────────────────────────────────────────────
    def load(self, reader) -> None:
        try:
            records = reader.get_stream(stream_name(self._ve_id))
        except Exception:
            records = []
        with self._lock:
            for record in records or []:
                self._apply_locked(record.type, json.loads(record.data.decode("utf-8")))

    def _apply_locked(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == models.POLICY_SAVED:
            policy = ScalingPolicy.from_dict(data["policy"])
            self._policies[policy.policy_id] = policy
        elif event_type == models.POLICY_DELETED:
            self._policies.pop(data["policy_id"], None)
        elif event_type == models.POLICIES_PAUSED:
            self._paused_until, self._pause_reason = float(data["until"]), data.get("reason")
        elif event_type == models.POLICY_DECISION_RECORDED:
            self._decisions.append(data)
            del self._decisions[:-self._keep]

    def _append(self, event_type: str, event) -> Dict[str, Any]:
        data = event.model_dump(mode="json")
        self._events.append_to_stream(stream_name(self._ve_id), event_type, data)
        with self._lock:
            self._apply_locked(event_type, data)
        return data

    def _stamp(self) -> Dict[str, Any]:
        return {
            "virtual_environment_id": self._ve_id,
            "created_at": datetime.fromtimestamp(self._now_fn(), tz=timezone.utc),
        }

    # ── policies ─────────────────────────────────────────────────────────────
    def list(self) -> List[ScalingPolicy]:
        with self._lock:
            return sorted(self._policies.values(), key=lambda p: p.name)

    def get(self, policy_id: str) -> ScalingPolicy:
        with self._lock:
            policy = self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"policy {policy_id} not found")
        return policy

    def save(self, policy: ScalingPolicy, by: Optional[str]) -> ScalingPolicy:
        if not self._allowed:
            raise ConflictError("this VE's plan doesn't include scaling policies")
        errors = validate(policy)
        if errors:
            raise ConflictError("; ".join(errors))
        self._append(models.POLICY_SAVED, models.ScalingPolicySaved(
            policy=policy.to_dict(), user_id=by, **self._stamp(),
        ))
        return policy

    def delete(self, policy_id: str, by: Optional[str]) -> None:
        self.get(policy_id)
        self._append(models.POLICY_DELETED, models.ScalingPolicyDeleted(
            policy_id=policy_id, user_id=by, **self._stamp(),
        ))

    # ── pause ────────────────────────────────────────────────────────────────
    def pause(self, seconds: float, reason: str, by: Optional[str] = None) -> float:
        until = self._now_fn() + max(0.0, seconds)
        self._append(models.POLICIES_PAUSED, models.ScalingPoliciesPaused(
            until=until, reason=reason, user_id=by, **self._stamp(),
        ))
        return until

    def paused_until(self) -> float:
        with self._lock:
            return self._paused_until

    def is_paused(self) -> bool:
        return self._now_fn() < self.paused_until()

    def pause_reason(self) -> Optional[str]:
        with self._lock:
            return self._pause_reason if self._now_fn() < self._paused_until else None

    # ── decisions ────────────────────────────────────────────────────────────
    def record_decision(self, **fields: Any) -> Dict[str, Any]:
        return self._append(models.POLICY_DECISION_RECORDED, models.ScalingPolicyDecisionRecorded(
            **fields, **self._stamp(),
        ))

    def decisions(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._decisions[-limit:]))
