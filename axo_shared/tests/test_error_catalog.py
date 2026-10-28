import importlib
import inspect
import pkgutil

from axo_shared import error_catalog as C
from axo_shared.errors import AxoError


def error_classes(package: str):
    """Every AxoError subclass defined in ``package`` (one import per module)."""
    root = importlib.import_module(package)
    found = set()
    for info in pkgutil.walk_packages(root.__path__, package + "."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, AxoError) and cls.__module__ == module.__name__:
                found.add(cls)
    return found


def missing_from_catalog(classes):
    """Classes whose (code, name) isn't exactly an entry of the catalog."""
    out = []
    for cls in classes:
        entry = C.BY_CODE.get(cls.code)
        if entry is None or entry.name != cls.name:
            out.append(f"{cls.__module__}.{cls.__name__} ({cls.code} {cls.name})")
    return sorted(out)


def test_the_catalog_has_no_gaps_or_clashes():
    assert C.problems() == []


def test_every_shared_error_class_is_in_the_catalog():
    assert missing_from_catalog(error_classes("axo_shared")) == []


def test_catalog_json_is_sorted_and_complete():
    data = C.as_json()
    codes = [e["code"] for e in data["errors"]]
    assert codes == sorted(codes) and len(codes) == len(C.CATALOG)
    assert set(data["components"]) == set(C.COMPONENTS)
