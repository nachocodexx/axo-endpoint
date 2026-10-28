"""Choreography blueprints: create, edit (each edit = a new version), list,
versions, triggers, checks, delete / purge.

Saved straight to the read model (so the client can run what it just
saved) and appended to the choreography's event stream (the history; the
projector applies it again, idempotently). Roles are the Control Plane's
job: anyone it lets through may act here.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from axo_vem.domain.choreography.choreography import Choreography, stream_name
from axo_vem.domain.choreography.graph_ops import graph_problems, is_process, predecessors, sinks
from axo_vem.domain.choreography.inputs import DATA_PARAM, ParamInfo
from axo_vem.domain.choreography.designer import designer
from axo_vem.domain.choreography.merge import rule_for
from axo_vem.domain.choreography.repository import ChoreographyRepository
from axo_vem.domain.choreography.triggers import stamp, trigger_problems, trigger_warnings
from axo_vem.domain.choreography.run import MAX_CALL_DEPTH
from axo_vem.domain.choreography.run_repository import ChoreographyRunRepository
from axo_vem.domain.compute.repository import FunctionRepository
from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from axo_vem.domain.events import models
from axo_vem.domain.events.publisher import EventPublisher

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepCatalog:
    """What the VE has that steps name: functions (with their parameters),
    objects (with their class's methods) and choreographies (with their run
    inputs; attached by ChoreographyService). ``object_lookup(name)`` gives
    ``(object info, class version doc)`` or None."""

    def __init__(self, functions: FunctionRepository,
                 object_lookup: Optional[Callable[[str], Optional[tuple]]] = None,
                 default_version: Optional[Callable[[str], Optional[int]]] = None) -> None:
        self._functions = functions
        self._object_lookup = object_lookup
        self._default_version = default_version
        self._choreographies: Optional[ChoreographyRepository] = None

    def attach_choreographies(self, repository: ChoreographyRepository) -> None:
        self._choreographies = repository

    def choreography(self, choreography_id: Optional[str], version: Optional[int]) -> Optional[Choreography]:
        """A called choreography: that version, or the newest when None."""
        if self._choreographies is None or not choreography_id:
            return None
        latest = self._choreographies.get(choreography_id)
        if latest is None or version is None or version == latest.version:
            return latest
        return self._choreographies.get_version(choreography_id, version)

    def run_inputs(self, graph: models.ChoreographyGraph, depth: int = 1) -> ParamInfo:
        """What a run takes -- a call's parameters: its first steps'
        parameters not set on them (a run's input fills them by name), and
        ``data`` when it starts at a bucket (the files handed to the call
        become that bucket's files). Unknown (``names`` None) when a first
        step's parameters aren't known."""
        preds = predecessors(graph)
        names: List[str] = []
        required: set = set()
        defaults: Dict[str, Any] = {}
        known = True
        for node in graph.nodes:
            if preds[node.node_id]:
                continue
            if node.kind == "bucket":
                if DATA_PARAM not in names:
                    names.append(DATA_PARAM)
                continue
            info = self.param_info(node, depth)
            if info is None:
                continue
            if info.names is None:
                known = False
                continue
            for name in info.names:
                if name in node.params:
                    continue
                if name not in names:
                    names.append(name)
                if name in info.required:
                    required.add(name)
                elif name in info.defaults:
                    defaults.setdefault(name, info.defaults[name])
        defaults = {k: v for k, v in defaults.items() if k not in required}
        return ParamInfo(names=names if known else None, required=required, defaults=defaults)

    def function(self, function_id: str, version: Optional[int]):
        """A step without a version runs the function's default version
        (the newest unless someone picked another)."""
        if version is None and self._default_version is not None:
            version = self._default_version(function_id)
            if version is None:
                return None
        function = (self._functions.latest(function_id) if version is None
                    else self._functions.get(function_id, version))
        if function is None or function.deleted_at is not None:
            return None
        return function

    def object(self, name: str) -> Optional[tuple]:
        return self._object_lookup(name) if self._object_lookup is not None else None

    def param_info(self, node: models.ChoreographyNode, depth: int = 0) -> Optional[ParamInfo]:
        """None = what the step names doesn't exist (any more)."""
        if node.kind == "choreography":
            inner = self.choreography(node.choreography_id, node.choreography_version)
            if inner is None or depth >= MAX_CALL_DEPTH:
                return None if inner is None else ParamInfo()
            return self.run_inputs(inner.graph, depth + 1)
        if node.kind == "function":
            function = self.function(node.function_id, node.function_version)
            return ParamInfo.from_schema(function.params_schema) if function is not None else None
        found = self.object(node.object_name)
        if found is None:
            return None
        _info, cls = found
        method = node.method or "run"
        methods = {m.get("name"): m for m in (cls or {}).get("methods") or []}
        if methods and method not in methods:
            return None
        return ParamInfo.from_method(methods.get(method))


class ChoreographyService:
    def __init__(self, repository: ChoreographyRepository, runs: ChoreographyRunRepository,
                 catalog: StepCatalog, publisher: EventPublisher,
                 virtual_environment_id: Optional[str] = None) -> None:
        self._repository = repository
        self._runs = runs
        self._catalog = catalog
        self._publisher = publisher
        self._ve = virtual_environment_id
        catalog.attach_choreographies(repository)
        # Called after the watched buckets change (a trigger added / removed,
        # a choreography deleted): the server sends a heartbeat at once, so
        # the Control Plane routes that bucket's next file here without
        # waiting for the next beat.
        self.on_triggers_changed: Callable[[], None] = lambda: None

    # ── reading ──────────────────────────────────────────────────────────────
    def trigger_buckets(self) -> List[Dict[str, str]]:
        """Every bucket a trigger watches and who added it (what the heartbeat
        carries to the Control Plane)."""
        return self._repository.trigger_buckets()

    def get(self, ref: str) -> Choreography:
        """By name or id."""
        found = self._repository.get_by_name(ref) or (self._repository.get(ref) if _UUID.match(ref) else None)
        if found is None:
            raise NotFoundError(f"No choreography {ref}", error_name="CHOREOGRAPHY_NOT_FOUND")
        return found

    def get_version(self, ref: str, version: Optional[int]) -> Choreography:
        choreography = self.get(ref)
        if version is None or version == choreography.version:
            return choreography
        found = self._repository.get_version(choreography.choreography_id, version)
        if found is None:
            raise NotFoundError(f"{choreography.name} has no version {version}",
                                error_name="CHOREOGRAPHY_VERSION_NOT_FOUND")
        return found

    def list(self) -> List[Dict[str, Any]]:
        """Each choreography with its run figures (one count over the runs)."""
        figures = self._runs.overview(datetime.now(timezone.utc) - timedelta(hours=24))
        none = {"last_run": None, "runs_24h": 0, "errors_24h": 0, "active_runs": 0}
        return [{**self._summary(c), **figures.get(c.choreography_id, none)} for c in self._repository.list()]

    def detail(self, ref: str, version: Optional[int] = None) -> Dict[str, Any]:
        choreography = self.get_version(ref, version)
        latest = self.get(ref)
        return {**choreography.to_dict(), "latest_version": latest.version, "triggers": latest.triggers,
                "active_runs": sum(self._runs.count(latest.choreography_id, s) for s in ("queued", "running")),
                "warnings": self.warnings(choreography.graph), "steps": self.steps(choreography.graph),
                "uses": self.uses(choreography.graph), "used_by": self.used_by(latest.choreography_id),
                "ends": sinks(choreography.graph)}

    def versions(self, ref: str) -> List[Dict[str, Any]]:
        return self._repository.versions(self.get(ref).choreography_id)

    def _summary(self, c: Choreography) -> Dict[str, Any]:
        return {"choreography_id": c.choreography_id, "name": c.name, "description": c.description,
                "version": c.version, "steps": len(c.graph.nodes), "triggers": len(c.triggers),
                "owner_user_id": c.owner_user_id, "updated_at": c.updated_at, "updated_by": c.updated_by,
                "updated_by_name": c.updated_by_name,
                "uses": sorted({n.choreography_id for n in c.graph.nodes
                                if n.kind == "choreography" and n.choreography_id})}

    # ── calls between choreographies ─────────────────────────────────────────
    def uses(self, graph: models.ChoreographyGraph) -> List[Dict[str, Any]]:
        """The choreographies its steps call (what the Overview's "Uses" lists)."""
        out: List[Dict[str, Any]] = []
        for node in graph.nodes:
            if node.kind != "choreography" or not node.choreography_id:
                continue
            inner = self._catalog.choreography(node.choreography_id, None)
            out.append({"step": node.node_id, "choreography_id": node.choreography_id,
                        "name": inner.name if inner is not None else node.choreography_name,
                        "version": node.choreography_version, "latest_version": inner.version if inner else None,
                        "exists": inner is not None})
        return out

    def used_by(self, choreography_id: str) -> List[Dict[str, Any]]:
        """The choreographies whose newest version calls this one."""
        out: List[Dict[str, Any]] = []
        for other in self._repository.list():
            steps = [n for n in other.graph.nodes if n.kind == "choreography" and n.choreography_id == choreography_id]
            if steps and other.choreography_id != choreography_id:
                out.append({"choreography_id": other.choreography_id, "name": other.name, "version": other.version,
                            "steps": [n.node_id for n in steps],
                            "pinned": sorted({n.choreography_version for n in steps
                                              if n.choreography_version is not None})})
        return out

    def call_problems(self, graph: models.ChoreographyGraph, choreography_id: Optional[str] = None,
                      name: Optional[str] = None) -> List[Tuple[str, str]]:
        """(error name, message): calls that would go in a circle or more than
        MAX_CALL_DEPTH levels deep, following the version each call would
        run (its pinned one, else the newest)."""
        found: List[Tuple[str, str]] = []

        def walk(g: models.ChoreographyGraph, ids: List[str], names: List[str]) -> None:
            for node in g.nodes:
                if node.kind != "choreography":
                    continue
                inner = self._catalog.choreography(node.choreography_id, node.choreography_version)
                if inner is None:
                    continue                     # reported as a missing choreography
                if inner.choreography_id in ids:
                    circle = names[ids.index(inner.choreography_id):] + [inner.name]
                    found.append(("CHOREOGRAPHY_LOOP", f"{' → '.join(circle)}: these choreographies would call "
                                                       "each other in a circle"))
                    continue
                if len(ids) + 1 > MAX_CALL_DEPTH:
                    found.append(("CHOREOGRAPHY_TOO_DEEP", f"{' → '.join(names + [inner.name])}: calls go at most "
                                                           f"{MAX_CALL_DEPTH} levels deep"))
                    continue
                walk(inner.graph, ids + [inner.choreography_id], names + [inner.name])

        walk(graph, [choreography_id or ""], [name or "this choreography"])
        return list(dict.fromkeys(found))

    def ensure_deletable(self, choreography: Choreography) -> None:
        callers = self.used_by(choreography.choreography_id)
        if callers:
            raise ConflictError(
                f"{choreography.name} is called by {', '.join(c['name'] for c in callers)}: remove those steps "
                "first", error_name="CHOREOGRAPHY_IN_USE")

    # ── checks ───────────────────────────────────────────────────────────────
    def problems(self, graph: models.ChoreographyGraph, choreography_id: Optional[str] = None,
                 name: Optional[str] = None, settings: Optional[models.ChoreographySettings] = None) -> List[str]:
        """What makes it unusable: a bad graph, steps naming a function /
        object / method / choreography that doesn't exist, calls in a circle
        or too deep, an "Its answer" step that isn't a last step."""
        problems = graph_problems(graph)
        if problems:
            return problems
        problems += trigger_problems(graph)
        if settings is not None and settings.answer_step is not None and settings.answer_step not in sinks(graph):
            problems.append(f"its answer step {settings.answer_step} isn't one of its last steps")
        for node in graph.nodes:
            if node.kind != "choreography":
                continue
            if self._catalog.choreography(node.choreography_id, node.choreography_version) is None:
                which = f" v{node.choreography_version}" if node.choreography_version is not None else ""
                problems.append(f"step {node.node_id}: there's no choreography "
                                f"{node.choreography_name or node.choreography_id}{which}")
        problems += [message for _name, message in self.call_problems(graph, choreography_id, name)]
        for node in graph.nodes:
            if node.kind == "function" and self._catalog.function(node.function_id, node.function_version) is None:
                which = f" v{node.function_version}" if node.function_version is not None else ""
                problems.append(f"step {node.node_id}: there's no function {node.function_name or node.function_id}{which}")
            elif node.kind == "object":
                found = self._catalog.object(node.object_name)
                if found is None:
                    problems.append(f"step {node.node_id}: there's no object {node.object_name}")
                elif self._catalog.param_info(node) is None:
                    problems.append(f"step {node.node_id}: {node.object_name} has no method {node.method or 'run'}")
        return problems

    def warnings(self, graph: models.ChoreographyGraph) -> List[str]:
        """Allowed, but worth knowing before a run."""
        warnings: List[str] = []
        if graph_problems(graph):
            return warnings
        warnings += trigger_warnings(graph)
        preds = predecessors(graph)
        by_id = {n.node_id: n for n in graph.nodes}
        for node in graph.nodes:
            if not is_process(node):
                continue
            info = self._catalog.param_info(node)
            senders = [p for p in preds[node.node_id] if is_process(by_id[p])]
            if len(senders) > 1:
                changing = sorted({p for p, r in self._rules(node, info).items()
                                   if r["strategy"] in ("random", "first_finished", "last_finished")})
                if changing:
                    warnings.append(f"step {node.node_id}: {', '.join(changing)} can come from a different step "
                                    "in each run (random / first / last to finish)")
            if info is not None and not senders and not any(by_id[p].kind == "bucket" for p in preds[node.node_id]):
                missing = sorted(info.required - set(node.params))
                if missing:
                    warnings.append(f"step {node.node_id} starts the run and needs {', '.join(missing)}: set "
                                    "them on the step or the run must be started with them")
        for node in graph.nodes:
            if node.kind == "choreography":
                warnings += self._call_warnings(node, preds, by_id)
        objects: Dict[str, List[str]] = {}
        for node in graph.nodes:
            if node.kind == "object" and node.object_name:
                objects.setdefault(node.object_name, []).append(node.node_id)
        for name, steps in objects.items():
            if len(steps) > 1:
                warnings.append(f"steps {', '.join(steps)} all call {name}: its calls run one at a time, so the "
                                "order between parallel ones isn't fixed")
        return warnings

    def _call_warnings(self, node: models.ChoreographyNode, preds: Dict[str, List[str]],
                       by_id: Dict[str, models.ChoreographyNode]) -> List[str]:
        inner = self._catalog.choreography(node.choreography_id, node.choreography_version)
        if inner is None:
            return []
        out: List[str] = []
        where = f"step {node.node_id}"
        ends = sinks(inner.graph)
        if len(ends) > 1 and inner.settings.answer_step is None:
            out.append(f"{where}: {inner.name} has {len(ends)} last steps and no \"Its answer\" picked in its "
                       "settings, so the answer is every last step's answer, by step")
        info = self._catalog.param_info(node)
        if info is not None and info.names is not None:
            gone = sorted(k for k in node.params if k not in info.names)
            if gone:
                newest = "" if node.choreography_version is not None else " (newest version)"
                out.append(f"{where}: {inner.name}{newest} doesn't take {', '.join(gone)} any more")
            gets_files = any(by_id[p].kind == "bucket" for p in preds[node.node_id])
            if gets_files and DATA_PARAM not in info.names:
                out.append(f"{where} gets files, but {inner.name} doesn't start with a bucket (or take data): "
                           "the files aren't passed on")
        return out

    def check(self, graph: models.ChoreographyGraph, choreography_id: Optional[str] = None,
              settings: Optional[models.ChoreographySettings] = None) -> Dict[str, Any]:
        current = self._repository.get(choreography_id) if choreography_id else None
        problems = self.problems(graph, choreography_id, current.name if current else None, settings)
        return {"ok": not problems, "problems": problems, "warnings": self.warnings(graph),
                "steps": {} if problems else self.steps(graph)}

    @staticmethod
    def _rules(node: models.ChoreographyNode, info: Optional[ParamInfo]) -> Dict[str, Dict[str, Any]]:
        """Each known parameter's merge rule (``"*"`` alone when the
        parameters aren't known); parameters set on the step are left out."""
        names = (info.names if info is not None and info.names is not None else [models.MERGE_ALL])
        return {name: rule_for(node, name).model_dump() for name in names if name not in node.params}

    def steps(self, graph: models.ChoreographyGraph) -> Dict[str, Dict[str, Any]]:
        """For the node dialog: every process step -> the steps sending to
        it (connection order) and its parameters: required or not, the
        code's default, the value set on the step and -- when more than one
        step sends to it -- how each is picked. ``params`` None = not known
        (a function without a schema: every key of earlier outputs is passed)."""
        preds = predecessors(graph)
        by_id = {n.node_id: n for n in graph.nodes}
        out: Dict[str, Dict[str, Any]] = {}
        for node in graph.nodes:
            if not is_process(node):
                continue
            senders = [p for p in preds[node.node_id] if is_process(by_id[p])]
            info = self._catalog.param_info(node)
            fan_in = len(senders) > 1
            params = None
            if info is not None and info.names is not None:
                params = []
                for name in info.names:
                    row: Dict[str, Any] = {"name": name, "required": name in info.required}
                    if name in info.defaults:
                        row["default"] = info.defaults[name]
                    if name in node.params:
                        row["value"] = node.params[name]
                    elif fan_in:
                        row["merge"] = rule_for(node, name).model_dump()
                    params.append(row)
            out[node.node_id] = {
                "senders": senders, "files_from": next((p for p in preds[node.node_id]
                                                        if by_id[p].kind == "bucket"), None),
                "exists": info is not None, "params": params,
                "merge_all": rule_for(node, models.MERGE_ALL).model_dump() if fan_in else None,
            }
        return out

    @staticmethod
    def designer() -> Dict[str, Any]:
        """Every setting of a step, an arrow and the choreography, the merge
        strategies and the condition comparisons (the designer's dialogs)."""
        return designer()

    def _require_valid(self, graph: models.ChoreographyGraph, choreography_id: Optional[str] = None,
                       name: Optional[str] = None, settings: Optional[models.ChoreographySettings] = None) -> None:
        problems = self.problems(graph, choreography_id, name, settings)
        if problems:
            calls = self.call_problems(graph, choreography_id, name)
            raise InvalidRequestError("; ".join(problems),
                                      error_name=calls[0][0] if calls else "CHOREOGRAPHY_INVALID")

    # ── writing ──────────────────────────────────────────────────────────────
    def create(self, *, user_id: str, name: str, graph: models.ChoreographyGraph,
               settings: Optional[models.ChoreographySettings] = None,
               description: Optional[str] = None, user_name: Optional[str] = None) -> Dict[str, Any]:
        _check_name(name)
        graph = stamp(graph, None, user_id, _now())
        self._require_valid(graph, None, name, settings)
        choreography_id = str(uuid.uuid4())
        if not self._repository.reserve_name(name, choreography_id):
            raise ConflictError(f"A choreography called {name} already exists", error_name="CHOREOGRAPHY_EXISTS")
        try:
            event = models.ChoreographyCreated(
                choreography_id=choreography_id, name=name, description=description, owner_user_id=user_id,
                user_id=user_id, virtual_environment_id=self._ve, version=1, graph=graph,
                settings=settings or models.ChoreographySettings(),
            )
            now = event.created_at.isoformat()
            choreography = Choreography(
                choreography_id=choreography_id, name=name, owner_user_id=user_id, version=1, graph=graph,
                settings=event.settings, description=description, created_at=now, updated_at=now,
                updated_by=user_id, updated_by_name=user_name,
            )
            self._repository.save(choreography)
        except Exception:
            self._repository.release_name(name, choreography_id)
            raise
        self._publisher.append_to_stream(stream_name(choreography_id), models.CHOREOGRAPHY_CREATED,
                                         event.model_dump(mode="json"))
        if graph.triggers:
            self._triggers_changed()
        return {**choreography.to_dict(), "existing": False}

    def update(self, *, user_id: str, ref: str, graph: models.ChoreographyGraph,
               name: Optional[str] = None, settings: Optional[models.ChoreographySettings] = None,
               description: Optional[str] = None, _stamped: bool = False,
               user_name: Optional[str] = None) -> Dict[str, Any]:
        """A new version -- unless nothing changed (``existing: true``).
        Runs already going keep the version they started with. A trigger
        the newest version had keeps who added it; a new one is ``user_id``'s."""
        current = self.get(ref)
        if not _stamped:
            graph = stamp(graph, current.graph, user_id, _now())
        name = name or current.name
        settings = settings or current.settings
        description = current.description if description is None else description
        if (graph == current.graph and name == current.name and settings == current.settings
                and description == current.description):
            return {**current.to_dict(), "existing": True}
        triggers_changed = graph.triggers != current.graph.triggers
        _check_name(name)
        self._require_valid(graph, current.choreography_id, name, settings)
        renamed = name != current.name
        if renamed and not self._repository.reserve_name(name, current.choreography_id):
            raise ConflictError(f"A choreography called {name} already exists", error_name="CHOREOGRAPHY_EXISTS")
        version = current.version + 1
        event = models.ChoreographyUpdated(
            choreography_id=current.choreography_id, name=name, description=description, version=version,
            graph=graph, settings=settings, user_id=user_id, virtual_environment_id=self._ve,
        )
        updated = Choreography(
            choreography_id=current.choreography_id, name=name, owner_user_id=current.owner_user_id,
            version=version, graph=graph, settings=settings, description=description,
            created_at=current.created_at, updated_at=event.created_at.isoformat(), updated_by=user_id,
            updated_by_name=user_name, legacy_triggers=[] if graph.triggers else current.legacy_triggers,
        )
        self._repository.save(updated)
        if renamed:
            self._repository.release_name(current.name, current.choreography_id)
        self._publisher.append_to_stream(stream_name(current.choreography_id), models.CHOREOGRAPHY_UPDATED,
                                         event.model_dump(mode="json"))
        if triggers_changed:
            self._triggers_changed()
        return {**updated.to_dict(), "existing": False}

    def save(self, *, user_id: str, name: str, graph: models.ChoreographyGraph,
             settings: Optional[models.ChoreographySettings] = None,
             description: Optional[str] = None, user_name: Optional[str] = None) -> Dict[str, Any]:
        """Create, or a new version of the one with this name (the client's
        ``save``), with its warnings and its steps' parameters to show."""
        if self._repository.get_by_name(name) is None:
            saved = self.create(user_id=user_id, name=name, graph=graph, settings=settings, description=description,
                                user_name=user_name)
        else:
            saved = self.update(user_id=user_id, ref=name, graph=graph, settings=settings, description=description,
                                user_name=user_name)
        return {**saved, "warnings": self.warnings(graph), "steps": self.steps(graph)}

    def delete(self, *, user_id: str, ref: str) -> Dict[str, Any]:
        """Waiting runs are cancelled by the caller (the engine) first.
        Refused while another choreography's newest version calls it."""
        choreography = self.get(ref)
        self.ensure_deletable(choreography)
        event = models.ChoreographyDeleted(choreography_id=choreography.choreography_id, user_id=user_id,
                                           virtual_environment_id=self._ve)
        self._repository.soft_delete(choreography.choreography_id, event.created_at.isoformat())
        self._repository.release_name(choreography.name, choreography.choreography_id)
        self._publisher.append_to_stream(stream_name(choreography.choreography_id), models.CHOREOGRAPHY_DELETED,
                                         event.model_dump(mode="json"))
        if choreography.triggers:
            self._triggers_changed()
        return {"choreography_id": choreography.choreography_id, "name": choreography.name, "deleted": True}

    # ── triggers (saved with each version) ───────────────────────────────────
    def add_trigger(self, *, user_id: str, ref: str, trigger: models.ChoreographyTrigger) -> Dict[str, Any]:
        """A new version with one more trigger (the Triggers tab's quick add)."""
        current = self.get(ref)
        graph = current.graph.model_copy(update={"triggers": [*current.graph.triggers,
                                                              trigger.model_copy(update={"trigger_id": ""})]})
        saved = self.update(user_id=user_id, ref=current.choreography_id, graph=graph)
        return {**saved["graph"]["triggers"][-1], "version": saved["version"]}

    def remove_trigger(self, *, user_id: str, ref: str, trigger_id: str) -> Dict[str, Any]:
        """A new version without that trigger."""
        current = self.get(ref)
        kept = [t for t in current.graph.triggers if t.trigger_id != trigger_id]
        if len(kept) == len(current.graph.triggers):
            raise NotFoundError(f"{current.name} has no trigger {trigger_id}",
                                error_name="CHOREOGRAPHY_TRIGGER_NOT_FOUND")
        saved = self.update(user_id=user_id, ref=current.choreography_id,
                            graph=current.graph.model_copy(update={"triggers": kept}))
        return {"trigger_id": trigger_id, "removed": True, "version": saved["version"]}

    def migrate_legacy_triggers(self) -> int:
        """Bucket triggers added before triggers were saved with each
        version become part of a new version (who added each one is kept;
        a version they were pinned to is dropped: the newest version runs).
        Safe to run on every start."""
        moved = 0
        for choreography in self._repository.list():
            if not choreography.legacy_triggers or choreography.graph.triggers:
                continue
            triggers = []
            for old in choreography.legacy_triggers:
                if not old.get("bucket"):
                    continue
                starts = [n.node_id for n in choreography.graph.nodes if n.kind == "bucket"
                          and n.bucket_name == old["bucket"]
                          and all(e.target_node_id != n.node_id for e in choreography.graph.edges)]
                triggers.append(models.ChoreographyTrigger(
                    trigger_id=old.get("trigger_id") or "", event_type=models.FILE_WRITTEN, bucket=old["bucket"],
                    prefix=old.get("prefix") or None, targets=starts, created_by=old.get("created_by"),
                    created_at=old.get("created_at")))
            graph = choreography.graph.model_copy(update={"triggers": triggers})
            if trigger_problems(graph):
                continue
            self.update(user_id="system", ref=choreography.choreography_id, graph=graph, _stamped=True,
                        description=choreography.description)
            moved += 1
        return moved

    def _triggers_changed(self) -> None:
        try:
            self.on_triggers_changed()
        except Exception:
            pass          # the regular heartbeat carries it a few seconds later anyway


def _check_name(name: str) -> None:
    if not NAME_PATTERN.match(name or ""):
        raise InvalidRequestError(
            f"{name!r} isn't a valid name (1-63 letters, digits, '.', '_' or '-', starting with a letter or digit)",
            error_name="CHOREOGRAPHY_INVALID")
