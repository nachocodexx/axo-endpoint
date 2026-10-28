"""``Choreography``: a reusable graph of steps, built in Python.

    from axo import AxoClient, Choreography

    ch = Choreography("daily-report")
    raw = ch.bucket("alice/raw")
    clean = ch.step(clean_rows)                      # an @axo_function (or a plain function)
    total = ch.step(client.object("totals"), method="add")
    out = ch.bucket("alice/reports")
    raw >> clean >> [total, out]
    ch.on_bucket("alice/raw", prefix="in/")          # every new file there starts a run
    ch.on_event("order.paid", when=When("total") > 100)   # and so does an event someone sends

    client.save(ch).unwrap()                          # registers the code; a new version if it changed
    run = client.run(ch, input={"day": "mon"}).unwrap()
    run.result().unwrap()                             # {step: output} of the last steps

A step's parameters come, in order, from ``params`` set on it, the bucket
file before it (``data``), the outputs of the steps before it (keys matched
by name), and finally the code's own default. When several steps before it
send the same key, ``merge`` says which value it gets -- by default the
first step in the order they were connected (``Merge.priority()``):

    ch.step(report, merge="random")                  # one rule for every parameter
    ch.step(report, merge={"*": Merge.priority(b, a), "rows": "sum",
                           "items": Merge.collect(as_list=True), "day": a})   # a step = from it

An arrow can have a condition on the earlier step's output; ``otherwise``
is followed when none of that step's conditions matched:

    count.when(When("rows") > 0) >> report
    count.when((When("rows") == 0) | When("error").exists()) >> alert
    count.otherwise() >> log

An "on failure" arrow is followed only when the step fails; the next step
gets the error (``ctx.event["failure"]``) and the failure counts as handled:

    train.on_failure() >> alert

Events: anything can start runs by sending an event of its own type; its
``data`` fields fill the first steps' parameters by name, and the whole
event is ``ctx.event["trigger"]["event"]``:

    client.emit("order.paid", {"order_id": "o-7", "total": 250}, id="pay-o-7")   # the same id = once

Triggers are saved with each version (only the newest version's start
runs). ``visibility`` -- who sees the runs and their results besides who
started them: ``"runner"`` (default), ``"ve"`` (everyone in the VE) or
``["team-id", ...]`` (those teams).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

_NODE_ID = re.compile(r"[^A-Za-z0-9_-]+")
# The rules that need nothing else, by name; the reducers stand for ``Merge.reduce(name)``.
_SIMPLE = ("priority", "random", "first_finished", "last_finished", "collect", "none")
_REDUCERS = ("sum", "min", "max", "concat", "all_equal")


@dataclass(eq=False)
class Step:
    """One node. ``a >> b`` connects a to b (and returns b); lists fan out / in."""

    choreography: "Choreography"
    node_id: str
    kind: str                                         # function | object | choreography | bucket
    target: Any = None                                # the function, the object's / choreography's name
                                                      # (or the Choreography itself), the bucket
    method: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)

    def __rshift__(self, other: Union["Step", Sequence["Step"]]) -> Union["Step", Sequence["Step"]]:
        for target in _steps(other):
            self.choreography.connect(self, target)
        return other

    def __rrshift__(self, other: Sequence[Union["Step", "Branch"]]) -> "Step":
        for source in other:
            if isinstance(source, Branch):
                source >> self
            else:
                self.choreography.connect(source, self)
        return self

    def when(self, condition: "Condition") -> "Branch":
        """``a.when(cond) >> b``: the arrow is followed only when a's output matches."""
        return Branch(self, condition=condition)

    def otherwise(self) -> "Branch":
        """``a.otherwise() >> b``: followed when none of a's conditions matched."""
        return Branch(self, otherwise=True)

    def on_failure(self) -> "Branch":
        """``a.on_failure() >> b``: followed only when a fails; b gets the error."""
        return Branch(self, on_failure=True)

    def __repr__(self) -> str:
        return f"<step {self.node_id} ({self.kind})>"


@dataclass(eq=False)
class Branch:
    """A step's arrow-to-be with a condition (or ``otherwise`` / ``on_failure``)."""

    source: Step
    condition: Optional["Condition"] = None
    otherwise: bool = False
    on_failure: bool = False

    def __rshift__(self, other: Union[Step, Sequence[Step]]) -> Union[Step, Sequence[Step]]:
        for target in _steps(other):
            self.source.choreography.connect(self.source, target, when=self.condition, otherwise=self.otherwise,
                                             on_failure=self.on_failure)
        return other


