"""The dev VEM's active objects (see ui_server.FakeVemApi): Axo classes with
their versions and code, objects with saved values, kept revisions and
history, and calls that run as jobs (FakeFunctions' jobs: they queue, run and
finish as time passes; when one finishes its effect on the object is
applied). Shapes follow the real VEM's routes (axo_vem controllers/objects.py)
and the endpoint's state answer (ObjectCoordinator.handle_state)."""

from __future__ import annotations

import email.parser
import email.policy
import hashlib
import json
import random
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from axo_shared.active_objects.source import check_class_source, read_class

from fake_jobs import FakeFunctions, _epoch, _iso

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
HOT_MAX = 4096
DONE = ("COMPLETED", "FAILED", "CANCELLED")

CART_V1 = '''from axo import Axo, axo_method


class ShoppingCart(Axo):
    """A customer's cart: the items and their total."""

    def __init__(self, owner: str, currency: str = "MXN"):
        self.owner = owner
        self.currency = currency
        self.items = []
        self.total = 0.0

    @axo_method
    def add_item(self, sku: str, qty: int = 1, price: float = 10.0):
        """Adds a product (or more of one already in the cart).

        Args:
            sku: the product code, like A1
            qty: how many
            price: the price of one
        """
        self.items.append({"sku": sku, "qty": qty, "price": price})
        self.total = sum(i["qty"] * i["price"] for i in self.items)
        return {"items": len(self.items), "total": self.total}

    @axo_method
    def remove_item(self, sku: str):
        """Takes a product out of the cart."""
        self.items = [i for i in self.items if i["sku"] != sku]
        self.total = sum(i["qty"] * i["price"] for i in self.items)
        return {"items": len(self.items), "total": self.total}

    @axo_method(name="empty")
    def clear(self):
        """Removes everything."""
        self.items = []
        self.total = 0.0
'''

CART_V2 = CART_V1.replace('''    @axo_method(name="empty")''', '''    @axo_method
    def checkout(self, coupon: str = ""):
        """Places the order and empties the cart."""
        if not self.items:
            raise ValueError("the cart is empty")
        order = {"order_id": "ord-1042", "total": self.total, "coupon": coupon or None}
        self.orders.append(order)
        self.items = []
        self.total = 0.0
        return order

    def upgrade(self, old_version):
        self.orders = []

    @axo_method(name="empty")''')

COUNTER = '''from axo import Axo, axo_method


class Counter(Axo):
    """Counts visits."""

    def __init__(self, start: int = 0):
        self.count = start

    def run(self, by: int = 1):
        self.count += by
        return self.count

    @axo_method
    def reset(self):
        self.count = 0
'''

MODEL = '''import pickle

from axo import Axo, axo_method


class ModelCache(Axo):
    """A trained model kept warm between predictions."""

    def __init__(self, name: str):
        self.name = name
        self.model = None
        self.trained_on = None
        self.metrics = {}

    @axo_method
    def retrain(self, dataset: str, epochs: int = 3):
        """Trains again on a bucket file."""
        self.trained_on = dataset
        self.metrics = {"auc": 0.91, "epochs": epochs}

    @axo_method
    def predict(self, features: list):
        return {"score": 0.73}
'''

CLASSES = [("ShoppingCart", [CART_V1, CART_V2]), ("Counter", [COUNTER]), ("ModelCache", [MODEL])]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _attr(value: Any = None, kind: str = "json", size: Optional[int] = None) -> Dict[str, Any]:
    """One saved attribute as the endpoint shows it: small JSON values in
    full (the hot tier), anything else by size."""
    if kind == "json":
        text = json.dumps(value, sort_keys=True)
        size = len(text) if size is None else size
        out = {"tier": "hot" if size <= HOT_MAX else "blob", "kind": "json", "size": size, "hash": _hash(text)}
        if size <= HOT_MAX:
            out["value"] = value
        return out
    return {"tier": "blob", "kind": kind, "size": int(size or 0), "hash": _hash(f"{kind}:{size}:{random.random()}")}


