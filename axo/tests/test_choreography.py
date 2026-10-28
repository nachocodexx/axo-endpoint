import pytest

from axo import Choreography, DataRef, Merge, When, axo_function
from axo.client.client import FunctionRef
from axo.client.handles import ObjectHandle
from axo.core.context import LocalContext, Store
from axo.runtime import call_function


@axo_function(name="load")
def load(day, limit=10, opts=None, ctx=None):
    return {"day": day, "limit": limit}


def test_params_schema_records_defaults():
    assert load.spec.params_schema() == [
        {"name": "day", "type": "json", "required": True},
        {"name": "limit", "type": "json", "required": False, "default": 10},
        {"name": "opts", "type": "json", "required": False},
    ]
    assert load.spec.to_dict()["optional"] == ["limit", "opts"]


def test_building_a_graph():
    ch = Choreography("etl", queue_limit=5)
    raw = ch.bucket("alice/raw", files=["a.csv"])
    first = ch.step(load, params={"limit": 3}, max_attempts=2)
    second = ch.step(load)                                   # the same function twice: its own id
    obj = ch.step(ObjectHandle(None, "totals"), method="add", join="arrived")
    out = ch.bucket("alice/out", key_pattern="{run_id}/{source}.json")
    raw >> [first, second] >> obj
    obj >> out
    graph = ch.graph(lambda step: ("fid", None, "load"))
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    assert list(nodes) == ["raw", "load", "load-2", "totals-add", "out"]
    assert nodes["raw"]["selected_items"] == [{"name": "a.csv"}]
    assert nodes["load"]["params"] == {"limit": 3} and nodes["load"]["max_attempts"] == 2
    assert nodes["load"]["function_version"] is None
    assert nodes["totals-add"] == {**nodes["totals-add"], "kind": "object", "object_name": "totals",
                                   "method": "add", "join": "arrived"}
    assert [(e["source_node_id"], e["target_node_id"]) for e in graph["edges"]] == [
        ("raw", "load"), ("raw", "load-2"), ("load", "totals-add"), ("load-2", "totals-add"), ("totals-add", "out")]


def test_builder_refuses_mistakes():
    ch = Choreography("x")
    ch.step(load, name="a")
    with pytest.raises(ValueError):
        ch.step(load, name="a")
    with pytest.raises(ValueError):
        ch.step(load, method="run")
    other = Choreography("y").step(load)
    with pytest.raises(ValueError):
        ch.steps[0] >> other
    assert ch.step(FunctionRef("f1", 2, "f"), version=2).options["function_version"] == 2


def test_data_parameters_become_data_refs():
    seen = {}

    def use(data, n=1):
        seen["data"] = data
        return n

    ref = {"__axo_data__": {"bucket": "alice/raw", "name": "a.csv", "version": 2, "format": "csv"}}
    assert call_function(use, {"data": ref, "n": 4}, LocalContext()) == 4
    assert seen["data"] == DataRef("alice/raw", "a.csv", 2, "csv")
    assert seen["data"].to_dict()["__axo_data__"]["name"] == "a.csv"
    assert DataRef.from_value({"store": "vem", "id": "x"}) is None
    assert call_function(use, {"data": [ref, ref]}, LocalContext()) == 1          # all files at once
    assert seen["data"] == [DataRef("alice/raw", "a.csv", 2, "csv")] * 2


def test_files_mode_and_completed_with_errors_setting():
    ch = Choreography("m", allow_completed_with_errors=False)
    ch.step(load, files_mode="all_at_once")
    assert ch.settings == {"allow_completed_with_errors": False}
    assert ch.graph(lambda step: ("fid", None, "load"))["nodes"][0]["files_mode"] == "all_at_once"
    with pytest.raises(ValueError):
        ch.step(load, files_mode="batch")


def test_store_put_without_the_platform_says_so():
    from axo.errors import AxoStoreUnavailableError
    with pytest.raises((AxoStoreUnavailableError, ImportError)):
        Store(LocalContext(output_buckets=["b"])).put("k", 1)


