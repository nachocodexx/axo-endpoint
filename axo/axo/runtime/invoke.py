"""Running functions and object calls the way the platform does.

The endpoint (inside a worker or runner container) loads the code with
``axo.core.capture.load`` and calls these. For objects it passes in the
saved state; what comes back (``ObjectOutcome``) is what it saves -- only
when the call succeeded, and only the values that really changed.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple

from axo.core.axo import UPGRADE_METHOD, Axo, bind, method_attr, methods_of, set_context, state_of
from axo.core.data import wrap_params
from axo.core.decorators import CONTEXT_PARAM, DEFAULT_METHOD, unwrap
from axo.errors import AxoMethodNotFoundError

Identity = Tuple[Optional[str], Optional[str]]


@dataclass
class ObjectOutcome:
    result: Any = None
    # Every public attribute in memory after the call (loaded or set);
    # values never fetched aren't here and stay as saved.
    values: Dict[str, Any] = field(default_factory=dict)
    deleted: Set[str] = field(default_factory=set)


def _call(fn: Callable[..., Any], params: Dict[str, Any], ctx: Any) -> Any:
    kwargs = wrap_params(params)
    if CONTEXT_PARAM in inspect.signature(fn).parameters:
        kwargs[CONTEXT_PARAM] = ctx
    return fn(**kwargs)


def call_function(fn: Any, params: Dict[str, Any], ctx: Any) -> Any:
    """``@axo_function`` style: parameters by name, ``ctx`` if it asks."""
    return _call(unwrap(fn), params, ctx)


def create_object(cls: type, args: Iterable[Any] = (), kwargs: Optional[Dict[str, Any]] = None,
                  ctx: Any = None, identity: Identity = (None, None)) -> ObjectOutcome:
    """Runs ``__init__`` with the platform's context already in place."""
    obj = cls.__new__(cls)
    bind(obj, {}, ctx=ctx, identity=identity)
    obj.__init__(*list(args), **dict(kwargs or {}))
    values, deleted = state_of(obj)
    return ObjectOutcome(values=values, deleted=deleted)


def _instance(cls: type, values: Dict[str, Any], lazy: Iterable[str], loader: Optional[Callable[[str], Any]],
              ctx: Any, identity: Identity) -> Axo:
    return bind(cls.__new__(cls), dict(values), lazy, loader, ctx, identity)


def call_object(cls: type, method: Optional[str], params: Dict[str, Any], ctx: Any,
                values: Dict[str, Any], lazy: Iterable[str] = (), loader: Optional[Callable[[str], Any]] = None,
                identity: Identity = (None, None)) -> ObjectOutcome:
    """One method call on saved state. ``lazy`` names saved values not sent
    along; ``loader(name)`` fetches one when the method reads it."""
    name = method or DEFAULT_METHOD
    attr = method_attr(cls, name)
    if attr is None:
        raise AxoMethodNotFoundError(f"{cls.__name__} has no callable method {name!r}",
                                     {"class": cls.__name__, "method": name,
                                      "methods": sorted(methods_of(cls))})
    obj = _instance(cls, values, lazy, loader, ctx, identity)
    result = _call(getattr(obj, attr), params, ctx)
    set_context(obj, None)
    after, deleted = state_of(obj)
    return ObjectOutcome(result=result, values=after, deleted=deleted)


def upgrade_object(cls: type, old_version: int, ctx: Any, values: Dict[str, Any], lazy: Iterable[str] = (),
                   loader: Optional[Callable[[str], Any]] = None,
                   identity: Identity = (None, None)) -> ObjectOutcome:
    """Moves saved state to a newer class version: its ``upgrade(self,
    old_version)`` runs if it has one (else the state is kept as is)."""
    obj = _instance(cls, values, lazy, loader, ctx, identity)
    hook = getattr(type(obj), UPGRADE_METHOD, None)
    if callable(hook):
        _call(getattr(obj, UPGRADE_METHOD), {"old_version": old_version}, ctx)
    after, deleted = state_of(obj)
    return ObjectOutcome(values=after, deleted=deleted)
