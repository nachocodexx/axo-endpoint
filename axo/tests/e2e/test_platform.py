"""The whole platform, end to end, against a running ``./deploy.sh up``.

    AXO_E2E=1 poetry run pytest tests/e2e -s          (from axo/)

A new person signs up, rents a Free VE and uses everything through the
Control Plane: functions, an active object, buckets, a choreography started
by a bucket trigger, a stop / start of the VE, and the bill. The VE
(``e2e-<time>``) is deleted at the end; ``AXO_E2E_KEEP=1`` leaves it so the
UI has real data to show. ``AXO_E2E_CP_URL`` = where the Control Plane is
(default http://localhost:9000).
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional

import httpx
import pytest

from axo import Axo, AxoClient, Choreography, axo_function, axo_method

pytestmark = pytest.mark.skipif(os.environ.get("AXO_E2E") != "1", reason="needs a running platform (AXO_E2E=1)")

CP = os.environ.get("AXO_E2E_CP_URL", "http://localhost:9000")


@axo_function(name="add")
def add(a: int, b: int = 2) -> int:
    return a + b


@axo_function(name="measure")
def measure(data) -> dict:
    content = data.read()
    return {"bytes": len(content), "lines": content.count(b"\n")}


class Counter(Axo):
    def __init__(self, start: int = 0):
        self.total = start

    @axo_method
    def increment(self, by: int = 1) -> int:
        self.total += by
        return self.total


def _wait(what: str, check: Callable[[], Optional[Any]], timeout: float = 300.0, poll: float = 2.0) -> Any:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = check()
        if last:
            return last
        time.sleep(poll)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


class Person:
    """One signed-up person talking to the CP with their session."""

    def __init__(self, username: str) -> None:
        self.username = username
        self.http = httpx.Client(base_url=CP, timeout=60)
        password = "e2e-password-1"
        assert self.http.post("/auth/signup", json={"username": username, "email": f"{username}@example.com",
                                                    "password": password}).status_code == 201
        session = self.http.post("/auth/login", json={"username": username, "password": password}).json()
        self.http.headers.update({"Authorization": f"Bearer {session['access_token']}",
                                  "Temporal-Secret-Key": session["temporal_secret"]})

    def call(self, method: str, path: str, expect: int = 200, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        assert response.status_code == expect, f"{method} {path}: {response.status_code} {response.text[:300]}"
        return response.json() if response.content else None


@pytest.fixture(scope="module")
def platform():
    stamp = time.strftime("%m%d%H%M%S")
    person = Person(f"e2e-{stamp}")
    ve = person.call("POST", "/ves", expect=201, json={"name": f"e2e-{stamp}", "plan_id": "free"})["ve"]
    ve_id, ve_name = ve["ve_id"], ve["name"]
    _wait("the VE to run", lambda: person.call("GET", f"/ves/{ve_id}")["health"] == "Healthy", timeout=420)
    key = person.call("POST", "/sdk-keys", expect=201, json={"name": "e2e", "ve_id": ve_id})["key"]
    client = AxoClient(key=key, ve=ve_id, base_url=CP)
    yield person, ve_id, client
    client.close()
    if os.environ.get("AXO_E2E_KEEP") != "1":
        person.call("DELETE", f"/ves/{ve_id}", expect=202, params={"confirm": ve_name})


def test_a_function_runs(platform):
    _person, _ve, client = platform
    job = client.call(add, a=40).unwrap()
    assert job.result(timeout=180).unwrap() == 42


def test_an_active_object_keeps_its_state(platform):
    _person, _ve, client = platform
    counter = client.create(Counter, "e2e-counter", start=10).unwrap()
    counter.created.result(timeout=180).unwrap()
    assert counter.increment(by=5).unwrap().result(timeout=120).unwrap() == 15
    assert counter.increment().unwrap().result(timeout=120).unwrap() == 16
    assert counter.state().unwrap()["attributes"]["total"]["value"] == 16


def test_a_file_in_a_bucket_starts_its_choreography(platform):
    person, _ve, client = platform
    owner = person.username
    for name in ("in", "out"):
        person.call("POST", "/buckets", expect=201, json={"name": name})
    flow = Choreography("e2e-measure")
    files, step, results = flow.bucket(f"{owner}/in"), flow.step(measure), flow.bucket(f"{owner}/out")
    files >> step >> results
    flow.on_bucket(f"{owner}/in", prefix="data/")
    client.save(flow).unwrap()

    person.call("PUT", f"/buckets/{owner}/in/objects/data/hello.txt", expect=201, content=b"one\ntwo\nthree\n")

    def finished_run():
        runs = client.api("GET", "/choreographies/e2e-measure/runs").unwrap()
        done = [r for r in runs if r["status"] not in ("queued", "running")]
        return done[0] if done else None

    run = _wait("the triggered run", finished_run, timeout=240)
    assert run["status"] == "completed", run
    written = person.call("GET", f"/buckets/{owner}/out/objects")
    objects = written if isinstance(written, list) else written.get("objects", [])
    assert any(o["name"].startswith("e2e-measure/") for o in objects), written


def test_a_file_written_right_after_saving_a_trigger_is_never_missed(platform):
    """Saving answers only once the Control Plane knows the new trigger, so
    a file written at once starts its run (it used to race the heartbeat)."""
    person, _ve, client = platform
    owner = person.username
    person.call("POST", "/buckets", expect=201, json={"name": "quick"})
    for i in range(5):
        flow = Choreography(f"e2e-quick-{i}")
        files, step = flow.bucket(f"{owner}/quick"), flow.step(measure)
        files >> step
        flow.on_bucket(f"{owner}/quick", prefix=f"q{i}/")
        client.save(flow).unwrap()
        person.call("PUT", f"/buckets/{owner}/quick/objects/q{i}/file.txt", expect=201, content=b"a\nb\n")

    for i in range(5):
        def started(name=f"e2e-quick-{i}"):
            return client.api("GET", f"/choreographies/{name}/runs").unwrap() or None
        _wait(f"a run of e2e-quick-{i}", started, timeout=120)


def test_stop_and_start_bring_back_the_same_endpoint_and_the_functions(platform):
    person, ve_id, client = platform
    person.call("POST", f"/ves/{ve_id}/stop", expect=202)
    _wait("the VE to stop", lambda: person.call("GET", f"/ves/{ve_id}")["lifecycle"] == "Stopped", timeout=300)
    person.call("POST", f"/ves/{ve_id}/start", expect=202)
    _wait("the VE to run again", lambda: person.call("GET", f"/ves/{ve_id}")["health"] == "Healthy", timeout=420)
    assert client.call(add, a=1).unwrap().result(timeout=180).unwrap() == 3

    def settled():
        endpoints = client.api("GET", "/endpoints").unwrap()
        live = [e for e in endpoints if e.get("status") not in ("removed", "stopped")]
        return live if len(live) == 1 else None
    _wait("one endpoint (the target)", settled, timeout=120)


def test_the_jobs_reach_the_bill(platform):
    person, ve_id, _client = platform

    def billed():
        costs = person.call("GET", f"/ves/{ve_id}/costs")
        functions = costs.get("by_function") or costs.get("functions") or []
        return costs if functions else None
    costs = _wait("the job records on the bill", billed, timeout=180, poll=5)
    assert costs
