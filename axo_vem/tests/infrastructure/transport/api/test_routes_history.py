from datetime import datetime, timedelta, timezone

from axo_shared.events import models

from fastapi.testclient import TestClient

from axo_vem.infrastructure.transport.api.app import create_app

from .conftest import CP_HEADERS, _fake_current_user


def _seed_function_registered(kurrent_appender, function_id="add", endpoint_id="n0"):
    event = models.FunctionRegistered(endpoint_id=endpoint_id, function_id=function_id, version=1)
    kurrent_appender.append_to_stream(
        f"functions-{function_id}", models.FUNCTION_REGISTERED, event.model_dump(mode="json"),
    )
    return event


def _seed_endpoint_started(kurrent_appender, endpoint_id="n0"):
    event = models.EndpointStarted(
        endpoint_id=endpoint_id, router_bind="tcp://0.0.0.0:5555", pub_bind="tcp://0.0.0.0:5556",
    )
    kurrent_appender.append_to_stream(f"endpoints-{endpoint_id}", models.ENDPOINT_STARTED, event.model_dump(mode="json"))
    return event


def test_history_with_no_params_returns_full_timeline(client, kurrent_appender):
    _seed_function_registered(kurrent_appender, function_id="add")
    _seed_endpoint_started(kurrent_appender, endpoint_id="n0")

    response = client.get("/history")
    assert response.status_code == 200
    event_types = {e["event_type"] for e in response.json()}
    assert event_types == {models.FUNCTION_REGISTERED, models.ENDPOINT_STARTED}


def test_history_filters_by_function_id(client, kurrent_appender):
    _seed_function_registered(kurrent_appender, function_id="add")
    _seed_function_registered(kurrent_appender, function_id="sub")

    response = client.get("/history", params={"function_id": "add"})
    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["meta"]["function_id"] == "add"


def test_function_history_route_scopes_by_path_param(client, kurrent_appender):
    _seed_function_registered(kurrent_appender, function_id="add")
    _seed_function_registered(kurrent_appender, function_id="sub")

    response = client.get("/functions/add/history")
    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["meta"]["function_id"] == "add"


def test_endpoint_history_route_scopes_by_path_param(client, kurrent_appender):
    _seed_endpoint_started(kurrent_appender, endpoint_id="n0")
    _seed_endpoint_started(kurrent_appender, endpoint_id="n1")

    response = client.get("/endpoints/n0/history")
    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["endpoint_id"] == "n0"


def test_active_object_history_route_returns_empty_no_source_yet(client, kurrent_appender):
    """No event in the taxonomy carries active_object_id today -- this
    documents the expected always-empty result until Active Object
    mesh-side instrumentation exists, rather than leaving it untested."""
    _seed_function_registered(kurrent_appender, function_id="add")

    response = client.get("/active-objects/anything/history")
    assert response.status_code == 200
    assert response.json() == []


def _seed_old(kurrent_appender, hours_ago=2):
    old_event = models.FunctionRegistered(
        endpoint_id="n0", function_id="old-fn", version=1,
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )
    kurrent_appender.append_to_stream(
        "functions-old-fn", models.FUNCTION_REGISTERED, old_event.model_dump(mode="json"),
    )


def test_since_minutes_narrows_the_window(client, kurrent_appender):
    _seed_old(kurrent_appender)
    _seed_function_registered(kurrent_appender, function_id="recent-fn")

    everything = {e["meta"]["function_id"] for e in client.get("/history").json()}
    last_hour = {e["meta"]["function_id"] for e in client.get("/history", params={"since_minutes": 60}).json()}

    assert everything == {"old-fn", "recent-fn"}
    assert last_hour == {"recent-fn"}


def test_records_older_than_retention_are_hidden_and_come_back_when_raised(collections, activity_repository, kurrent_appender):
    """Retention never deletes: lowering it hides older records, raising it
    shows them again."""
    retention_hours = [1]
    app = create_app(
        collections=collections, activity_repository=activity_repository,
        current_user_dependency=_fake_current_user,
        retention_cutoff=lambda: datetime.now(timezone.utc) - timedelta(hours=retention_hours[0]),
    )
    client = TestClient(app, headers=CP_HEADERS)
    _seed_old(kurrent_appender, hours_ago=2)

    assert client.get("/history").json() == []
    retention_hours[0] = 3
    assert {e["meta"]["function_id"] for e in client.get("/history").json()} == {"old-fn"}


