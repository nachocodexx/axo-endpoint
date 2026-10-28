"""``@axo_method`` and ``@axo_function``.

Both leave the code usable as plain Python: a decorated method or function
called directly just runs, here. Running it on the platform goes through an
``AxoClient`` (``client.call(fn, ...)``, ``client.create(...)``)."""

from __future__ import annotations

import functools
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_METHOD = "run"
CONTEXT_PARAM = "ctx"


@dataclass(frozen=True)
class MethodSpec:
    """Mirrors axo_shared.active_objects.MethodInfo (the platform's shape).
    ``optional`` = parameters with a default; ``defaults`` = those defaults
    that are JSON values (a choreography fills the rest from earlier steps)."""

    name: str
    params: List[str] = field(default_factory=list)
    wants_context: bool = False
    doc: Optional[str] = None
    optional: List[str] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "params": list(self.params), "wants_context": self.wants_context,
                "doc": self.doc, "optional": list(self.optional), "defaults": dict(self.defaults)}

    def params_schema(self) -> List[Dict[str, Any]]:
        """The platform's parameter list for a function: any JSON value, required
        unless it has a default (sent along when it's a JSON value)."""
        schema = []
        for p in self.params:
            entry: Dict[str, Any] = {"name": p, "type": "json", "required": p not in self.optional}
            if self.defaults.get(p) is not None:
                entry["default"] = self.defaults[p]
            schema.append(entry)
        return schema


def _json_value(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def describe(fn: Callable[..., Any], name: Optional[str] = None, skip_self: bool = True) -> MethodSpec:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if skip_self and params and params[0].name == "self":
        params = params[1:]
    named = [p for p in params
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name != CONTEXT_PARAM]
    with_default = [p for p in named if p.default is not inspect.Parameter.empty]
    doc = inspect.getdoc(fn)
    return MethodSpec(name=name or fn.__name__, params=[p.name for p in named],
                      wants_context=CONTEXT_PARAM in sig.parameters,
                      doc=doc.splitlines()[0] if doc else None,
                      optional=[p.name for p in with_default],
                      defaults={p.name: p.default for p in with_default if _json_value(p.default)})


def axo_method(fn: Optional[Callable[..., Any]] = None, *, name: Optional[str] = None):
    """Marks a method people can call on the platform. ``run`` is callable
    without the decorator (it's the default method). Arguments arrive by
    name; add ``ctx`` to the signature to receive the job's context."""

    def mark(f: Callable[..., Any]) -> Callable[..., Any]:
        f.__axo_method__ = {"name": name or f.__name__}  # type: ignore[attr-defined]
        return f

    return mark(fn) if fn is not None else mark


@dataclass(frozen=True)
class FunctionOptions:
    name: Optional[str] = None
    python_version: Optional[str] = None
    requirements: Sequence[str] = ()
    max_concurrency: int = 1
    # Seconds a warm runner waits for the next job; None = the VE's default,
    # -1 = always warm.
    idle_ttl: Optional[float] = None
    # Tries in all when the code fails (the first included); platform
    # failures are tried again on their own, free.
    max_attempts: Optional[int] = None
    retry_delay: Optional[float] = None
    retry_backoff: Optional[str] = None     # "fixed" | "exponential"
    # How long its results are kept by default: 1h 1d 7d 30d 90d 1y never.
    keep_result: Optional[str] = None

    def runtime_spec(self) -> Dict[str, Any]:
        """The endpoint's RuntimeSpec: own Python or packages = a container."""
        spec: Dict[str, Any] = {"type": "container" if (self.python_version or self.requirements) else "process",
                                "max_concurrency": int(self.max_concurrency)}
        if self.python_version:
            spec["python_version"] = self.python_version
        if self.requirements:
            spec["requirements"] = list(self.requirements)
        if self.idle_ttl is not None:
            spec["idle_ttl_seconds"] = float(self.idle_ttl)
        if self.max_attempts is not None:
            spec["max_attempts"] = int(self.max_attempts)
        if self.retry_delay is not None:
            spec["retry_delay_seconds"] = float(self.retry_delay)
        if self.retry_backoff is not None:
            spec["retry_backoff"] = self.retry_backoff
        if self.keep_result is not None:
            spec["keep_result"] = self.keep_result
        return spec


class AxoFunction:
    """A function that can run on the platform. Calling it runs it here."""

    def __init__(self, fn: Callable[..., Any], options: FunctionOptions) -> None:
        if not inspect.isfunction(fn):
            raise TypeError("@axo_function decorates a plain function")
        self.fn = fn
        self.options = options
        self.spec = describe(fn, options.name, skip_self=False)
        functools.update_wrapper(self, fn)

    @property
    def name(self) -> str:
        return self.options.name or self.fn.__name__

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.spec.wants_context and CONTEXT_PARAM not in kwargs:
            from axo.core.context import LocalContext
            kwargs[CONTEXT_PARAM] = LocalContext()
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<axo_function {self.name}>"


def axo_function(
    fn: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    python_version: Optional[str] = None,
    requirements: Sequence[str] = (),
    max_concurrency: int = 1,
    idle_ttl: Optional[float] = None,
    max_attempts: Optional[int] = None,
    retry_delay: Optional[float] = None,
    retry_backoff: Optional[str] = None,
    keep_result: Optional[str] = None,
):
    """Marks a function that can run on the platform. Its parameters are
    named arguments; ``ctx: Context`` is optional. A new version is made only
    when its code changes. ``max_attempts`` / ``retry_delay`` /
    ``retry_backoff``: how a failing run is tried again; ``keep_result``:
    how long its results are kept (each call can say otherwise)."""
    options = FunctionOptions(name=name, python_version=python_version, requirements=tuple(requirements),
                              max_concurrency=max_concurrency, idle_ttl=idle_ttl, max_attempts=max_attempts,
                              retry_delay=retry_delay, retry_backoff=retry_backoff, keep_result=keep_result)

    def wrap(f: Callable[..., Any]) -> AxoFunction:
        return AxoFunction(f, options)

    return wrap(fn) if fn is not None else wrap


def unwrap(fn: Any) -> Callable[..., Any]:
    return fn.fn if isinstance(fn, AxoFunction) else fn
