"""Classes registered from source (the browser's way): what's read from the
code, the live check, and deleting a version no object is on."""
import mongomock
import pytest

from axo_vem.application.objects.service import ObjectService
from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events import models
from axo_vem.infrastructure.database.mongo.object_repository import (
    MongoActiveObjectRepository, MongoObjectClassRepository,
)

CART = b'''from axo import Axo, axo_method


class Cart(Axo):
    """Items someone is buying."""

    def __init__(self, owner: str, currency: str = "MXN"):
        self.owner = owner
        self.items = []

    @axo_method
    def add_item(self, sku: str, qty: int = 1):
        self.items.append({"sku": sku, "qty": qty})

    def upgrade(self, old_version):
        pass
'''


class _Register:
    def __init__(self):
        self.versions = 0

    def execute(self, **kwargs):
        self.versions += 1
        return {"version": self.versions, "function_id": kwargs["function_id"]}


class _Deleter:
    def __init__(self):
        self.deleted = []

    def execute(self, *, function_id, version, current_user_id):
        self.deleted.append((function_id, version))
        return {"function_id": function_id, "version": version}


class _Log:
    def __init__(self):
        self.events = []

    def append_to_stream(self, stream, event_type, data):
        self.events.append((stream, event_type, data))


@pytest.fixture
def parts():
    db = mongomock.MongoClient()["t"]
    classes = MongoObjectClassRepository(db["object_classes"])
    objects = MongoActiveObjectRepository(db["active_objects"])
    deleter, log = _Deleter(), _Log()
    service = ObjectService("ve1", None, _Register(), classes, objects, log, delete_function=deleter)
    return service, classes, objects, deleter, log


def test_a_class_from_source_gets_its_methods_and_init(parts):
    service, classes, *_ = parts
    out = service.register_class(user_id="ana", class_name="Cart", code=CART)
    assert out["version"] == 1 and out["existing"] is False
    doc = classes.get("Cart", 1)
    assert [m["name"] for m in doc["methods"]] == ["add_item"]
    sku = doc["methods"][0]["params_schema"][0]
    assert (sku["name"], sku["type"], sku["required"]) == ("sku", "string", True)
    assert doc["init"]["params"] == ["owner", "currency"] and doc["init"]["defaults"] == {"currency": "MXN"}
    assert doc["has_upgrade"] is True and doc["description"] == "Items someone is buying."
    assert doc["created_by"] == "ana"


def test_the_sdks_methods_keep_their_names_and_get_forms(parts):
    service, classes, *_ = parts
    sdk = [{"name": "add_item", "params": ["sku", "qty"], "optional": ["qty"], "defaults": {"qty": 1}}]
    service.register_class(user_id="ana", class_name="Cart", code=CART, methods=sdk)
    method = classes.get("Cart", 1)["methods"][0]
    assert method["params"] == ["sku", "qty"] and [p["name"] for p in method["params_schema"]] == ["sku", "qty"]


def test_the_check_names_the_same_version(parts):
    service, *_ = parts
    first = service.check_class(CART.decode(), "Cart")
    assert first["ok"] and first["existing_version"] is None and first["classes"] == ["Cart"]
    service.register_class(user_id="ana", class_name="Cart", code=CART)
    assert service.check_class(CART.decode(), "Cart")["existing_version"] == 1
    assert not service.check_class("class Cart(:\n", "Cart")["ok"]


def test_a_version_in_use_is_kept(parts):
    service, classes, objects, deleter, _ = parts
    service.register_class(user_id="ana", class_name="Cart", code=CART)
    objects.reserve({"name": "cart-1", "object_id": "o1", "class_name": "Cart", "class_version": 1,
                     "status": "ready"})
    with pytest.raises(ConflictError) as err:
        service.delete_class_version(user_id="ana", class_name="Cart", version=1)
    assert err.value.error_name == "ACTIVE_OBJECT_CLASS_IN_USE" and "cart-1" in str(err.value)
    assert classes.get("Cart", 1) is not None and deleter.deleted == []


def test_an_unused_version_goes_with_its_code(parts):
    service, classes, objects, deleter, log = parts
    service.register_class(user_id="ana", class_name="Cart", code=CART)
    service.register_class(user_id="ana", class_name="Cart", code=CART + b"\n# v2\n")
    objects.reserve({"name": "cart-1", "object_id": "o1", "class_name": "Cart", "class_version": 2,
                     "status": "ready"})
    out = service.delete_class_version(user_id="ana", class_name="Cart", version=1)
    assert out == {"class_name": "Cart", "version": 1, "deleted": True}
    assert classes.get("Cart", 1) is None and classes.get("Cart", 2) is not None
    assert deleter.deleted == [(service.class_function_id("Cart"), 1)]
    stream, event_type, data = log.events[-1]
    assert event_type == models.ACTIVE_OBJECT_CLASS_VERSION_DELETED and stream == "object-classes-Cart"
    assert data["version"] == 1 and data["user_id"] == "ana"
