"""Loads registered code into the callable a worker or runner calls with
``(params, ctx)`` -- the one place that knows the two call styles
(``RuntimeSpec.call_style``):

- ``kwargs``: a function, parameters by name, ``ctx`` if its signature has
  it (plain source or an ``@axo_function``; the only function style).
- ``object``: an Axo class. The params carry one object operation under
  ``OBJECT_PARAM`` (create / call / upgrade, the saved state); the call runs
  through ``axo.runtime`` and returns the changed values already encoded
  (here, where the class is loaded), as JSON. The endpoint's
  ``ObjectCoordinator`` saves them.
"""

from __future__ import annotations

import base64
from typing import Any, Callable, Dict, Optional


from axo_shared.active_objects.models import AttributeEntry
from axo_shared.active_objects.state import codec

from axo_endpoint.core.dataio.ref import IORef

OBJECT_PARAM = "__axo_object__"
OBJECT_OUTCOME = "__axo_object_outcome__"
# dataio op a running object call uses to fetch a saved value not sent along.
STATE_GET = "state_get"
STATE_BUCKET = "axo-object-state"

Entry = Callable[[Dict[str, Any], Any], Any]


def load_entry(code: bytes, code_format: str, name: str, call_style: str = "kwargs") -> Entry:
    if call_style not in ("kwargs", "object"):
        raise ValueError(f"call style {call_style!r} is not supported: functions take named parameters")
    from axo.core.capture import Code, load

    loaded = load(Code(name=name, kind="class" if call_style == "object" else "function",
                       format="source" if code_format == "source" else "pickle", data=code))
    if loaded.is_err:
        raise loaded.unwrap_err()
    target = loaded.unwrap()
    if call_style == "kwargs":
        from axo.runtime import call_function
        return lambda params, ctx: call_function(target, params, ctx)
    return lambda params, ctx: run_object_operation(target, params, ctx)


def run_object_operation(cls: type, params: Dict[str, Any], ctx: Any,
                         fetch: Optional[Callable[[str], bytes]] = None) -> Dict[str, Any]:
    """One object operation inside the worker. Raises what the user's code
    raised (the coordinator then saves nothing)."""
    from axo.runtime import call_object, create_object, upgrade_object

    spec = params[OBJECT_PARAM]
    method_params = {k: v for k, v in params.items() if k != OBJECT_PARAM}
    identity = (spec.get("object_id"), spec.get("name"))
    lazy_entries = {n: AttributeEntry.from_dict(e) for n, e in (spec.get("lazy") or {}).items()}
    fetch = fetch or _fetch_over_channel

    def loader(attribute: str) -> Any:
        return _ok(codec.decode(lazy_entries[attribute], fetch(attribute)))

    op = spec["op"]
    if op == "create":
        outcome = create_object(cls, spec.get("args") or [], spec.get("kwargs") or {}, ctx, identity)
    elif op == "call":
        outcome = call_object(cls, spec.get("method"), method_params, ctx, spec.get("values") or {},
                              lazy_entries, loader, identity)
    elif op == "upgrade":
        outcome = upgrade_object(cls, int(spec.get("from_version") or 0), ctx, spec.get("values") or {},
                                 lazy_entries, loader, identity)
    else:
        raise ValueError(f"unknown object operation {op!r}")

    saved = spec.get("hashes") or {}
    hot_max = int(spec.get("hot_max_bytes", 4096))
    changed: Dict[str, Any] = {}
    for attribute, value in outcome.values.items():
        entry, data = _ok(codec.encode(value, hot_max))
        if saved.get(attribute) == entry.hash:
            continue                                # read but not changed
        changed[attribute] = {"entry": entry.to_dict(), "data": base64.b64encode(data).decode("ascii")}
    return {OBJECT_OUTCOME: {"result": outcome.result, "changed": changed,
                             "deleted": sorted(set(outcome.deleted) & set(saved))}}


def _ok(result: Any) -> Any:
    if result.is_err:
        raise result.unwrap_err()
    return result.unwrap()


def _fetch_over_channel(attribute: str) -> bytes:
    from axo_endpoint.dataio import _require_channel

    return _require_channel().request(STATE_GET, IORef(bucket=STATE_BUCKET, name=attribute), None)
