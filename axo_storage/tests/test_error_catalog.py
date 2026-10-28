"""Every error class axo_storage defines is in the shared error catalog."""
import importlib
import inspect
import pkgutil

from axo_shared import error_catalog as C
from axo_shared.errors import AxoError

import axo_storage


def test_every_storage_error_class_is_in_the_catalog():
    missing = []
    for info in pkgutil.walk_packages(axo_storage.__path__, "axo_storage."):
        if info.name.endswith("__main__"):
            continue
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, AxoError) and cls.__module__ == module.__name__:
                entry = C.BY_CODE.get(cls.code)
                if entry is None or entry.name != cls.name:
                    missing.append(f"{cls.__module__}.{cls.__name__} ({cls.code} {cls.name})")
    assert sorted(missing) == []
