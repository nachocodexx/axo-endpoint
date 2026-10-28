from axo_shared.functions.signature import read_signature


SOURCE = b'''
from typing import Optional
from axo import DataRef

def resize(image: DataRef, width: int, keep_ratio: bool = True, label: "str" = "x",
           scale: Optional[float] = None, tags: list = [], *, mode: str | None = "fast",
           ctx=None, **extra):
    """Resizes the latest image.

    Args:
        image: the picture to resize
        width (int): new width in pixels,
            counted before any border
        keep_ratio: keep the proportions

    Returns:
        the new size
    """
    return {"width": width}
'''


def test_the_form_is_read_from_the_signature_hints_defaults_and_docstring():
    sig = read_signature(SOURCE, "resize")
    assert sig.wants_context and sig.description == "Resizes the latest image."
    by = {p.name: p for p in sig.params}
    assert list(by) == ["image", "width", "keep_ratio", "label", "scale", "tags", "mode"]
    assert [by[n].type for n in by] == ["data_ref", "number", "boolean", "string", "number", "json", "string"]
    assert [n for n in by if by[n].required] == ["image", "width"]
    assert by["keep_ratio"].default is True and by["tags"].default == [] and by["mode"].default == "fast"
    assert by["width"].description == "new width in pixels, counted before any border"
    assert by["image"].description == "the picture to resize" and by["scale"].description is None


def test_param_lines_and_no_hints_mean_json():
    sig = read_signature(b'def f(a, b=len("x")):\n    """Does f.\n\n    :param a: the a\n    """\n', "f")
    assert [(p.name, p.type, p.required, p.default, p.description) for p in sig.params] == [
        ("a", "json", True, None, "the a"), ("b", "json", False, None, None)]
    assert not sig.wants_context


def test_the_old_params_ctx_style_is_recognised():
    assert read_signature(b"def f(params, ctx):\n    return 1\n", "f").old_style
    assert not read_signature(b"def f(params: dict, ctx=None):\n    return 1\n", "f").old_style
    assert not read_signature(b"def f(width: int, ctx=None):\n    return 1\n", "f").old_style


def test_the_live_check_points_at_the_line_and_shows_the_run_form():
    from axo_shared.functions.signature import check_source, source_code_hash
    bad = check_source(b"def add(a, b:\n    pass\n", "add")
    assert not bad["ok"] and bad["problems"][0]["line"] == 1
    missing = check_source(b"def sub(a: int):\n    return a\n", "add")
    assert missing["functions"] == ["sub"] and "add" in missing["problems"][0]["message"]
    old = check_source(b"def add(params, ctx):\n    return 1\n", "add")
    assert not old["ok"] and old["problems"][0]["line"] == 1
    good = check_source(b'def add(a: int, b: int = 2, ctx=None):\n    """Adds."""\n    return a + b\n', "add")
    assert good["ok"] and [p["name"] for p in good["params_schema"]] == ["a", "b"] and good["description"] == "Adds."
    assert good["code_hash"] == source_code_hash(b'def add(a: int, b: int = 2, ctx=None):\n    """Adds."""\n    return a + b\n')