class Condition:
    """Rules on a step's output, made with ``When``; ``&`` = and, ``|`` = or."""

    def __init__(self, match: str, rules: List[Dict[str, Any]]) -> None:
        self.match, self.rules = match, rules

    def _join(self, other: "Condition", match: str) -> "Condition":
        rules: List[Dict[str, Any]] = []
        for part in (self, other):
            rules.extend(part.rules if part.match == match or len(part.rules) == 1 else [part.to_dict()])
        return Condition(match, rules)

    def __and__(self, other: "Condition") -> "Condition":
        return self._join(other, "all")

    def __or__(self, other: "Condition") -> "Condition":
        return self._join(other, "any")

    def to_dict(self) -> Dict[str, Any]:
        return {"match": self.match, "rules": self.rules}

    def __repr__(self) -> str:
        return f"<condition {self.to_dict()}>"


class When:
    """A field of the earlier step's output (``rows``, ``stats.count``,
    ``items.0``; empty = the whole output): ``When("rows") > 0``."""

    __hash__ = None  # type: ignore[assignment]

    def __init__(self, field: str = "") -> None:
        self.field = field

    def _rule(self, op: str, value: Any = None) -> Condition:
        return Condition("all", [{"field": self.field, "op": op, "value": value}])

    def __eq__(self, value: Any) -> Condition:  # type: ignore[override]
        return self._rule("==", value)

    def __ne__(self, value: Any) -> Condition:  # type: ignore[override]
        return self._rule("!=", value)

    def __gt__(self, value: Any) -> Condition:
        return self._rule(">", value)

    def __ge__(self, value: Any) -> Condition:
        return self._rule(">=", value)

    def __lt__(self, value: Any) -> Condition:
        return self._rule("<", value)

    def __le__(self, value: Any) -> Condition:
        return self._rule("<=", value)

    def is_in(self, values: Sequence[Any]) -> Condition:
        return self._rule("in", list(values))

    def not_in(self, values: Sequence[Any]) -> Condition:
        return self._rule("not_in", list(values))

    def contains(self, value: Any) -> Condition:
        return self._rule("contains", value)

    def not_contains(self, value: Any) -> Condition:
        return self._rule("not_contains", value)

    def exists(self) -> Condition:
        return self._rule("exists")

    def not_exists(self) -> Condition:
        return self._rule("not_exists")

    def is_true(self) -> Condition:
        return self._rule("is_true")

    def is_false(self) -> Condition:
        return self._rule("is_false")

    def is_empty(self) -> Condition:
        return self._rule("is_empty")

    def not_empty(self) -> Condition:
        return self._rule("not_empty")


def _steps(value: Union[Step, Iterable[Step]]) -> List[Step]:
    return [value] if isinstance(value, Step) else list(value)


def _node_ids(steps: Sequence[Union["Step", str]]) -> List[str]:
    return [s.node_id if isinstance(s, Step) else str(s) for s in steps]


