import time

import pytest

from .conftest import FAKE_USER, acting_as

OTHER = acting_as("user-2")


@pytest.fixture(autouse=True)
def functions(db, step_jobs):
    for function_id, handler in (("add", lambda p, e: {"sum": 3}), ("show", lambda p, e: {"seen": p})):
        db["functions"].insert_one({"_id": f"{function_id}:1", "function_id": function_id, "version": 1,
                                    "deleted_at": None, "params_schema": [{"name": "sum", "type": "json"}]
                                    if function_id == "show" else None})
        step_jobs.on(function_id, handler)


def _graph(*extra_nodes, edges=()):
    nodes = [{"node_id": "n1", "kind": "function", "function_id": "add", "position": {"x": 0, "y": 0}},
             *extra_nodes]
    return {"nodes": nodes, "edges": [{"edge_id": f"e{i}", "source_node_id": a, "target_node_id": b}
                                      for i, (a, b) in enumerate(edges)]}


SHOW = {"node_id": "n2", "kind": "function", "function_id": "show", "retry_delay_seconds": 0}


def _wait(client, name, run_id):
    for _ in range(400):
        body = client.get(f"/choreographies/{name}/runs/{run_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.01)
    raise AssertionError(body)


def test_create_get_and_versions(client):
    created = client.post("/choreographies", json={"name": "etl", "graph": _graph()})
    assert created.status_code == 201 and created.json()["owner_user_id"] == FAKE_USER.key
    detail = client.get("/choreographies/etl").json()
    assert detail["version"] == 1 and detail["active_runs"] == 0 and detail["warnings"] == []
    assert client.get(f"/choreographies/{created.json()['choreography_id']}").json()["name"] == "etl"

    updated = client.put("/choreographies/etl", json={"graph": _graph(SHOW, edges=[("n1", "n2")])},
                         headers=OTHER)
    assert updated.json()["version"] == 2 and updated.json()["updated_by"] == "user-2"
    assert [v["version"] for v in client.get("/choreographies/etl/versions").json()] == [2, 1]
    assert len(client.get("/choreographies/etl?version=1").json()["graph"]["nodes"]) == 1
    assert client.get("/choreographies").json()[0]["steps"] == 2


def test_errors_carry_their_catalog_names(client):
    missing = client.get("/choreographies/nope")
    assert missing.status_code == 404 and missing.json()["error"] == "CHOREOGRAPHY_NOT_FOUND"
    assert missing.json()["error_code"] == 9611
    client.post("/choreographies", json={"name": "etl", "graph": _graph()})
    taken = client.post("/choreographies", json={"name": "etl", "graph": _graph()})
    assert taken.status_code == 409 and taken.json()["error"] == "CHOREOGRAPHY_EXISTS"
    bad = client.post("/choreographies", json={"name": "x", "graph": _graph(
        {"node_id": "n2", "kind": "function", "function_id": "ghost"})})
    assert bad.status_code == 422 and bad.json()["error"] == "CHOREOGRAPHY_INVALID"
    check = client.post("/choreographies/check", json={"graph": _graph(edges=[("n1", "n1")])}).json()
    assert check["ok"] is False and "connected to itself" in check["problems"][0]
    designer = client.get("/choreographies/designer").json()
    assert {s["name"] for s in designer["merge_strategies"]} >= {"priority", "from", "random", "collect", "none"}
    assert designer["step_settings"] and designer["condition_operators"]


def test_save_by_name_run_and_follow(client):
    assert client.post("/choreographies/save", json={"name": "s", "graph": _graph()}).json()["version"] == 1
    saved = client.post("/choreographies/save", json={"name": "s", "graph": _graph(SHOW, edges=[("n1", "n2")])})
    assert saved.json()["version"] == 2

    started = client.post("/choreographies/s/runs", json={"input": {"x": 1}, "run_id": "my-run-0001"})
    assert started.status_code == 201
    run = _wait(client, "s", "my-run-0001")
    assert run["status"] == "completed" and run["result"] == {"n2": {"job_id": run["steps"]["n2"]["job_id"]}}
    answers = client.get("/choreographies/s/runs/my-run-0001/result").json()
    assert answers == {"run_id": "my-run-0001", "result": {"n2": {"seen": {"sum": 3}}}}
    assert run["acting_user_id"] == FAKE_USER.key
    again = client.post("/choreographies/s/runs", json={"run_id": "my-run-0001"})
    assert again.json()["status"] == "completed"                 # the same id = the same run
    assert client.get("/choreography-runs/my-run-0001").json()["run_id"] == "my-run-0001"

    old = client.post("/choreographies/s/runs", json={"version": 1}).json()
    assert _wait(client, "s", old["run_id"])["version"] == 1
    runs = client.get("/choreographies/s/runs").json()
    assert len(runs) == 2 and runs[0]["counts"]["completed"] >= 1
    usage = client.get("/choreographies/s/usage").json()
    assert usage["runs"] == 2 and usage["by_version"] == {"2": 1, "1": 1}


def test_cancel_resume_and_wrong_choreography(client, step_jobs):
    gate = step_jobs.hold("add")
    client.post("/choreographies", json={"name": "c", "graph": _graph()})
    client.post("/choreographies", json={"name": "d", "graph": _graph()})
    run_id = client.post("/choreographies/c/runs").json()["run_id"]
    assert client.get(f"/choreographies/d/runs/{run_id}").status_code == 404
    assert client.post(f"/choreographies/c/runs/{run_id}/cancel").status_code == 200
    run = _wait(client, "c", run_id)
    assert run["status"] == "cancelled"
    again = client.post(f"/choreographies/c/runs/{run_id}/cancel")
    assert again.status_code == 409 and again.json()["error"] == "CHOREOGRAPHY_RUN_NOT_ACTIVE"
    del step_jobs.gates["add"]
    gate.clear()
    assert client.post(f"/choreographies/c/runs/{run_id}/resume").status_code == 200
    assert _wait(client, "c", run_id)["status"] == "completed"
    done = client.post(f"/choreographies/c/runs/{run_id}/resume")
    assert done.status_code == 409 and done.json()["error"] == "CHOREOGRAPHY_RUN_NOT_RESUMABLE"


def test_triggers_and_bucket_events(client):
    client.post("/choreographies", json={"name": "t", "graph": _graph()})
    trigger = client.post("/choreographies/t/triggers", json={"bucket": "alice/raw"}, headers=OTHER)
    assert trigger.status_code == 201 and trigger.json()["created_by"] == "user-2"
    # What the Control Plane reads right after a trigger change.
    listed = client.get("/trigger-buckets").json()
    assert listed["triggers"] == [{"bucket": "alice/raw", "created_by": "user-2"}] and listed["at"]
    runs = client.post("/bucket-events", json={"bucket": "alice/raw", "name": "a.csv", "version": 1}).json()["runs"]
    assert len(runs) == 1
    run = _wait(client, "t", runs[0]["run_id"])
    assert run["started_by_type"] == "trigger" and run["acting_user_id"] == "user-2"
    assert run["trigger"]["name"] == "a.csv"
    removed = client.delete(f"/choreographies/t/triggers/{trigger.json()['trigger_id']}")
    assert removed.json()["removed"] is True
    assert client.post("/bucket-events", json={"bucket": "alice/raw", "name": "b.csv"}).json()["runs"] == []
    assert client.get("/trigger-buckets").json()["triggers"] == []


def test_queue_full_is_429(client, step_jobs):
    step_jobs.hold("add")
    client.post("/choreographies", json={"name": "q", "graph": _graph(),
                                         "settings": {"max_parallel_runs": 1, "queue_limit": 1}})
    assert client.post("/choreographies/q/runs").status_code == 201
    assert client.post("/choreographies/q/runs").status_code == 201
    full = client.post("/choreographies/q/runs")
    assert full.status_code == 429 and full.json()["error"] == "CHOREOGRAPHY_QUEUE_FULL"
    step_jobs.cancel("ep-0", "-")                          # releases the held jobs


def test_delete_cancels_runs_then_purge(client, step_jobs):
    step_jobs.hold("add")
    created = client.post("/choreographies", json={"name": "gone", "graph": _graph()}).json()
    client.post("/choreographies/gone/runs")
    deleted = client.delete("/choreographies/gone").json()
    assert deleted["deleted"] is True and deleted["runs_cancelled"] == 1
    assert client.get("/choreographies/gone").status_code == 404
    purged = client.delete(f"/choreographies/{created['choreography_id']}/purge")
    assert purged.json()["purged"] is True