def test_merge_rules():
    ch = Choreography("fan")
    a, b = ch.step(load, name="a"), ch.step(load, name="b")
    one = ch.step(load, name="one", merge="random")
    each = ch.step(load, name="each", merge={"*": Merge.priority(b, a), "day": a, "limit": "sum",
                                              "opts": Merge.collect(as_list=True), "x": {"from": "b"}})
    [a, b] >> one
    [a, b] >> each
    nodes = {n["node_id"]: n for n in ch.graph(lambda step: ("fid", None, "load"))["nodes"]}
    assert nodes["one"]["merge"] == {"*": {"strategy": "random"}}
    assert nodes["each"]["merge"] == {
        "*": {"strategy": "priority", "order": ["b", "a"]}, "day": {"strategy": "from", "source": "a"},
        "limit": {"strategy": "reduce", "reducer": "sum"}, "opts": {"strategy": "collect", "as_list": True},
        "x": {"strategy": "from", "source": "b"}}
    assert "merge" not in nodes["a"]
    with pytest.raises(ValueError):
        ch.step(load, merge="loudest")
    with pytest.raises(ValueError):
        Merge.reduce("avg")


def test_conditions_on_arrows():
    ch = Choreography("branch")
    count, report, alert, log, end = (ch.step(load, name=n) for n in ("count", "report", "alert", "log", "end"))
    count.when(When("rows") > 0) >> report
    count.when((When("rows") == 0) | When("error").exists()) >> alert
    count.otherwise() >> log
    [report.when(When("ok").is_true() & When("tags").contains("eu")), alert] >> end
    edges = {(e["source_node_id"], e["target_node_id"]): e for e in ch.graph(lambda s: ("f", None, "l"))["edges"]}
    assert edges[("count", "report")]["condition"] == {"match": "all", "rules": [
        {"field": "rows", "op": ">", "value": 0}]}
    assert edges[("count", "alert")]["condition"] == {"match": "any", "rules": [
        {"field": "rows", "op": "==", "value": 0}, {"field": "error", "op": "exists", "value": None}]}
    assert edges[("count", "log")] == {**edges[("count", "log")], "otherwise": True} and \
        "condition" not in edges[("count", "log")]
    assert [r["op"] for r in edges[("report", "end")]["condition"]["rules"]] == ["is_true", "contains"]
    assert "condition" not in edges[("alert", "end")]
    nested = ((When("a") > 1) & (When("b") < 2)) | When("c").is_in([1, 2])
    assert nested.to_dict() == {"match": "any", "rules": [
        {"match": "all", "rules": [{"field": "a", "op": ">", "value": 1}, {"field": "b", "op": "<", "value": 2}]},
        {"field": "c", "op": "in", "value": [1, 2]}]}
    with pytest.raises(ValueError):
        ch.connect(count, end, when=When("x") > 1, otherwise=True)


def test_on_failure_arrows():
    ch = Choreography("safe")
    train, alert, save = (ch.step(load, name=n) for n in ("train", "alert", "save"))
    train >> save
    train.on_failure() >> alert
    edges = {(e["source_node_id"], e["target_node_id"]): e for e in ch.graph(lambda s: ("f", None, "l"))["edges"]}
    assert edges[("train", "alert")]["on_failure"] is True and "on_failure" not in edges[("train", "save")]
    with pytest.raises(ValueError):
        ch.connect(train, alert, when=When("x") > 1, on_failure=True)
    with pytest.raises(ValueError):
        train.on_failure() >> ch.bucket("alice/out")


def test_triggers_are_part_of_the_graph():
    ch = Choreography("ship")
    raw = ch.bucket("alice/raw")
    pack = ch.step(load)
    raw >> pack
    assert "triggers" not in ch.graph(lambda step: ("fid", None, "load"))     # none declared: keep the saved ones
    ch.on_bucket("alice/raw", prefix="in/")
    ch.on_event("order.paid", when=When("total") > 100)
    graph = ch.graph(lambda step: ("fid", None, "load"))
    assert graph["triggers"] == [
        {"event_type": "file.written", "bucket": "alice/raw", "prefix": "in/", "targets": ["raw"]},
        {"event_type": "order.paid", "targets": [],
         "condition": {"match": "all", "rules": [{"field": "total", "op": ">", "value": 100}]}},
    ]
    assert all("position" not in n for n in graph["nodes"])               # the designer arranges it
    with pytest.raises(ValueError):
        ch.on_event("file.deleted")