@dataclass(frozen=True)
class Merge:
    """How a step picks a parameter several steps before it send."""

    strategy: str
    order: Optional[List[str]] = None
    source: Optional[str] = None
    reducer: Optional[str] = None
    as_list: bool = False

    @classmethod
    def priority(cls, *order: Union["Step", str]) -> "Merge":
        """The first in ``order`` that sent it (unnamed steps follow, in connection order)."""
        return cls("priority", order=_node_ids(order) or None)

    @classmethod
    def from_(cls, step: Union["Step", str]) -> "Merge":
        """Only from ``step``."""
        return cls("from", source=_node_ids([step])[0])

    @classmethod
    def random(cls) -> "Merge":
        """Any of them (the same one again on a retry or resume of the run)."""
        return cls("random")

    @classmethod
    def first_finished(cls) -> "Merge":
        return cls("first_finished")

    @classmethod
    def last_finished(cls) -> "Merge":
        return cls("last_finished")

    @classmethod
    def collect(cls, *order: Union["Step", str], as_list: bool = False) -> "Merge":
        """Every value: ``{step: value}``, or a list in ``order`` with ``as_list``."""
        return cls("collect", order=_node_ids(order) or None, as_list=as_list)

    @classmethod
    def reduce(cls, reducer: str, *order: Union["Step", str]) -> "Merge":
        """One value: ``sum`` / ``min`` / ``max`` / ``concat`` / ``all_equal``."""
        if reducer not in _REDUCERS:
            raise ValueError(f"unknown reducer {reducer!r}; use one of {', '.join(_REDUCERS)}")
        return cls("reduce", order=_node_ids(order) or None, reducer=reducer)

    @classmethod
    def none(cls) -> "Merge":
        """Not taken from the steps before (still in the event)."""
        return cls("none")

    @classmethod
    def of(cls, value: Union["Merge", str, Dict[str, Any]]) -> "Merge":
        if isinstance(value, Merge):
            return value
        if isinstance(value, str):
            if value in _SIMPLE:
                return cls(value)
            if value in _REDUCERS:
                return cls.reduce(value)
            raise ValueError(f"unknown merge rule {value!r}; use one of {', '.join(_SIMPLE + _REDUCERS)} "
                             "or Merge.from_(step)")
        if isinstance(value, Step):
            return cls.from_(value)
        if isinstance(value, dict) and set(value) == {"from"}:
            return cls.from_(value["from"])
        if isinstance(value, dict) and "strategy" in value:
            return cls(**value)
        raise ValueError(f"not a merge rule: {value!r}")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"strategy": self.strategy}
        for key in ("order", "source", "reducer"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.as_list:
            out["as_list"] = True
        return out


def _merge_rules(merge: Union[Merge, str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``merge=`` as sent: one rule for every parameter, or {param or "*": rule}."""
    if isinstance(merge, dict) and "strategy" not in merge and set(merge) != {"from"}:
        return {param: Merge.of(rule).to_dict() for param, rule in merge.items()}
    return {"*": Merge.of(merge).to_dict()}


@dataclass(frozen=True)
class Trigger:
    """``file.written`` (``bucket`` / ``prefix``) or an event type of your own."""

    event_type: str = "file.written"
    bucket: Optional[str] = None
    prefix: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    starts: Sequence[str] = ()             # step ids it's drawn connected to

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"event_type": self.event_type, "targets": list(self.starts)}
        for key in ("bucket", "prefix", "condition"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out


class Choreography:
    def __init__(self, name: str, description: Optional[str] = None, max_parallel_runs: Optional[int] = None,
                 queue_limit: Optional[int] = None, allow_completed_with_errors: Optional[bool] = None,
                 keep_results: Optional[str] = None,
                 visibility: Union[str, Sequence[str], None] = None,
                 answer_step: Optional[str] = None) -> None:
        """``allow_completed_with_errors`` (on by default): a failed file or
        branch doesn't stop the rest and the run ends ``completed_with_errors``;
        off, the first error fails the whole run. ``keep_results``: how long
        its steps' answers are kept (1h 1d 7d 30d 90d 1y never; else the VE's
        default) -- a run can be resumed while they are. ``answer_step``
        (or ``ch.answer(step)``): when another choreography calls this one,
        the last step whose answer is the call's answer."""
        self.name = name
        self.description = description
        self.settings = {k: v for k, v in (("max_parallel_runs", max_parallel_runs),
                                           ("queue_limit", queue_limit),
                                           ("allow_completed_with_errors", allow_completed_with_errors),
                                           ("keep_results", keep_results), ("answer_step", answer_step))
                         if v is not None}
        self.steps: List[Step] = []
        self.edges: List[Dict[str, Any]] = []   # {source, target, condition?, otherwise?}
        # None = leave the saved triggers as they are; a list = exactly these.
        self.triggers: Optional[List[Trigger]] = None
        # who sees runs and results: "runner" / "ve" / team ids (None = leave as it is)
        self.visibility = visibility

    # ── building ─────────────────────────────────────────────────────────────
    def _id(self, wanted: Optional[str], base: str) -> str:
        taken = {s.node_id for s in self.steps}
        if wanted:
            if wanted in taken:
                raise ValueError(f"there is already a step named {wanted!r}")
            return wanted
        base = (_NODE_ID.sub("-", base).strip("-") or "step")[:36]
        node_id, n = base, 2
        while node_id in taken:
            node_id, n = f"{base}-{n}", n + 1
        return node_id

    def _add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def answer(self, step: Union[Step, str]) -> "Choreography":
        """When another choreography calls this one: ``step`` (a last step)
        gives the call's answer."""
        self.settings["answer_step"] = step.node_id if isinstance(step, Step) else step
        return self

    def step(self, target: Any, method: Optional[str] = None, name: Optional[str] = None,
             params: Optional[Dict[str, Any]] = None, merge: Union[Merge, str, Dict[str, Any], None] = None,
             join: Optional[str] = None, max_attempts: Optional[int] = None, retry_backoff: Optional[str] = None,
             retry_delay_seconds: Optional[float] = None, timeout_seconds: Optional[float] = None,
             max_wait: Optional[float] = None, parallelism: Optional[int] = None,
             files_mode: Optional[str] = None, version: Optional[int] = None) -> Step:
        """A function step (an ``@axo_function``, a plain function or a
        ``FunctionRef``), or an object step (an ``ObjectHandle`` or
        ``object=`` name via ``ch.object``), or a call of another
        choreography (its name, or a ``Choreography``: saved first by
        ``client.save``) -- a run of it, linked to this run: its parameters
        are that choreography's inputs, its answer is that run's answer.
        ``version`` pins a function / choreography version; without it the
        newest one when the run starts. ``join="arrived"`` runs with
        whichever steps before it finished, instead of waiting for all.
        ``merge``: how a parameter several steps before it send is picked
        (a rule for all, or ``{param or "*": rule}``; see ``Merge``).
        After a bucket with several files: ``files_mode="per_file"`` (default)
        calls it once per file, ``"all_at_once"`` once with ``data`` = a list
        of every file. ``max_attempts`` / ``retry_backoff`` (fixed |
        exponential) / ``retry_delay_seconds``: how the step's job is tried
        again when its code fails (else the function's own settings)."""
        from axo.client.handles import ObjectHandle

        if files_mode not in (None, "per_file", "all_at_once"):
            raise ValueError(f"files_mode is per_file or all_at_once, not {files_mode!r}")
        options = {k: v for k, v in (
            ("params", params), ("merge", None if merge is None else _merge_rules(merge)), ("join", join),
            ("max_attempts", max_attempts), ("retry_backoff", retry_backoff),
            ("retry_delay_seconds", retry_delay_seconds),
            ("timeout_seconds", timeout_seconds), ("max_wait", max_wait), ("parallelism", parallelism),
            ("files_mode", files_mode),
            ("function_version", version)) if v is not None}
        if isinstance(target, (str, Choreography)):
            jobs_only = [k for k in ("max_attempts", "retry_backoff", "retry_delay_seconds") if k in options]
            if method is not None or jobs_only:
                raise ValueError(f"{', '.join(jobs_only) or 'method'}: not for a choreography step (its own "
                                 "steps have their tries)")
            if "function_version" in options:
                options["choreography_version"] = options.pop("function_version")
            label = target.name if isinstance(target, Choreography) else target
            return self._add(Step(self, self._id(name, label), "choreography", target, options=options))
        if isinstance(target, ObjectHandle):
            return self.object(target.name, method, name=name, **{k: v for k, v in options.items()
                                                                     if k != "function_version"})
        if method is not None:
            raise ValueError("method= is only for object steps")
        label = getattr(target, "name", None) or getattr(target, "__name__", None) or "step"
        return self._add(Step(self, self._id(name, label), "function", target, options=options))

    def object(self, object_name: str, method: Optional[str] = None, name: Optional[str] = None,
               **options: Any) -> Step:
        """A step calling ``method`` (default ``run``) of an existing object."""
        label = f"{object_name}-{method}" if method else object_name
        return self._add(Step(self, self._id(name, label), "object", object_name, method,
                              {k: v for k, v in options.items() if v is not None}))

    def bucket(self, bucket: str, name: Optional[str] = None, key_pattern: Optional[str] = None,
               files: Optional[Sequence[str]] = None) -> Step:
        """A bucket. After a step: its output is saved there (as JSON, under
        ``key_pattern``, default ``{choreography}/{run_id}/{source}.json``).
        Before a step: the step gets the file as ``data`` -- once per file
        when there are several (``files`` picks them; else the trigger's
        file, else every file)."""
        options: Dict[str, Any] = {}
        if key_pattern is not None:
            options["key_pattern"] = key_pattern
        if files is not None:
            options["selected_items"] = [{"name": f} for f in files]
        return self._add(Step(self, self._id(name, bucket.rsplit("/", 1)[-1]), "bucket", bucket,
                              options=options))

    def connect(self, source: Step, target: Step, when: Optional[Condition] = None,
                otherwise: bool = False, on_failure: bool = False) -> None:
        """An arrow; ``when`` / ``otherwise`` / ``on_failure`` as in the ``Step`` methods."""
        if source.choreography is not self or target.choreography is not self:
            raise ValueError("both steps must belong to this choreography")
        if when is not None and otherwise:
            raise ValueError("an arrow has a condition or is 'otherwise', not both")
        if on_failure and (when is not None or otherwise):
            raise ValueError("an 'on failure' arrow can't have a condition or be 'otherwise'")
        if on_failure and "bucket" in (source.kind, target.kind):
            raise ValueError("'on failure' arrows go from a function or object step to another one")
        if when is not None and not isinstance(when, Condition):
            raise ValueError("when= takes a condition made with When(...), e.g. When('rows') > 0")
        edge: Dict[str, Any] = {"source": source.node_id, "target": target.node_id}
        if when is not None:
            edge["condition"] = when.to_dict()
        if otherwise:
            edge["otherwise"] = True
        if on_failure:
            edge["on_failure"] = True
        self.edges = [e for e in self.edges if (e["source"], e["target"]) != (edge["source"], edge["target"])]
        self.edges.append(edge)

    def on_bucket(self, bucket: str, prefix: Optional[str] = None) -> "Choreography":
        """Every new file written to ``bucket`` (under ``prefix``) starts a
        run; the bucket step of ``bucket`` reads that file."""
        starts = [s.node_id for s in self.steps if s.kind == "bucket" and s.target == bucket
                  and all(e["target"] != s.node_id for e in self.edges)]
        self.triggers = (self.triggers or []) + [Trigger(bucket=bucket, prefix=prefix or None, starts=starts)]
        return self

    def on_event(self, event_type: str, when: Optional["Condition"] = None,
                 starts: Optional[Sequence["Step"]] = None) -> "Choreography":
        """Every event of ``event_type`` someone sends (``client.emit``)
        starts a run -- only those whose data matches ``when``, if given."""
        if event_type.startswith(("file.", "schedule.", "run.", "axo.")):
            raise ValueError(f"{event_type}: names starting with file. schedule. run. axo. are the platform's "
                             "(use on_bucket for new files)")
        self.triggers = (self.triggers or []) + [Trigger(
            event_type=event_type, condition=when.to_dict() if when is not None else None,
            starts=[s.node_id for s in starts or []])]
        return self

    # ── what is sent ─────────────────────────────────────────────────────────
    def graph(self, resolve, resolve_choreography=None) -> Dict[str, Any]:
        """The platform's graph; ``resolve(step)`` gives a function step's
        ``(function_id, version or None, name)``, ``resolve_choreography(step)``
        a choreography step's ``(choreography_id, name)``."""
        nodes = []
        for index, step in enumerate(self.steps):
            # no position: the designer arranges a choreography saved from Python
            node: Dict[str, Any] = {"node_id": step.node_id, "kind": step.kind}
            options = dict(step.options)
            if step.kind == "function":
                function_id, version, label = resolve(step)
                node.update(function_id=function_id, function_name=label)
                node["function_version"] = options.pop("function_version", version)
            elif step.kind == "object":
                node.update(object_name=step.target, method=step.method)
            elif step.kind == "choreography":
                if resolve_choreography is None:
                    raise ValueError(f"step {step.node_id} calls a choreography: save it with client.save")
                choreography_id, label = resolve_choreography(step)
                node.update(choreography_id=choreography_id, choreography_name=label)
            else:
                node["bucket_name"] = step.target
            node.update(options)
            nodes.append(node)
        edges = [{"edge_id": f"e{i}", "source_node_id": e["source"], "target_node_id": e["target"],
                  **{k: e[k] for k in ("condition", "otherwise", "on_failure") if k in e}}
                 for i, e in enumerate(self.edges)]
        graph: Dict[str, Any] = {"nodes": nodes, "edges": edges}
        if self.triggers is not None:
            graph["triggers"] = [t.to_dict() for t in self.triggers]
        return graph

    def __repr__(self) -> str:
        return f"<choreography {self.name}: {len(self.steps)} steps>"