class FakeObjects:
    def __init__(self, functions: FakeFunctions, history_page: Callable[[List[Dict[str, Any]], Any], List[Dict[str, Any]]]):
        self._fn = functions
        self._page = history_page
        self._ves: Dict[str, Dict[str, Any]] = {}
        self._rng = random.Random(5)

    # ── state ────────────────────────────────────────────────────────────────
    def _ve(self, ve_id: str, fve: Dict[str, Any]) -> Dict[str, Any]:
        ve = self._ves.get(ve_id)
        if ve is None:
            ve = self._ves[ve_id] = {"classes": {}, "objects": {}, "revs": {}, "history": [], "pending": {}}
            self._seed(ve, ve_id, fve)
        self._advance(ve, ve_id, fve)
        return ve

    def _fid(self, class_name: str) -> str:
        return f"cls-{class_name.lower()}-{_hash(class_name)[:6]}"

    def _add_class(self, ve, fve, ve_id, class_name, source, who, at, runtime_spec=None) -> Dict[str, Any]:
        versions = [v for (c, v) in ve["classes"] if c == class_name]
        version = max(versions, default=0) + 1
        read = read_class(source.encode(), class_name) or {"methods": [], "init": None, "has_upgrade": False,
                                                            "description": None}
        fid = self._fid(class_name)
        spec = {"type": "process", **(runtime_spec or {}), "call_style": "object"}
        doc = {"class_name": class_name, "version": version, "code_hash": _hash(source), "methods": read["methods"],
               "has_upgrade": read["has_upgrade"], "function_id": fid, "runtime_spec": spec, "init": read["init"],
               "description": read["description"], "created_at": _iso(at), "created_by": who}
        ve["classes"][(class_name, version)] = doc
        fve["functions"][(fid, version)] = {
            "function_id": fid, "version": version, "name": class_name, "runtime_spec": spec, "params_schema": [],
            "description": read["description"], "code_format": "source", "code_hash": doc["code_hash"],
            "endpoint_id": list(fve["endpoints"][:1]), "container_status": None, "deleted_at": None,
            "registered_by": who, "registered_by_name": self._fn._name_of(who), "registered_at": _iso(at),
            "has_display_source": False, "virtual_environment_id": ve_id}
        fve["code"][(fid, version)] = source
        return doc

    def _event(self, ve, kind, at, user, meta) -> None:
        ve["history"].append({"event_id": uuid.uuid4().hex, "created_at": _iso(at), "endpoint_id": None,
                              "user_id": user, "event_type": kind, "area": "object", "meta": meta})

    def _save(self, ve, obj, changes: Dict[str, Dict[str, Any]], deleted: List[str], at: float) -> int:
        """A new revision with these changes; past ones kept up to the object's setting."""
        revs = ve["revs"].setdefault(obj["name"], [])
        current = revs[0]["attributes"] if revs else {}
        attributes = {k: v for k, v in current.items() if k not in deleted}
        attributes.update(changes)
        revision = (revs[0]["revision"] if revs else 0) + 1
        revs.insert(0, {"revision": revision, "updated_at": at, "attributes": attributes})
        keep = obj.get("keep_revisions") if obj.get("keep_revisions") is not None else 10
        del revs[keep + 1:]
        obj.update(revision=revision, size_bytes=sum(a["size"] for a in attributes.values()))
        return revision

    def _values(self, ve, name) -> Dict[str, Any]:
        revs = ve["revs"].get(name) or []
        return {k: a.get("value") for k, a in (revs[0]["attributes"] if revs else {}).items()}

    def _effect(self, obj, values, method, params) -> tuple:
        """(result, changed values) of a method on these values -- each sample
        class's methods; any other class's calls change nothing."""
        cls = obj["class_name"]
        p = params or {}
        if method == "__create__":
            extra = {"ShoppingCart": {"currency": "MXN", "items": [], "total": 0.0}, "Counter": {"count": 0},
                     "ModelCache": {"model": None, "trained_on": None, "metrics": {}}}.get(cls, {})
            kwargs = dict(p.get("kwargs") or {})
            if cls == "Counter" and "start" in kwargs:
                kwargs = {"count": kwargs["start"]}
            return None, {**extra, **kwargs}
        if cls == "ShoppingCart":
            items = list(values.get("items") or [])
            if method == "add_item":
                items.append({"sku": p.get("sku", "A1"), "qty": int(p.get("qty", 1)), "price": float(p.get("price", 10.0))})
            elif method == "remove_item":
                items = [i for i in items if i.get("sku") != p.get("sku")]
            elif method in ("empty", "checkout"):
                if method == "checkout":
                    order = {"order_id": f"ord-{self._rng.randrange(1000, 9999)}",
                             "total": sum(i["qty"] * i["price"] for i in items), "coupon": p.get("coupon") or None}
                    return order, {"items": [], "total": 0.0,
                                   "orders": list(values.get("orders") or []) + [order]}
                items = []
            total = round(sum(i["qty"] * i["price"] for i in items), 2)
            return {"items": len(items), "total": total}, {"items": items, "total": total}
        if cls == "Counter":
            if method == "reset":
                return None, {"count": 0}
            count = int(values.get("count") or 0) + int(p.get("by", 1))
            return count, {"count": count}
        if cls == "ModelCache":
            if method == "retrain":
                return None, {"trained_on": p.get("dataset"), "metrics": {"auc": round(self._rng.uniform(0.85, 0.95), 3),
                                                                          "epochs": int(p.get("epochs", 3))}}
            return {"score": round(self._rng.random(), 3)}, {}
        return {"ok": True}, {}

    # ── jobs ─────────────────────────────────────────────────────────────────
    def _job(self, ve, fve, ve_id, obj, method, params, who, at, keep=None, fail=False, version=None):
        version = version or obj["class_version"]
        job = self._fn._new_job(fve, ve_id, obj["function_id"], version, params, who, "user", at, keep=keep)
        job.update(kind="object", object_name=obj["name"], object_method=method)
        job["_plan"]["fail"] = fail
        job["_plan"]["run_ms"] = self._rng.uniform(1500, 3500)
        ve["pending"][job["job_id"]] = {"object": obj["name"], "object_id": obj["object_id"], "method": method,
                                        "params": params, "who": who, "version": version}
        return job

    def _advance(self, ve, ve_id, fve) -> None:
        for job_id, op in list(ve["pending"].items()):
            job = fve["jobs"].get(job_id)
            if job is None or job["status"] not in DONE:
                continue
            del ve["pending"][job_id]
            self._finish(ve, fve, job, op)

    def _finish(self, ve, fve, job, op) -> None:
        obj = ve["objects"].get(op["object"])
        if obj is None or obj["object_id"] != op["object_id"]:
            return
        at = _epoch(job["finished_at"]) or time.time()
        method, ok = op["method"], job["status"] == "COMPLETED"
        meta = {"object_id": obj["object_id"], "job_id": job["job_id"], "method": method, "class_version": op["version"],
                "outcome": job.get("outcome") or ("completed" if ok else "failed_user"), "revision": obj["revision"],
                "changed": [], "deleted": [], "size_bytes": obj["size_bytes"], "duration_ms": job.get("duration_ms")}
        if not ok:
            meta["failure"] = {"error_class": (job.get("error") or {}).get("name"),
                               "message": (job.get("error") or {}).get("message")}
            obj.update(calls=obj["calls"] + 1, last_call_at=_iso(at), last_outcome=meta["outcome"], last_method=method)
            if method == "__create__":
                ve["objects"].pop(obj["name"], None)                  # __init__ failed: the name is free again
            self._event(ve, "ActiveObjectCalled", at, op["who"], meta)
            return
        if method == "__upgrade__":
            changes = {"orders": _attr([])} if obj["class_name"] == "ShoppingCart" else {}
            revision = self._save(ve, obj, changes, [], at)
            self._event(ve, "ActiveObjectUpgraded", at, op["who"], {
                "object_id": obj["object_id"], "from_version": obj["class_version"], "to_version": op["version"],
                "revision": revision})
            obj["class_version"] = op["version"]
            return
        result, changed = self._effect(obj, self._values(ve, obj["name"]), method, op["params"])
        fve["results"][job["job_id"]] = result
        if job.get("result"):
            job["result"]["size"] = len(json.dumps(result))
        if changed:
            meta["revision"] = self._save(ve, obj, {k: _attr(v) for k, v in changed.items()}, [], at)
            meta["changed"] = sorted(changed)
            meta["size_bytes"] = obj["size_bytes"]
        obj.update(calls=obj["calls"] + 1, last_call_at=_iso(at), last_outcome="completed", last_method=method)
        if method == "__create__":
            obj["status"] = "ready"
            self._event(ve, "ActiveObjectCreated", at, op["who"], {
                "object_id": obj["object_id"], "name": obj["name"], "class_name": obj["class_name"],
                "class_version": obj["class_version"], "created_by": op["who"], "cloned_from": None})
        self._event(ve, "ActiveObjectCalled", at, op["who"], meta)

    def _past_call(self, ve, fve, ve_id, obj, method, params, who, at, fail=False) -> float:
        """A call that already happened (seeding): its job finished at once."""
        job = self._job(ve, fve, ve_id, obj, method, params, who, at, fail=fail)
        op = ve["pending"].pop(job["job_id"])
        run_ms = self._rng.uniform(300, 2500)
        done = self._fn._finish_attempt(fve, ve_id, job, 1, "failed_user" if fail else "completed", at, run_ms, "first")
        job["_plan"] = None
        if not fail:
            self._fn._keep_result(fve, job, at)
        self._finish(ve, fve, job, op)
        return done

    def _seed(self, ve, ve_id, fve) -> None:
        now = time.time()
        owner, other = fve["owner"], self._fn.people["other"]
        for i, (class_name, sources) in enumerate(CLASSES):
            for v, source in enumerate(sources):
                self._add_class(ve, fve, ve_id, class_name, source, owner if v == 0 else other,
                                now - (20 - i - v * 6) * 86400)

        def make(name, class_name, version, at, kwargs, who=owner):
            obj = {"name": name, "object_id": f"obj-{uuid.uuid4().hex[:24]}", "class_name": class_name,
                   "class_version": version, "function_id": self._fid(class_name), "status": "creating",
                   "created_by": who, "created_at": _iso(at), "cloned_from": None, "revision": 0, "size_bytes": 0,
                   "calls": 0, "keep_revisions": None, "last_call_at": None, "last_outcome": None, "last_method": None}
            ve["objects"][name] = obj
            self._past_call(ve, fve, ve_id, obj, "__create__", {"args": [], "kwargs": kwargs}, who, at)
            return obj

        cart = make("cart-42", "ShoppingCart", 1, now - 9 * 86400, {"owner": "maria", "currency": "MXN"})
        t = now - 9 * 86400 + 600
        for sku, qty, price in (("A1", 2, 129.0), ("B7", 1, 49.5), ("C3", 3, 15.0)):
            t = self._past_call(ve, fve, ve_id, cart, "add_item", {"sku": sku, "qty": qty, "price": price}, owner, t) + 3600
        self._past_call(ve, fve, ve_id, cart, "remove_item", {"sku": "B7"}, owner, now - 2 * 3600)

        cart2 = make("cart-43", "ShoppingCart", 2, now - 3 * 86400, {"owner": "luis", "currency": "USD"}, other)
        t = now - 3 * 86400 + 300
        for k in range(14):
            sku = ["A1", "B7", "C3", "D9"][k % 4]
            t = self._past_call(ve, fve, ve_id, cart2, "add_item", {"sku": sku, "qty": 1 + k % 3, "price": 20.0 + k},
                                other if k % 3 else owner, t) + 3 * 3600
        self._past_call(ve, fve, ve_id, cart2, "checkout", {"coupon": ""}, owner, now - 7200, fail=False)
        self._past_call(ve, fve, ve_id, cart2, "checkout", {}, owner, now - 3600, fail=True)
        self._past_call(ve, fve, ve_id, cart2, "add_item", {"sku": "A1", "qty": 1, "price": 129.0}, owner, now - 600)

        visits = make("visits", "Counter", 1, now - 12 * 86400, {"start": 100})
        for k in range(6):
            self._past_call(ve, fve, ve_id, visits, "run", {"by": 1 + k}, owner, now - (6 - k) * 86400)

        model = make("churn-model", "ModelCache", 1, now - 5 * 86400, {"name": "churn"})
        self._save(ve, model, {"model": _attr(kind="pickle", size=4_400_000),
                                          "embeddings": _attr(kind="bytes", size=1_250_000),
                                          "vocabulary": _attr({"w": list(range(900))}, size=6_100)}, [], now - 5 * 86400 + 60)
        self._past_call(ve, fve, ve_id, model, "retrain", {"dataset": "demo/ml-data/raw/2026-09.csv", "epochs": 5},
                        owner, now - 2 * 86400)
        for k in range(3):
            self._past_call(ve, fve, ve_id, model, "predict", {"features": [0.2, 1.4, 3.0]}, owner, now - (3 - k) * 3600)

    # ── views ────────────────────────────────────────────────────────────────
    def _state(self, ve, obj, revision=None) -> Optional[Dict[str, Any]]:
        revs = ve["revs"].get(obj["name"]) or []
        if not revs:
            return None
        chosen = revs[0] if revision is None else next((r for r in revs if r["revision"] == revision), None)
        if chosen is None:
            return None
        busy = any(p["object"] == obj["name"] for p in ve["pending"].values())
        return {"name": obj["name"], "object_id": obj["object_id"], "revision": chosen["revision"],
                "size_bytes": sum(a["size"] for a in chosen["attributes"].values()), "updated_at": chosen["updated_at"],
                "attributes": json.loads(json.dumps(chosen["attributes"])), "busy": busy,
                "revisions": [{"revision": r["revision"], "updated_at": r["updated_at"],
                               "size_bytes": sum(a["size"] for a in r["attributes"].values())} for r in revs]}

    @staticmethod
    def _form(request) -> Dict[str, Any]:
        raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)
        form: Dict[str, Any] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            form[name] = part.get_content() if part.get_filename() is None else part.get_payload(decode=True)
        return form

    # ── routes ───────────────────────────────────────────────────────────────
    def route(self, ve_id: str, fve: Dict[str, Any], limits: Dict[str, Any], request, body, who: str, role: str, ok, err):
        """The response for an object / class route, or None (not one of them)."""
        method, path, q = request.method, request.url.path, request.url.params
        ve = self._ve(ve_id, fve)            # seeded with the VE, so its calls are in the jobs list too
        if not (path.startswith("/object-classes") or path.startswith("/objects")):
            return None
        admin = role in ("admin", "owner", None, "")
        missing_object = lambda name: err(404, "ACTIVE_OBJECT_NOT_FOUND", 9401, f"No object {name}")  # noqa: E731
        now = time.time()

        # ── classes ──
        if method == "GET" and path == "/object-classes":
            return ok(sorted(ve["classes"].values(), key=lambda d: (d["class_name"], d["version"])))
        if method == "POST" and path == "/object-classes/check":
            out = check_class_source((body.get("source") or "").encode(), body.get("class_name") or "")
            same = [d["version"] for d in ve["classes"].values()
                    if d["class_name"] == body.get("class_name") and d["code_hash"] == _hash(body.get("source") or "")]
            return ok({**out, "existing_version": max(same) if same else None})
        if method == "POST" and path == "/object-classes":
            form = self._form(request)
            class_name = str(form.get("class_name") or "")
            code = form.get("code") or b""
            source = code.decode() if isinstance(code, bytes) else str(code)
            check = check_class_source(source.encode(), class_name)
            if not check["ok"]:
                return err(422, "ACTIVE_OBJECT_CLASS_INVALID", 9410, check["problems"][0]["message"])
            same = [d for d in ve["classes"].values() if d["class_name"] == class_name and d["code_hash"] == _hash(source)]
            if same:
                top = max(same, key=lambda d: d["version"])
                return ok({"class_name": class_name, "version": top["version"], "function_id": top["function_id"],
                           "code_hash": top["code_hash"], "existing": True}, 201)
            doc = self._add_class(ve, fve, ve_id, class_name, source, who, now,
                                  json.loads(form.get("runtime_spec") or "{}"))
            return ok({"class_name": class_name, "version": doc["version"], "function_id": doc["function_id"],
                       "code_hash": doc["code_hash"], "existing": False}, 201)
        m = re.match(r"^/object-classes/([^/]+)(?:/(\d+))?$", path)
        if m:
            class_name, version = m.group(1), m.group(2)
            docs = sorted((d for (c, _), d in ve["classes"].items() if c == class_name), key=lambda d: d["version"])
            if not docs:
                return err(404, "ACTIVE_OBJECT_CLASS_NOT_FOUND", 9408, f"No class {class_name}")
            if method == "GET" and version is None:
                return ok(docs)
            if method == "DELETE" and version is not None:
                doc = ve["classes"].get((class_name, int(version)))
                if doc is None:
                    return err(404, "ACTIVE_OBJECT_CLASS_NOT_FOUND", 9408, f"No class {class_name} v{version}")
                if not (admin or doc["created_by"] == who):
                    return err(403, "VEM_FORBIDDEN", 9602,
                               "only an Admin, or the Developer who registered this version, can delete it")
                using = sorted(o["name"] for o in ve["objects"].values()
                               if o["class_name"] == class_name and o["class_version"] == int(version))
                if using:
                    return err(409, "ACTIVE_OBJECT_CLASS_IN_USE", 9411,
                               f"{class_name} v{version} is used by {len(using)} object(s): {', '.join(using[:5])}")
                del ve["classes"][(class_name, int(version))]
                fn = fve["functions"].get((doc["function_id"], int(version)))
                if fn:
                    fn["deleted_at"] = _iso(now)
                return ok({"class_name": class_name, "version": int(version), "deleted": True})

        # ── objects ──
        if method == "GET" and path == "/objects":
            return ok(sorted(ve["objects"].values(), key=lambda o: o["name"]))
        if method == "POST" and path == "/objects":
            name, class_name = body.get("name") or "", body.get("class_name") or ""
            if not NAME_RE.match(name):
                return err(422, "VEM_INVALID", 9605,
                           "An object name is 1-63 letters, digits, '.', '-' or '_' (starting with a letter or digit)")
            versions = [v for (c, v) in ve["classes"] if c == class_name]
            version = int(body.get("class_version") or max(versions, default=0))
            cls = ve["classes"].get((class_name, version))
            if cls is None:
                return err(404, "ACTIVE_OBJECT_CLASS_NOT_FOUND", 9408, f"No class {class_name} v{version}")
            if name in ve["objects"]:
                return err(409, "ACTIVE_OBJECT_EXISTS", 9402, f"An object named {name} already exists")
            obj = {"name": name, "object_id": f"obj-{uuid.uuid4().hex[:24]}", "class_name": class_name,
                   "class_version": version, "function_id": cls["function_id"], "status": "creating",
                   "created_by": who, "created_at": _iso(now), "cloned_from": None, "revision": 0, "size_bytes": 0,
                   "calls": 0, "keep_revisions": body.get("keep_revisions"), "last_call_at": None,
                   "last_outcome": None, "last_method": None}
            ve["objects"][name] = obj
            kwargs = body.get("kwargs") or {}
            job = self._job(ve, fve, ve_id, obj, "__create__", {"args": body.get("args") or [], "kwargs": kwargs},
                            who, now, fail=kwargs.get("fail") is True)
            return ok({"job_id": job["job_id"], "status": "QUEUED", "endpoint_id": job["endpoint_id"],
                       "object_id": obj["object_id"], "name": name}, 201)
        m = re.match(r"^/objects/([^/]+)(/calls|/upgrade|/clone|/restore|/state|/history)?$", path)
        if not m:
            return None
        name, action = m.group(1), m.group(2)
        obj = ve["objects"].get(name)
        if obj is None:
            return missing_object(name)
        if method == "GET" and action is None:
            return ok(obj)
        if method == "PATCH" and action is None:
            keep = body.get("keep_revisions")
            most = limits.get("object_max_keep_revisions")
            if keep is not None and (keep < 0 or (most is not None and keep > most)):
                return err(422, "VEM_INVALID", 9605, f"keep_revisions must be between 0 and {most}")
            obj["keep_revisions"] = keep
            return ok({**obj, "keep_revisions": keep})
        if method == "DELETE" and action is None:
            del ve["objects"][name]
            ve["revs"].pop(name, None)
            self._event(ve, "ActiveObjectDeleted", now, who, {"object_id": obj["object_id"]})
            return ok({"name": name, "object_id": obj["object_id"], "deleted": True})
        if method == "POST" and action == "/calls":
            cls = ve["classes"].get((obj["class_name"], obj["class_version"])) or {}
            names = sorted(x["name"] for x in cls.get("methods") or [])
            called = body.get("method") or "run"
            if names and called not in names:
                return err(404, "ACTIVE_OBJECT_METHOD_NOT_FOUND", 9409,
                           f"{obj['class_name']} has no callable method {called!r} (it has: {', '.join(names)})")
            params = body.get("params") or {}
            fail = params.get("fail") is True or (called == "checkout" and not self._values(ve, name).get("items"))
            job = self._job(ve, fve, ve_id, obj, called, params, who, now, keep=body.get("keep_result"), fail=fail)
            return ok({"job_id": job["job_id"], "status": "QUEUED", "endpoint_id": job["endpoint_id"]})
        if method == "POST" and action == "/upgrade":
            versions = [v for (c, v) in ve["classes"] if c == obj["class_name"]]
            target = int(body.get("class_version") or max(versions))
            if target not in versions:
                return err(404, "ACTIVE_OBJECT_CLASS_NOT_FOUND", 9408, f"No class {obj['class_name']} v{target}")
            if target <= obj["class_version"]:
                return err(409, "ACTIVE_OBJECT_STATE_CONFLICT", 9403,
                           f"{name} is on {obj['class_name']} v{obj['class_version']}; upgrade only moves to a newer version")
            job = self._job(ve, fve, ve_id, obj, "__upgrade__", {"to_version": target}, who, now, version=target)
            return ok({"job_id": job["job_id"], "status": "QUEUED", "endpoint_id": job["endpoint_id"]})
        if method == "POST" and action == "/clone":
            new = body.get("name") or ""
            if not NAME_RE.match(new):
                return err(422, "VEM_INVALID", 9605, "An object name is 1-63 letters, digits, '.', '-' or '_'")
            if new in ve["objects"]:
                return err(409, "ACTIVE_OBJECT_EXISTS", 9402, f"An object named {new} already exists")
            copy = {**obj, "name": new, "object_id": f"obj-{uuid.uuid4().hex[:24]}", "created_by": who,
                    "created_at": _iso(now), "cloned_from": name, "calls": 0, "last_call_at": None,
                    "last_outcome": None, "last_method": None, "status": "ready", "revision": 0}
            ve["objects"][new] = copy
            current = (ve["revs"].get(name) or [{"attributes": {}}])[0]
            self._save(ve, copy, json.loads(json.dumps(current["attributes"])), [], now)
            self._event(ve, "ActiveObjectCreated", now, who, {
                "object_id": copy["object_id"], "name": new, "class_name": copy["class_name"],
                "class_version": copy["class_version"], "created_by": who, "cloned_from": name})
            return ok({"object_id": copy["object_id"], "name": new, "cloned_from": name, "revision": copy["revision"]}, 201)
        if method == "POST" and action == "/restore":
            if any(p["object"] == name for p in ve["pending"].values()):
                return err(409, "ACTIVE_OBJECT_STATE_CONFLICT", 9403,
                           "The object is running a call; restore it when it's done")
            revs = ve["revs"].get(name) or []
            wanted = next((r for r in revs if r["revision"] == int(body.get("revision", -1))), None)
            if wanted is None:
                return err(404, "ACTIVE_OBJECT_NOT_FOUND", 9401, f"{name} keeps no revision {body.get('revision')}")
            current = revs[0]["attributes"]
            gone = [k for k in current if k not in wanted["attributes"]]
            revision = self._save(ve, obj, json.loads(json.dumps(wanted["attributes"])), gone, now)
            obj.update(calls=obj["calls"] + 1, last_call_at=_iso(now), last_outcome="completed", last_method="__restore__")
            self._event(ve, "ActiveObjectCalled", now, who, {
                "object_id": obj["object_id"], "job_id": f"restore-{revision}", "method": "__restore__",
                "class_version": obj["class_version"], "outcome": "completed", "revision": revision,
                "changed": sorted(wanted["attributes"]), "deleted": [], "size_bytes": obj["size_bytes"],
                "restored_from": wanted["revision"]})
            return ok({"name": name, "object_id": obj["object_id"], "revision": revision,
                       "restored_from": wanted["revision"]})
        if method == "GET" and action == "/state":
            state = self._state(ve, obj, int(q["revision"]) if q.get("revision") else None)
            if state is None:
                return err(404, "ACTIVE_OBJECT_NOT_FOUND", 9401, f"Object {obj['object_id']} has no saved state")
            return ok(state)
        if method == "GET" and action == "/history":
            return ok(self._page([r for r in ve["history"] if r["meta"].get("object_id") == obj["object_id"]], q))
        return None
