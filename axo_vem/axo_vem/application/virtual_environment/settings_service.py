from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.virtual_environment.settings import (
    SETTING_NAMES,
    PlanLimits,
    VeSettings,
    stream_name,
    validate_changes,
)


VE_LIMITS_CHANGED = "VirtualEnvironmentLimitsChanged"


class VeSettingsService:
    """This VE's settings. Every change is appended to the VEM's own event
    log (one stream) and the current values are rebuilt by replaying it on
    startup -- so settings survive restarts and every value has a who/when/
    why history. Thread-safe: reconciliation, policies and the API all read
    and change it concurrently."""

    def __init__(
        self,
        virtual_environment_id: str,
        limits: PlanLimits,
        event_publisher: EventPublisher,
        initial: Optional[Any] = None,   # VeSettings or a dict of chosen values
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._ve_id = virtual_environment_id
        self._limits = limits
        self._events = event_publisher
        self._settings = VeSettings()
        # Applied (and recorded) only if this VE has no settings history yet.
        if isinstance(initial, VeSettings):
            initial = initial.to_dict()
        self._initial: Dict[str, Any] = {k: v for k, v in (initial or {}).items() if k in SETTING_NAMES}
        self._now_fn = now_fn
        self._lock = threading.Lock()
        self._listeners: List[Callable[[VeSettings], None]] = []
        self._detail_listeners: List[Callable[[VeSettings, Dict[str, Any], str], None]] = []

    @property
    def virtual_environment_id(self) -> str:
        return self._ve_id

    @property
    def limits(self) -> PlanLimits:
        return self._limits

    def current(self) -> VeSettings:
        with self._lock:
            return self._settings

    def on_change(self, listener: Callable[[VeSettings], None]) -> None:
        """Called with the new settings after every successful change."""
        self._listeners.append(listener)

    def on_change_detail(self, listener: Callable[[VeSettings, Dict[str, Any], str], None]) -> None:
        """Called with (new settings, what changed, changed_by_type) -- e.g. so
        a manual target change can pause the scaling policies."""
        self._detail_listeners.append(listener)

    def load(self, reader) -> int:
        """Replays this VE's settings stream (a KurrentReader). A missing
        stream means a brand-new VE: defaults apply. Returns how many changes
        were replayed."""
        try:
            records = reader.get_stream(stream_name(self._ve_id))
        except Exception:
            records = []
        settings = VeSettings()
        replayed = 0
        limits = self._limits
        for record in records or []:
            if record.type == VE_LIMITS_CHANGED:
                limits = PlanLimits.from_dict(json.loads(record.data.decode("utf-8")).get("limits", {}))
                continue
            if record.type != models.VE_SETTINGS_CHANGED:
                continue
            changes = json.loads(record.data.decode("utf-8")).get("changes", {})
            settings = settings.with_changes({k: v for k, v in changes.items() if k in settings.to_dict()})
            replayed += 1
        with self._lock:
            self._settings = settings
            self._limits = limits
        if replayed == 0 and self._initial:
            # A brand-new VE: the values chosen when it was bought.
            self.change(self._initial, changed_by_type="system", reason="initial settings (chosen when the VE was bought)")
        return replayed

    def set_limits(self, limits: PlanLimits, by: Optional[str] = None) -> PlanLimits:
        """New plan / quota (an upgrade or downgrade), without a restart. A
        target above the new maximum comes down to it (the extra endpoints
        drain); recorded so a restarted VEM keeps the new limits."""
        with self._lock:
            self._limits = limits
            event = {"virtual_environment_id": self._ve_id, "limits": limits.to_dict(), "changed_by_id": by,
                     "created_at": self._now_fn().isoformat()}
            self._events.append_to_stream(stream_name(self._ve_id), VE_LIMITS_CHANGED, event)
            current = self._settings
        if current.target_endpoints > limits.max_endpoints:
            target = limits.max_endpoints
            self.change({"target_endpoints": target, "min_endpoints": min(current.min_endpoints, target)},
                        changed_by_type="system", changed_by_id=by, reason="plan limit lowered")
        return limits

    def change(
        self,
        changes: Dict[str, Any],
        changed_by_type: str = "user",
        changed_by_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> VeSettings:
        """Validates against the plan limits, records the event, then applies.
        Raises ConflictError listing every problem; a no-op change records
        nothing."""
        with self._lock:
            current = self._settings
            errors = validate_changes(current, changes, self._limits)
            if errors:
                raise ConflictError("; ".join(errors))
            effective = {k: v for k, v in changes.items() if getattr(current, k) != v}
            if not effective:
                return current
            event = models.VirtualEnvironmentSettingsChanged(
                virtual_environment_id=self._ve_id, changes=effective,
                changed_by_type=changed_by_type, changed_by_id=changed_by_id, reason=reason,
                user_id=changed_by_id if changed_by_type == "user" else None,
            )
            self._events.append_to_stream(
                stream_name(self._ve_id), models.VE_SETTINGS_CHANGED, event.model_dump(mode="json"),
            )
            self._settings = current.with_changes(effective)
            updated = self._settings
        for listener in self._listeners:
            listener(updated)
        for detail_listener in self._detail_listeners:
            detail_listener(updated, effective, changed_by_type)
        return updated

    def retention_cutoff(self) -> Optional[datetime]:
        """Records older than this are hidden. None = keep showing everything."""
        days = self.current().effective_retention_days(self._limits)
        return self._now_fn() - timedelta(days=days) if days is not None else None

    def deletion_cutoff(self) -> Optional[datetime]:
        """Records older than this are deleted for good: the retention plus
        the platform's grace days. None = no retention, nothing is deleted."""
        cutoff = self.retention_cutoff()
        return cutoff - timedelta(days=max(self._limits.history_grace_days, 0)) if cutoff is not None else None
