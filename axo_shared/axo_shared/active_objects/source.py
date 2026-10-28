"""Reads an Axo class from its source, without running it: what the browser's
live check shows, and what the platform keeps about a class registered
from source (the browser's, or the readable code the SDK sends).

The rules are the ``axo`` library's (``Axo.__init_subclass__``): the methods
people can call are ``run`` and every method marked ``@axo_method``
(``name=`` renames it), its own and those of base classes in the same code;
``upgrade(self, old_version)`` is the step that moves an object from an
older version; ``__init__``'s parameters fill the "New object" form.
Each method is a ``MethodInfo`` dict plus its ``params_schema`` (the same
fields functions get: type from the hint, default, description).
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

from axo_shared.active_objects.models import MethodInfo
from axo_shared.functions.signature import signature_of, source_code_hash

DEFAULT_METHOD = "run"
UPGRADE_METHOD = "upgrade"
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def describe_method(node: Any, name: Optional[str] = None) -> Dict[str, Any]:
    signature = signature_of(node)
    schema = signature.params_schema()
    info = MethodInfo(
        name=name or node.name, params=[p["name"] for p in schema], wants_context=signature.wants_context,
        doc=signature.description, optional=[p["name"] for p in schema if not p["required"]],
        defaults={p["name"]: p["default"] for p in schema if p.get("default") is not None},
    )
    return {**info.to_dict(), "params_schema": schema}


def _marked_name(node: Any) -> Optional[str]:
    """The callable name when the def is decorated with ``axo_method`` (any
    spelling: ``@axo_method``, ``@axo_method()``, ``@axo.axo_method(name=...)``)."""
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if called != "axo_method":
            continue
        if isinstance(deco, ast.Call):
            for kw in deco.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
        return node.name
    return None


def _base_names(node: ast.ClassDef) -> List[str]:
    return [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases]


def describe_class(tree: ast.Module, class_name: str) -> Optional[Dict[str, Any]]:
    """``methods`` / ``init`` / ``has_upgrade`` / ``description`` / ``is_axo``
    of a top-level class (None when the code has no such class)."""
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    node = classes.get(class_name)
    if node is None:
        return None
    # Its own body last, so a method it defines again wins (like the MRO).
    chain: List[ast.ClassDef] = []
    is_axo = False
    pending, seen = [node], set()
    while pending:
        current = pending.pop(0)
        if current.name in seen:
            continue
        seen.add(current.name)
        chain.append(current)
        for base in _base_names(current):
            if base == "Axo":
                is_axo = True
            elif base in classes:
                pending.append(classes[base])
    methods: Dict[str, Dict[str, Any]] = {}
    init: Optional[Dict[str, Any]] = None
    has_upgrade = False
    for klass in reversed(chain):
        for item in klass.body:
            if not isinstance(item, _DEFS):
                continue
            if item.name == "__init__":
                init = describe_method(item)
            elif item.name == UPGRADE_METHOD:
                has_upgrade = True
            marked = _marked_name(item)
            if marked is not None:
                methods[marked] = describe_method(item, marked)
            elif item.name == DEFAULT_METHOD:
                methods[DEFAULT_METHOD] = describe_method(item)
    doc = ast.get_docstring(node)
    return {"methods": sorted(methods.values(), key=lambda m: m["name"]), "init": init,
            "has_upgrade": has_upgrade, "is_axo": is_axo,
            "description": doc.strip().splitlines()[0].strip() if doc and doc.strip() else None}


def read_class(source: bytes, class_name: str) -> Optional[Dict[str, Any]]:
    """``describe_class`` from raw source (None when it doesn't parse or
    has no such class)."""
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None
    return describe_class(tree, class_name)


def check_class_source(source: bytes, class_name: str) -> Dict[str, Any]:
    """The browser's live check, never running the code: ``problems``
    ({line, column, message}; empty = it can be registered), the top-level
    ``classes`` (to pick from), its methods, ``init``, ``has_upgrade`` and
    ``code_hash``."""
    out: Dict[str, Any] = {"ok": False, "problems": [], "classes": [], "methods": [], "init": None,
                           "has_upgrade": False, "description": None, "code_hash": source_code_hash(source)}
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        out["problems"].append({"line": None, "column": None, "message": "the file is not valid UTF-8 text"})
        return out
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        out["problems"].append({"line": exc.lineno, "column": exc.offset, "message": f"syntax error: {exc.msg}"})
        return out
    out["classes"] = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    described = describe_class(tree, class_name)
    if described is None:
        out["problems"].append({"line": None, "column": None,
                                "message": f"there is no top-level class named '{class_name}'"})
        return out
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    out.update({k: described[k] for k in ("methods", "init", "has_upgrade", "description")})
    if not described["is_axo"]:
        out["problems"].append({"line": node.lineno, "column": None, "message": (
            f"{class_name} must be an Axo class: class {class_name}(Axo) with from axo import Axo")})
    if not described["methods"]:
        out["problems"].append({"line": node.lineno, "column": None, "message": (
            "it has no method people can call: add run(self, ...) or mark methods with @axo_method")})
    out["ok"] = not out["problems"]
    return out
