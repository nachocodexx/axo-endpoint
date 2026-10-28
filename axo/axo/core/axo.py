"""The ``Axo`` base class.

An object's state is its public instance attributes (``self.x``; names
starting with ``_`` are never saved). On the platform, each call starts from
the saved state: small values are there at once, big ones are fetched the
first time the method reads them. What the call leaves in ``self`` is saved
when it succeeds -- including changes made in place (``self.items.append``)
-- and thrown away when it fails.

``self.event`` (what started the call in a choreography), ``self.store``
(the connected buckets) and ``self.ctx`` are provided by the platform and
can't be assigned.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple

from axo.core.context import Store
from axo.core.decorators import DEFAULT_METHOD, MethodSpec, describe

# Every per-instance internal lives in __dict__ under this prefix.
_P = "_axo_"
UPGRADE_METHOD = "upgrade"


class Axo:
    __axo_methods__: Dict[str, MethodSpec] = {}
    # Callable name -> the Python attribute (@axo_method(name=...) may differ).
    __axo_method_attrs__: Dict[str, str] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        methods: Dict[str, MethodSpec] = {}
        attrs: Dict[str, str] = {}
        for klass in reversed(cls.__mro__):
            for attr_name, attr in vars(klass).items():
                if not callable(attr) or isinstance(attr, type):
                    continue
                marked = getattr(attr, "__axo_method__", None)
                if marked is not None:
                    spec = describe(attr, marked["name"])
                elif attr_name == DEFAULT_METHOD:
                    spec = describe(attr)
                else:
                    continue
                methods[spec.name] = spec
                attrs[spec.name] = attr_name
        cls.__axo_methods__ = methods
        cls.__axo_method_attrs__ = attrs

    def __new__(cls, *args: Any, **kwargs: Any) -> "Axo":
        obj = super().__new__(cls)
        d = obj.__dict__
        d[_P + "lazy"] = set()
        d[_P + "loader"] = None
        d[_P + "deleted"] = set()
        d[_P + "ctx"] = None
        d[_P + "identity"] = (None, None)
        return obj

    # ── attribute tracking ───────────────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        # Only called when normal lookup failed: a saved value not fetched yet.
        if name.startswith(_P):
            raise AttributeError(name)
        lazy: Optional[Set[str]] = self.__dict__.get(_P + "lazy")
        if lazy and name in lazy:
            value = self.__dict__[_P + "loader"](name)
            self.__dict__[name] = value
            lazy.discard(name)
            return value
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if not name.startswith("_"):
            self.__dict__[_P + "lazy"].discard(name)
            self.__dict__[_P + "deleted"].discard(name)

    def __delattr__(self, name: str) -> None:
        lazy: Set[str] = self.__dict__[_P + "lazy"]
        if name in lazy:
            lazy.discard(name)
        else:
            object.__delattr__(self, name)
        if not name.startswith("_"):
            self.__dict__[_P + "deleted"].add(name)

    # ── provided by the platform ─────────────────────────────────────────────
    @property
    def ctx(self) -> Any:
        return self.__dict__[_P + "ctx"]

    @property
    def event(self) -> Any:
        return getattr(self.ctx, "event", None)

    @property
    def store(self) -> Store:
        return Store(self.ctx)

    @property
    def object_id(self) -> Optional[str]:
        return self.__dict__[_P + "identity"][0]

    @property
    def object_name(self) -> Optional[str]:
        return self.__dict__[_P + "identity"][1]

    def __repr__(self) -> str:
        name = self.object_name
        return f"<{type(self).__name__}{' ' + name if name else ''}>"


# ── used by the runtime (inside the platform's runners) and by tests ─────────

def bind(obj: Axo, values: Dict[str, Any], lazy: Iterable[str] = (),
         loader: Optional[Callable[[str], Any]] = None, ctx: Any = None,
         identity: Tuple[Optional[str], Optional[str]] = (None, None)) -> Axo:
    """Puts saved state into an instance without counting it as changed."""
    d = obj.__dict__
    d.update(values)
    d[_P + "lazy"] = set(lazy) - set(values)
    d[_P + "loader"] = loader
    d[_P + "ctx"] = ctx
    d[_P + "identity"] = identity
    return obj


def set_context(obj: Axo, ctx: Any) -> None:
    obj.__dict__[_P + "ctx"] = ctx


def state_of(obj: Axo) -> Tuple[Dict[str, Any], Set[str]]:
    """(every public attribute in memory, names deleted). Values still not
    fetched are in neither: they stay as saved."""
    values = {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return values, set(obj.__dict__[_P + "deleted"])


def methods_of(cls: type) -> Dict[str, MethodSpec]:
    return dict(getattr(cls, "__axo_methods__", {}))


def method_attr(cls: type, name: str) -> Optional[str]:
    """The Python attribute behind a callable method name (None = not callable)."""
    return getattr(cls, "__axo_method_attrs__", {}).get(name)
