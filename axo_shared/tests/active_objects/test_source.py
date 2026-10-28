from axo_shared.active_objects.source import check_class_source, read_class

CART = b'''
from axo import Axo, axo_method
import axo


class Base(Axo):
    @axo_method
    def ping(self):
        return "pong"


class ShoppingCart(Base):
    """A cart that keeps its items.

    More text."""

    def __init__(self, owner: str, currency: str = "MXN", limit=None):
        self.owner = owner
        self.items = []

    @axo_method
    def add_item(self, sku: str, qty: int = 1, ctx=None):
        """Adds one product.

        Args:
            sku: the product code
        """
        self.items.append({"sku": sku, "qty": qty})

    @axo_method(name="empty")
    def clear(self):
        self.items = []

    @axo.axo_method()
    def total(self, prices: dict) -> float:
        return 0.0

    def run(self, note: str = "hi"):
        return note

    def _helper(self):
        pass

    def upgrade(self, old_version):
        self.currency = "MXN"
'''


def test_reads_methods_init_and_upgrade():
    out = check_class_source(CART, "ShoppingCart")
    assert out["ok"], out["problems"]
    assert out["classes"] == ["Base", "ShoppingCart"]
    names = [m["name"] for m in out["methods"]]
    assert names == ["add_item", "empty", "ping", "run", "total"]       # the base's too; helpers aren't callable
    add = next(m for m in out["methods"] if m["name"] == "add_item")
    assert add["params"] == ["sku", "qty"] and add["optional"] == ["qty"] and add["defaults"] == {"qty": 1}
    assert add["wants_context"] is True and add["doc"] == "Adds one product."
    assert [p["type"] for p in add["params_schema"]] == ["string", "number"]
    assert add["params_schema"][0]["description"] == "the product code"
    assert out["init"]["params"] == ["owner", "currency", "limit"]
    assert out["init"]["defaults"] == {"currency": "MXN"} and out["init"]["optional"] == ["currency", "limit"]
    assert out["has_upgrade"] is True
    assert out["description"] == "A cart that keeps its items."
    assert out["code_hash"]


def test_problems_have_lines():
    assert check_class_source(b"class A(Axo):\n    def run(self):\n        return (\n", "A")["problems"][0]["line"]
    missing = check_class_source(b"class A(Axo):\n    def run(self): pass\n", "B")
    assert not missing["ok"] and "no top-level class named 'B'" in missing["problems"][0]["message"]
    plain = check_class_source(b"class A:\n    def run(self): pass\n", "A")
    assert not plain["ok"] and "must be an Axo class" in plain["problems"][0]["message"]
    nothing = check_class_source(b"from axo import Axo\nclass A(Axo):\n    x = 1\n", "A")
    assert not nothing["ok"] and "no method people can call" in nothing["problems"][0]["message"]


def test_read_class_without_init():
    out = read_class(b"from axo import Axo\nclass C(Axo):\n    def run(self, n: int):\n        return n\n", "C")
    assert out["init"] is None and [m["name"] for m in out["methods"]] == ["run"]
    assert read_class(b"not python (", "C") is None
