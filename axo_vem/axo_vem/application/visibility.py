"""The "who sees runs and results" setting of functions, choreographies and
objects, and the checks every job / run / result read goes through (see
domain/visibility.py for the rule)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pymongo.collection import Collection

from axo_vem.domain.errors import InvalidRequestError, NotOwnerError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher
from axo_vem.domain.visibility import KINDS, MODES, RUNNER, TEAMS, Shared, Viewer, Visibility, resource_of


class ResultNotVisibleError(NotOwnerError):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} belongs to someone else, and its setting doesn't share it with you",
                         error_name="RESULT_NOT_VISIBLE")


class VisibilityService:
    """Settings live in the VE's Mongo (``result_visibility``, ``_id`` =
    ``<kind>:<ref>``); a missing one = only who runs it. Changes are kept in
    the history (``ResultVisibilityChanged``)."""

    def __init__(self, collection: Collection, publisher: Optional[EventPublisher] = None,
                 creator_of: Optional[Callable[[str, str], Optional[str]]] = None,
                 virtual_environment_id: Optional[str] = None) -> None:
        self._col = collection
        self._publisher = publisher
        # (kind, ref) -> who created it (registered the first version / created it)
        self._creator_of = creator_of or (lambda kind, ref: None)
        self._ve = virtual_environment_id

    # ── the setting ──────────────────────────────────────────────────────────
    def get(self, kind: str, ref: str) -> Visibility:
        _check_kind(kind)
        doc = self._col.find_one({"_id": f"{kind}:{ref}"})
        return Visibility.from_dict(doc) if doc else Visibility(kind=kind, ref=ref)

    def describe(self, viewer: Viewer, kind: str, ref: str) -> Dict[str, Any]:
        creator = self._creator_of(kind, ref)
        return {**self.get(kind, ref).to_dict(), "creator": creator,
                "can_change": viewer.sees_all or (creator is not None and creator == viewer.user_id)}

    def set(self, viewer: Viewer, kind: str, ref: str, mode: str, teams: Optional[List[str]] = None,
            name: Optional[str] = None) -> Dict[str, Any]:
        """Only its creator or the VE's Admins / Owner change it; it applies
        at once (it isn't part of a version)."""
        _check_kind(kind)
        if mode not in MODES:
            raise InvalidRequestError(f"who sees runs and results is one of {', '.join(MODES)}",
                                      error_name="VEM_INVALID")
        teams = sorted(set(teams or [])) if mode == TEAMS else []
        if mode == TEAMS and not teams:
            raise InvalidRequestError("pick at least one team", error_name="VEM_INVALID")
        creator = self._creator_of(kind, ref)
        if not (viewer.sees_all or (creator is not None and creator == viewer.user_id)):
            raise NotOwnerError("only who created it, or the VE's Admins and Owner, change who sees its runs",
                                error_name="RESULT_VISIBILITY_FORBIDDEN")
        before = self.get(kind, ref)
        setting = Visibility(kind=kind, ref=ref, mode=mode, teams=teams, set_by=viewer.user_id,
                             set_at=datetime.now(timezone.utc).isoformat())
        self._col.replace_one({"_id": f"{kind}:{ref}"}, {"_id": f"{kind}:{ref}", **setting.to_dict()}, upsert=True)
        if self._publisher is not None and (before.mode, before.teams) != (mode, teams):
            event = models.ResultVisibilityChanged(
                kind=kind, ref=ref, name=name, mode=mode, teams=teams, previous_mode=before.mode,
                previous_teams=before.teams, user_id=viewer.user_id, virtual_environment_id=self._ve,
                **{f"{kind}_id": ref} if kind != "object" else {"object_name": ref})
            self._publisher.append_to_stream(f"visibility-{kind}-{ref}", models.RESULT_VISIBILITY_CHANGED,
                                             event.model_dump(mode="json"))
        return self.describe(viewer, kind, ref)

    def forget(self, kind: str, ref: str) -> None:
        self._col.delete_one({"_id": f"{kind}:{ref}"})

    # ── checks ───────────────────────────────────────────────────────────────
    def can_see_job(self, viewer: Viewer, job: Dict[str, Any]) -> bool:
        if viewer.sees_all or (job.get("acting_user_id") and job.get("acting_user_id") == viewer.user_id):
            return True
        kind, ref = resource_of(job)
        if self.get(kind, ref).shares_with(viewer):
            return True
        # a job of a run another choreography's run called: that one's setting too
        return any(self.get("choreography", c).shares_with(viewer)
                   for c in (job.get("choreography") or {}).get("chain") or [])

    def require_job(self, viewer: Viewer, job: Optional[Dict[str, Any]]) -> None:
        """A job nobody recorded (yet) isn't refused: there's nothing to say whose it is."""
        if job is not None and not self.can_see_job(viewer, job):
            raise ResultNotVisibleError(f"Job {job.get('job_id')}")

    def can_see_run(self, viewer: Viewer, choreography_id: str, acting_user_id: Optional[str],
                    chain: Optional[List[str]] = None) -> bool:
        """``chain``: a called run's choreographies above it -- who may see
        the calling run sees the runs it called."""
        if viewer.sees_all or (acting_user_id and acting_user_id == viewer.user_id):
            return True
        return any(self.get("choreography", c).shares_with(viewer) for c in [choreography_id, *(chain or [])])

    def require_run(self, viewer: Viewer, run: Dict[str, Any]) -> None:
        if not self.can_see_run(viewer, run.get("choreography_id") or "", run.get("acting_user_id"),
                                run.get("chain")):
            raise ResultNotVisibleError(f"Run {run.get('run_id')}")

    def shared(self, viewer: Viewer) -> Optional[Shared]:
        """For list queries: None = sees everything; else their own plus what
        the settings share with them."""
        if viewer.sees_all:
            return None
        found: Dict[str, set] = {kind: set() for kind in KINDS}
        for doc in self._col.find({"mode": {"$ne": RUNNER}}):
            setting = Visibility.from_dict(doc)
            if setting.shares_with(viewer):
                found[setting.kind].add(setting.ref)
        return Shared(user_id=viewer.user_id, function_ids=frozenset(found["function"]),
                      choreography_ids=frozenset(found["choreography"]), object_names=frozenset(found["object"]))


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise InvalidRequestError(f"settings are for {', '.join(KINDS)}", error_name="VEM_INVALID")


def creator_lookup(functions: Collection, active_objects: Collection,
                   choreographies: Any) -> Callable[[str, str], Optional[str]]:
    """Who created a function (registered its first version), a choreography
    or an object -- they may change who sees its runs."""

    def creator_of(kind: str, ref: str) -> Optional[str]:
        if kind == "function":
            doc = functions.find_one({"function_id": ref}, sort=[("version", 1)])
            return (doc or {}).get("owner_user_id")
        if kind == "choreography":
            found = choreographies.get(ref)
            return found.owner_user_id if found is not None else None
        doc = active_objects.find_one({"_id": ref})
        return (doc or {}).get("created_by")

    return creator_of
