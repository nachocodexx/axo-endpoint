"""Every Control Plane error name is in the shared catalog (95xx) with a
description and a hint, and every error body carries its number."""
import pathlib
import re

from axo_shared import error_catalog as C

from axo_control_plane.domain import errors as E

ROOT = pathlib.Path(__file__).resolve().parents[1] / "axo_control_plane"


def _used_names():
    names = set()
    for path in ROOT.rglob("*.py"):
        text = path.read_text()
        names.update(re.findall(r'code="([A-Z_]+)"', text))
        names.update(re.findall(r'"error": "([A-Z_]+)"', text))
    for cls in vars(E).values():
        if isinstance(cls, type) and issubclass(cls, E.DomainError):
            names.add(cls.code)
    return names


def test_every_cp_error_name_is_in_the_catalog_as_a_cp_error():
    wrong = sorted(n for n in _used_names()
                   if n not in C.BY_NAME or C.BY_NAME[n].component != C.CONTROL_PLANE
                   or not 9500 <= C.BY_NAME[n].code < 9600)
    assert wrong == []


def test_error_bodies_carry_the_number():
    body = E.LimitReachedError("used all", code="REQUEST_LIMIT").to_dict()
    assert body["error"] == "REQUEST_LIMIT" and body["error_code"] == 9512
    assert E.NotFoundError("x").to_dict()["error_code"] == 9502
