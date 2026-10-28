import json

import httpx
import pytest

from axo import AxoClient
from axo.client.client import ClassRef
from tests import samples


class FakeVe:
    """Answers like the Control Plane front door + the VEM routes."""

    def __init__(self):
        self.requests = []
        self.polls = 0
        self.functions = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "axo_k_s"
        path = request.url.path
        assert path.startswith("/ves/ve-1/api/")
        path = path[len("/ves/ve-1/api"):]
        self.requests.append((request.method, path))
        if (request.method, path) == ("POST", "/functions"):
            body = request.content.decode("latin-1")
            assert 'name="call_style"' in body and "kwargs" in body
            return httpx.Response(201, json={"function_id": "add", "version": 1, "endpoint_id": "ep-0"})
        if (request.method, path) == ("GET", "/functions"):
            return httpx.Response(200, json=[
                {"function_id": "add", "name": "add", "version": 1, "is_default": True, "deleted_at": None},
                {"function_id": "add", "name": "add", "version": 2, "is_default": False, "deleted_at": None},
            ])
        if (request.method, path) == ("POST", "/functions/add/1/jobs"):
            assert json.loads(request.content) == {"params": {"a": 1, "b": 2}}
            return httpx.Response(200, json={"job_id": "j1", "endpoint_id": "ep-0"})
        if (request.method, path) == ("GET", "/jobs/j1"):
            self.polls += 1
            status = ("QUEUED", "RUNNING", "COMPLETED")[min(self.polls, 3) - 1]
            return httpx.Response(200, json={"job_id": "j1", "endpoint_id": "ep-0", "status": status})
        if (request.method, path) == ("GET", "/jobs/j1/result"):
            return httpx.Response(200, json=3)
        if (request.method, path) == ("POST", "/jobs/j1/cancel"):
            return httpx.Response(200, json={"job_id": "j1", "cancelled": True})
        if (request.method, path) == ("POST", "/object-classes"):
            return httpx.Response(201, json={"class_name": "Counter", "version": 2})
        if (request.method, path) == ("POST", "/objects"):
            assert json.loads(request.content) == {"name": "visits", "class_name": "Counter", "class_version": 2,
                                                   "args": [5], "kwargs": {}}
            return httpx.Response(201, json={"job_id": "c1", "endpoint_id": "ep-0"})
        if (request.method, path) == ("POST", "/objects/visits/calls"):
            assert json.loads(request.content) == {"method": "run", "params": {"by": 2}}
            return httpx.Response(200, json={"job_id": "j2", "endpoint_id": None})
        if (request.method, path) == ("GET", "/jobs/j2"):
            return httpx.Response(200, json={"job_id": "j2", "endpoint_id": "ep-1", "status": "FAILED", "attempt": 2,
                                             "error": {"name": "FUNCTION_EXECUTION_FAILED", "fault": "code",
                                                       "message": "TypeError: nope"}})
        if (request.method, path) == ("GET", "/objects/nope"):
            return httpx.Response(404, json={"error": "ACTIVE_OBJECT_NOT_FOUND", "error_code": 9401,
                                             "message": "No object nope", "details": {}})
        return httpx.Response(500, json={"detail": f"unexpected {request.method} {path}"})


@pytest.fixture
def ve():
    return FakeVe()


@pytest.fixture
def client(ve):
    return AxoClient(key="axo_k_s", ve="ve-1", base_url="http://cp", transport=httpx.MockTransport(ve))


def test_a_function_registers_once_and_its_job_result_is_waited_for(client, ve):
    job = client.call(samples.add, a=1, b=2).unwrap()
    assert job.result(sleep=lambda s: None).unwrap() == 3
    client.call(samples.add, a=1, b=2).unwrap()
    assert ve.requests.count(("POST", "/functions")) == 1
    assert job.cancel().unwrap()["cancelled"] is True


def test_objects_are_created_and_called_by_name(client, ve):
    visits = client.create(samples.Counter, "visits", 5).unwrap()
    assert visits.created.job_id == "c1"
    job = visits.run(by=2).unwrap()
    err = job.result(sleep=lambda s: None).unwrap_err()
    assert err.name == "JOB_FAILED" and "nope" in err.message and job.endpoint_id == "ep-1"
    assert err.details["fault"] == "code" and err.details["attempt"] == 2


def test_a_known_class_version_skips_registering(client, ve):
    client.create(ClassRef("Counter", 2), "visits", 5).unwrap()
    assert ("POST", "/object-classes") not in ve.requests


def test_platform_errors_keep_their_catalog_name(client):
    err = client.object("nope").info().unwrap_err()
    assert (err.name, err.code, err.status) == ("ACTIVE_OBJECT_NOT_FOUND", 9401, 404)


def test_an_unreachable_platform_is_an_error_not_an_exception():
    def down(request):
        raise httpx.ConnectError("refused")

    client = AxoClient(key="k", ve="v", transport=httpx.MockTransport(down))
    assert client.object("x").info().unwrap_err().name == "NETWORK_ERROR"


def test_the_key_and_ve_are_required(monkeypatch):
    monkeypatch.delenv("AXO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        AxoClient(ve="v")


def test_a_call_can_say_how_long_its_result_is_kept_and_how_it_is_tried_again(ve):
    seen = []

    def answer(request):
        if request.url.path.endswith("/jobs") and request.method == "POST":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"job_id": "j9", "endpoint_id": "ep-0"})
        return ve(request)

    client = AxoClient(key="axo_k_s", ve="ve-1", base_url="http://cp", transport=httpx.MockTransport(answer))
    client.call(samples.add, keep_result="never", max_attempts=3, retry_backoff="exponential", a=1, b=2).unwrap()
    assert seen == [{"params": {"a": 1, "b": 2}, "keep_result": "never", "max_attempts": 3,
                     "retry_backoff": "exponential"}]


def test_a_registered_function_is_called_by_name_at_its_default_version(client, ve):
    ref = client.function("add").unwrap()
    assert (ref.function_id, ref.version) == ("add", 1)          # the picked default, not the newest
    assert client.call(ref, a=1, b=2).is_ok
    assert client.function("add", version=2).unwrap().version == 2
    assert client.function("nope").unwrap_err().name == "FUNCTION_NOT_FOUND"
