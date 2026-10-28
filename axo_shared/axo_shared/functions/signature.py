"""Reads a function's parameters from its source, without running it: the
one rule behind the run form, used for every function -- registered from
the browser or from the SDK (its source, or the readable text it sends with
a pickled function).

- the name, and whether it's required (no default);
- its type, from the type hint: int / float -> number, str -> string,
  bool -> boolean, DataRef -> data_ref, anything else (or none) -> json;
  ``Optional[X]`` / ``X | None`` count as X;
- its default, when it's a plain value (a number, text, list, ...);
- its description, from the docstring's ``Args:`` section (Google style) or
  ``:param name:`` lines.

``ctx`` is the job's context, never a parameter. ``*args`` / ``**kwargs``
can't be shown as fields and are left out.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from axo_shared.functions.params_schema import ParamSpec
from axo_shared.functions.source import FunctionSourceError, _parse

CONTEXT_PARAM = "ctx"
_TYPES = {"int": "number", "float": "number", "str": "string", "bool": "boolean", "DataRef": "data_ref"}
_ARGS_HEADERS = ("args:", "arguments:", "parameters:", "params:")
_ARG_LINE = re.compile(r"^\s*\*{0,2}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
_PARAM_LINE = re.compile(r"^\s*:param\s+(?:[\w\[\], .|]+\s+)?(\w+)\s*:\s*(.*)$")


@dataclass(frozen=True)
class FunctionSignature:
    params: List[ParamSpec] = field(default_factory=list)
    wants_context: bool = False
    description: Optional[str] = None      # the docstring's first line
    # Written the old way, ``def f(params, ctx)`` (no longer supported).
    old_style: bool = False

    def params_schema(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.params]


def read_signature(source: bytes, function_name: str) -> FunctionSignature:
    """Raises FunctionSourceError when the source has no such function."""
    tree = _parse(source)
    node = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == function_name), None)
    if node is None:
        raise FunctionSourceError(f"uploaded source does not define a top-level function named '{function_name}'")
    return signature_of(node)


def signature_of(node: Any) -> FunctionSignature:
    """The parameters of one ``def`` (a function, or a class's method: ``self``
    / ``cls`` are left out)."""
    doc = ast.get_docstring(node)
    described = _describe_args(doc)
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: List[Optional[ast.expr]] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
    params: List[ParamSpec] = []
    wants_context = False
    for arg, default in pairs:
        if arg.arg == CONTEXT_PARAM:
            wants_context = True
            continue
        if arg.arg in ("self", "cls"):
            continue
        params.append(ParamSpec(name=arg.arg, type=_type_of(arg.annotation), required=default is None,
                                default=_plain_value(default), description=described.get(arg.arg)))
    first_line = doc.strip().splitlines()[0].strip() if doc and doc.strip() else None
    names = [a.arg for a in positional]
    old_style = names[:2] == ["params", CONTEXT_PARAM] and positional[0].annotation is None
    return FunctionSignature(params=params, wants_context=wants_context, description=first_line, old_style=old_style)


def _type_of(annotation: Optional[ast.expr]) -> str:
    if annotation is None:
        return "json"
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):   # "int" (a string hint)
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return "json"
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):   # X | None
        sides = [s for s in (annotation.left, annotation.right) if not _is_none(s)]
        return _type_of(sides[0]) if len(sides) == 1 else "json"
    if isinstance(annotation, ast.Subscript) and _name_of(annotation.value) == "Optional":
        return _type_of(annotation.slice)
    return _TYPES.get(_name_of(annotation) or "", "json")


def _name_of(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):          # axo.DataRef, typing.Optional
        return node.attr
    return None


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _plain_value(node: Optional[ast.expr]) -> Any:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
        json.dumps(value)
        return value
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None                              # computed / not JSON: the code's own default applies


def _describe_args(doc: Optional[str]) -> Dict[str, str]:
    if not doc:
        return {}
    found: Dict[str, str] = {}
    lines = doc.splitlines()
    for line in lines:
        hit = _PARAM_LINE.match(line)
        if hit and hit.group(2).strip():
            found[hit.group(1)] = hit.group(2).strip()
    inside, indent, current = False, None, None
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in _ARGS_HEADERS:
            inside, indent, current = True, None, None
            continue
        if not inside:
            continue
        if not stripped:
            current = None
            continue
        width = len(line) - len(line.lstrip())
        if indent is None:
            indent = width
        if width < indent or (stripped.endswith(":") and width <= indent and not _ARG_LINE.match(line)):
            inside = False                      # the next section (Returns:, Raises:, ...)
            continue
        hit = _ARG_LINE.match(line) if width == indent else None
        if hit:
            current = hit.group(1)
            if hit.group(2).strip():
                found.setdefault(current, hit.group(2).strip())
        elif current is not None:               # a description going on over more lines
            found[current] = (found.get(current, "") + " " + stripped).strip()
    return found


def source_code_hash(source: bytes) -> str:
    """The same hash the axo SDK gives source code (``Code.code_hash``): the
    same code again is the same version."""
    h = hashlib.sha256(b"source")
    h.update(b":")
    h.update(source)
    return h.hexdigest()


def check_source(source: bytes, function_name: str) -> Dict[str, Any]:
    """What the browser's live check shows, never running the code:
    ``problems`` ({line, column, message}; empty = it can be registered),
    the run form it would get (``params_schema``), ``description`` and the
    top-level functions it defines (to pick the name from)."""
    out: Dict[str, Any] = {"ok": False, "problems": [], "params_schema": [], "description": None,
                           "functions": [], "code_hash": source_code_hash(source)}
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        out["problems"].append({"line": None, "column": None, "message": "the file is not valid UTF-8 text"})
        return out
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        out["problems"].append({"line": exc.lineno, "column": exc.offset,
                                "message": f"syntax error: {exc.msg}"})
        return out
    out["functions"] = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if function_name not in out["functions"]:
        out["problems"].append({"line": None, "column": None,
                                "message": f"there is no top-level function named '{function_name}'"})
        return out
    signature = read_signature(source, function_name)
    if signature.old_style:
        node = next(n for n in tree.body if getattr(n, "name", None) == function_name)
        out["problems"].append({"line": node.lineno, "column": None, "message": (
            "f(params, ctx) is no longer supported: write it with named parameters, "
            f"for example def {function_name}(width: int, height: int = 100, ctx=None)")})
        return out
    out.update(ok=True, params_schema=signature.params_schema(), description=signature.description)
    return out