def test_save_keeps_the_saved_triggers_sends_events_and_sets_who_sees_runs():
    import json

    import httpx

    from axo import AxoClient

    seen = []

    def answer(request):
        path = request.url.path.split("/api", 1)[1]
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, path, body))
        if (request.method, path) == ("GET", "/choreographies/ship"):
            return httpx.Response(200, json={"graph": {"triggers": [{"trigger_id": "tr-1", "event_type": "x.y"}]}})
        if path == "/choreographies/save":
            return httpx.Response(200, json={"name": "ship", "version": 2})
        if path.startswith("/visibility/"):
            return httpx.Response(200, json={"mode": "teams", "teams": body["teams"]})
        return httpx.Response(201, json={"id": body.get("event_id"), "runs": []})

    client = AxoClient(key="axo_k_s", ve="ve-1", base_url="http://cp", transport=httpx.MockTransport(answer))
    ch = Choreography("ship", visibility=["team-blue"])
    ch.step(ObjectHandle(None, "orders"), method="pack")
    saved = client.save(ch).unwrap()
    save_body = next(b for m, p, b in seen if p == "/choreographies/save")
    assert save_body["graph"]["triggers"] == [{"trigger_id": "tr-1", "event_type": "x.y"}]
    assert saved["visibility"] == {"mode": "teams", "teams": ["team-blue"]}
    assert client.emit("order.paid", {"total": 5}, id="pay-1").unwrap()["id"] == "pay-1"
    assert seen[-1] == ("POST", "/events", {"type": "order.paid", "data": {"total": 5}, "event_id": "pay-1"})


def test_a_choreography_step_calls_another_choreography():
    ch = Choreography("daily", answer_step="report")
    call = ch.step("clean-visits", params={"day": "mon"}, version=3, timeout_seconds=60)
    report = ch.step(load, name="report")
    call >> report
    graph = ch.graph(lambda step: ("fid", None, "load"), lambda step: ("cid-1", "clean-visits"))
    node = graph["nodes"][0]
    assert node == {"node_id": "clean-visits", "kind": "choreography", "choreography_id": "cid-1",
                    "choreography_name": "clean-visits", "choreography_version": 3, "params": {"day": "mon"},
                    "timeout_seconds": 60}
    assert ch.settings == {"answer_step": "report"} and ch.answer(call).settings["answer_step"] == "clean-visits"
    with pytest.raises(ValueError):
        ch.step("other", max_attempts=2)            # its own steps have their tries
    with pytest.raises(ValueError):
        Choreography("x").step("y").choreography.graph(lambda step: None)


def test_save_saves_a_called_choreography_first_and_refers_to_it_by_id():
    import json

    import httpx

    from axo import AxoClient

    seen = []

    def answer(request):
        path = request.url.path.split("/api", 1)[1]
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, path, body))
        if (request.method, path) == ("GET", "/choreographies/inner"):
            return httpx.Response(200, json={"choreography_id": "cid-inner", "name": "inner", "graph": {}})
        if request.method == "GET":
            return httpx.Response(404, json={"error": "CHOREOGRAPHY_NOT_FOUND", "error_code": 9611, "message": "x"})
        if path == "/choreographies/save":
            return httpx.Response(200, json={"name": body["name"], "version": 1})
        return httpx.Response(404, json={})

    client = AxoClient(key="axo_k_s", ve="ve-1", base_url="http://cp", transport=httpx.MockTransport(answer))
    inner = Choreography("inner")
    inner.step(ObjectHandle(None, "orders"), method="pack")
    outer = Choreography("outer")
    outer.step(inner)
    client.save(outer).unwrap()
    saves = [b for m, p, b in seen if p == "/choreographies/save"]
    assert [b["name"] for b in saves] == ["inner", "outer"]
    assert saves[1]["graph"]["nodes"][0]["choreography_id"] == "cid-inner"
