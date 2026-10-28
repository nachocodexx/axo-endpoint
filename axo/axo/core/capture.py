"""Turns an Axo class or an ``@axo_function`` into code the platform can
store and run, and back.

The code is sent as source when possible -- the class or function itself
plus its module's ``import`` lines -- so it can be read on the platform and
a new version is only made when it really changes. When the source isn't
available, or it uses names from its module that the imports don't bring
(helper functions, constants), the object is pickled with cloudpickle
instead, which carries those along.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import inspect
import sys
import textwrap
import types
from dataclasses import dataclass
from typing import Any, Literal, Optional, Set

import cloudpickle
from option import Err, Ok, Result

from axo.core.axo import Axo
from axo.core.decorators import AxoFunction
from axo.errors import AxoCaptureError

CodeFormat = Literal["source", "pickle"]


@dataclass(frozen=True)
class Code:
    name: str
    kind: Literal["class", "function"]
    format: CodeFormat
    data: bytes
    # A pickle's readable text (shown on the platform's Code tab, never run).
    display: Optional[str] = None

    @property
    def code_hash(self) -> str:
        h = hashlib.sha256(self.format.encode())
        h.update(b":")
        h.update(self.data)
        return h.hexdigest()

    @property
    def text(self) -> Optional[str]:
        return self.data.decode("utf-8") if self.format == "source" else None


def capture(obj: Any, force_pickle: bool = False) -> Result[Code, AxoCaptureError]:
    if isinstance(obj, type) and issubclass(obj, Axo) and obj is not Axo:
        kind = "class"
    elif isinstance(obj, AxoFunction) or inspect.isfunction(obj):
        kind = "function"
    else:
        return Err(AxoCaptureError(f"{obj!r} is neither an Axo subclass nor a function"))
    target = obj.fn if isinstance(obj, AxoFunction) else obj
    name = obj.name if isinstance(obj, AxoFunction) else obj.__name__
    if name != target.__name__:
        # The platform finds the code by the name it defines.
        force_pickle = True
    source = None if force_pickle else _source_of(target)
    if source is not None:
        return Ok(Code(name=name, kind=kind, format="source", data=source.encode("utf-8")))
    try:
        data = _pickle_by_value(obj)
    except Exception as exc:  # cloudpickle can fail on many kinds of globals
        return Err(AxoCaptureError(f"Can't send {name}: no source and pickling failed: {exc}", {"name": name}))
    return Ok(Code(name=name, kind=kind, format="pickle", data=data, display=_display_of(target, name)))


def _display_of(target: Any, name: str) -> str:
    """What a pickled object looks like to a reader: its own source when
    Python has it, else its signature."""
    try:
        text = textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError):
        try:
            signature = str(inspect.signature(target))
        except (TypeError, ValueError):
            signature = "(...)"
        keyword = "class" if isinstance(target, type) else "def"
        text = f"{keyword} {target.__name__}{signature if keyword == 'def' else ''}:\n    ...\n"
    note = "# Sent as a pickle (cloudpickle): this text is for reading; the pickle is what runs.\n"
    if name != target.__name__:
        note += f"# Registered as {name!r}.\n"
    return note + text


def load(code: Code) -> Result[Any, AxoCaptureError]:
    """The class or function again (an ``AxoFunction`` stays wrapped)."""
    if code.format == "pickle":
        try:
            return Ok(cloudpickle.loads(code.data))
        except Exception as exc:
            return Err(AxoCaptureError(f"Can't load {code.name}: {exc}", {"name": code.name}))
    module_name = f"axo_code_{code.name}_{code.code_hash[:12]}"
    module = types.ModuleType(module_name)
    # Classes and dataclasses defined in it look their module up by name.
    sys.modules[module_name] = module
    try:
        exec(compile(code.data, f"<axo {code.name}>", "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        return Err(AxoCaptureError(f"Can't load {code.name}: {exc}", {"name": code.name}))
    found = module.__dict__.get(code.name)
    if found is None:
        return Err(AxoCaptureError(f"The code doesn't define {code.name}", {"name": code.name}))
    # Values of classes defined here are pickled with their class, so they
    # can be read again where this exact module was never loaded.
    cloudpickle.register_pickle_by_value(module)
    return Ok(found)


def _source_of(target: Any) -> Optional[str]:
    try:
        own = textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError):
        return None
    module = inspect.getmodule(target)
    try:
        module_source = inspect.getsource(module) if module is not None else ""
    except (OSError, TypeError):
        module_source = ""
    imports = _import_lines(module_source)
    source = ("\n".join(imports) + "\n\n\n" if imports else "") + own
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if target.__qualname__ != target.__name__:
        return None                               # defined inside a function or class
    missing = _free_names(tree) - _bound_names(tree) - set(dir(builtins))
    if module is not None and any(n in vars(module) for n in missing):
        return None                               # uses module globals the source wouldn't have
    return source


def _import_lines(module_source: str) -> list:
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return []
    return [ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def _free_names(tree: ast.AST) -> Set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _bound_names(tree: ast.AST) -> Set[str]:
    bound: Set[str] = {"__class__", "__name__", "__file__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _pickle_by_value(obj: Any) -> bytes:
    target = obj.fn if isinstance(obj, AxoFunction) else obj
    module = inspect.getmodule(target)
    by_value = module is not None and module.__name__ not in ("__main__",) and not module.__name__.startswith("axo.")
    if by_value:
        cloudpickle.register_pickle_by_value(module)
    try:
        return cloudpickle.dumps(obj)
    finally:
        if by_value:
            cloudpickle.unregister_pickle_by_value(module)