def test_endpoint_readings_from_an_older_event_log_are_not_history(client, kurrent_appender, db):
    """A log written before readings stopped going to Kurrent replays
    cleanly: the endpoint's figures update, nothing lands in the history."""
    _seed_endpoint_started(kurrent_appender, endpoint_id="n0")
    for depth in (1, 2, 3):
        event = models.EndpointMetricsReported(endpoint_id="n0", metrics={"queue_depth": depth})
        kurrent_appender.append_to_stream("endpoints-n0", models.ENDPOINT_METRICS_REPORTED, event.model_dump(mode="json"))

    for path in ("/history", "/endpoints/n0/history"):
        assert [e["event_type"] for e in client.get(path).json()] == [models.ENDPOINT_STARTED]
    assert db["endpoints"].find_one({"_id": "n0"})["metrics"]["queue_depth"] == 3


def _seed_at(activity_repository, event_type, at, **fields):
    data = {"event_id": f"{event_type}-{at.timestamp()}-{fields.get('endpoint_id', '')}",
            "created_at": at.isoformat(), **fields}
    activity_repository.record(event_type, data)


def test_history_filters_by_area_on_the_server(client, activity_repository):
    now = datetime.now(timezone.utc)
    for i in range(5):
        _seed_at(activity_repository, models.JOB_COMPLETED, now - timedelta(seconds=i), job_id=f"j{i}")
    _seed_at(activity_repository, models.ENDPOINT_STARTED, now - timedelta(hours=1), endpoint_id="n0")
    _seed_at(activity_repository, "VirtualEnvironmentSettingsChanged", now - timedelta(hours=2))

    # The newest 2 rows are jobs, yet the endpoint event is still found.
    assert [e["event_type"] for e in client.get("/history", params={"areas": "endpoint", "limit": 2}).json()] \
        == [models.ENDPOINT_STARTED]
    both = client.get("/history", params={"areas": "endpoint,ve"}).json()
    assert [e["area"] for e in both] == ["endpoint", "ve"]
    assert client.get("/history", params={"areas": "nope"}).status_code == 422


def test_load_more_pages_through_the_history_without_gaps_or_repeats(client, activity_repository):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for i in range(7):   # pairs share a time: paging must still not lose one
        _seed_at(activity_repository, models.ENDPOINT_STARTED, now - timedelta(seconds=i // 2), endpoint_id=f"n{i}")

    seen, page, params = [], None, {"limit": 3}
    while page is None or len(page) == 3:
        page = client.get("/history", params=params).json()
        seen += [e["event_id"] for e in page]
        if page:
            params = {"limit": 3, "before": page[-1]["created_at"], "before_id": page[-1]["event_id"]}
    assert len(seen) == len(set(seen)) == 7

    endpoint_page = client.get("/endpoints/n0/history", params={"before": now.isoformat(), "limit": 5}).json()
    assert endpoint_page == []   # n0 is the newest row, not older than itself
    assert client.get("/history", params={"limit": 0}).status_code == 422
    assert client.get("/history", params={"before": "yesterday"}).status_code == 422


def test_choreography_history_route_scopes_by_path_param(client, kurrent_appender):
    for choreography_id in ("ch-a", "ch-b"):
        event = models.ChoreographyRunStarted(choreography_id=choreography_id, run_id=f"run-{choreography_id}",
                                              choreography_name=choreography_id, version=1,
                                              started_by_type="user")
        kurrent_appender.append_to_stream(f"choreography-runs-{choreography_id}", models.CHOREOGRAPHY_RUN_STARTED,
                                          event.model_dump(mode="json"))

    docs = client.get("/choreographies/ch-a/history").json()
    assert [d["meta"]["choreography_id"] for d in docs] == ["ch-a"]
