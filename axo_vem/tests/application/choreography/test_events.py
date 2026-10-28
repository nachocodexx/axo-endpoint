"""Events start choreographies: people's own types and file.written."""

import pytest

from axo_vem.application.choreography.events import EventsService
from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from axo_vem.domain.events.models import ChoreographyTrigger, ConditionRule, EdgeCondition
from axo_vem.domain.visibility import Viewer

from tests.application.choreography.harness import Harness, bucket, fn, graph

# made-up example: a shop sends "order.paid" when someone pays; ana's
# choreography "ship" starts on it (only for orders over 100)
ANA = {"user_id": "ana", "started_by_type": "user", "started_by_id": "ana", "started_by_name": "Ana"}


@pytest.fixture
def h():
    harness = Harness()
    yield harness
    harness.close()


@pytest.fixture
def events(h):
    return EventsService(h.db["events"], h.db["event_types"], h.repository, h.engine.on_event, "ve1")


def _ship(h, user="ana"):
    h.function("pack", lambda p, e: {"packed": p["order_id"], "by": e["trigger"]["event"]["type"]})
    big = EdgeCondition(rules=[ConditionRule(field="total", op=">", value=100)])
    return h.service.create(user_id=user, name="ship", graph=graph([fn("pack")]).model_copy(update={
        "triggers": [ChoreographyTrigger(event_type="order.paid", condition=big, targets=["pack"])]}))


def test_an_event_starts_the_matching_choreographies_once(h, events):
    _ship(h)
    small = events.emit(sender=ANA, event_type="order.paid", data={"order_id": "o-1", "total": 20})
    assert small["runs"] == [] and small["duplicate"] is False         # the condition didn't match
    sent = events.emit(sender=ANA, event_type="order.paid", data={"order_id": "o-2", "total": 250}, event_id="pay-2")
    [started] = sent["runs"]
    run = h.wait(started["run_id"])
    assert run["status"] == "completed" and run["acting_user_id"] == "ana" and run["started_by_type"] == "trigger"
    # the event's data fills the first step's parameters by name; the event itself is ctx.event
    assert run["result"]["pack"] == {"packed": "o-2", "by": "order.paid"}
    again = events.emit(sender=ANA, event_type="order.paid", data={"order_id": "o-2", "total": 250},
                        event_id="pay-2")
    assert again["duplicate"] is True and len(h.runs.list(run["choreography_id"])) == 1


def test_event_types_are_found_described_and_kept_while_used(h, events):
    _ship(h)
    events.emit(sender=ANA, event_type="order.paid", data={"order_id": "o-1", "total": 5})
    found = {t["type"]: t for t in events.types()}
    assert found["file.written"]["built_in"] is True
    paid = found["order.paid"]
    assert paid["found"] is True and paid["count"] == 1 and [u["choreography"] for u in paid["used_by"]] == ["ship"]
    assert {f["name"]: f["type"] for f in paid["fields"]} == {"order_id": "text", "total": "number"}
    described = events.save_type(user_id="ana", event_type="order.paid", description="Someone paid an order",
                                 fields=[{"name": "total", "type": "number", "description": "In cents"}])
    assert described["description"] == "Someone paid an order" and described["found"] is False
    with pytest.raises(ConflictError):
        events.delete_type(event_type="order.paid")                     # "ship" starts on it
    events.save_type(user_id="ana", event_type="user.created")
    assert events.delete_type(event_type="user.created")["deleted"] is True
    with pytest.raises(InvalidRequestError):
        events.emit(sender=ANA, event_type="file.written", data={})      # the platform's own
    with pytest.raises(InvalidRequestError):
        events.save_type(user_id="ana", event_type="Not A Type")


def test_each_person_sees_their_events_and_those_that_started_their_triggers(h, events):
    _ship(h, user="ana")
    ben = {"user_id": "ben", "started_by_type": "user", "started_by_id": "ben"}
    events.emit(sender=ben, event_type="order.paid", data={"order_id": "o-9", "total": 500}, event_id="b-1")
    events.emit(sender=ben, event_type="other.thing", data={}, event_id="b-2")
    assert [e["id"] for e in events.list(Viewer("ana", "developer"))["events"]] == ["b-1"]
    assert sorted(e["id"] for e in events.list(Viewer("ben", "developer"))["events"]) == ["b-1", "b-2"]
    assert len(events.list(Viewer("carla", "admin"))["events"]) == 2
    with pytest.raises(NotFoundError):
        events.get(Viewer("ana", "viewer"), "b-2")


def test_triggers_are_checked_when_saved(h):
    h.function("pack", lambda p, e: "ok")
    for trigger, problem in (
        (ChoreographyTrigger(event_type="file.done"), "platform's"),
        (ChoreographyTrigger(event_type="file.written"), "needs a bucket"),
        (ChoreographyTrigger(event_type="order.paid", bucket="alice/raw"), "only 'file written'"),
        (ChoreographyTrigger(event_type="order.paid", targets=["nope"]), "no step nope"),
    ):
        with pytest.raises(InvalidRequestError, match=problem):
            h.service.create(user_id="u1", name="bad", graph=graph([fn("pack")]).model_copy(
                update={"triggers": [trigger]}))


def test_old_bucket_triggers_move_into_a_new_version(h):
    h.buckets.objects["alice/raw"] = []
    h.function("copy", lambda p, e: "ok")
    h.service.create(user_id="u1", name="watch", graph=graph([bucket("raw", "alice/raw"), fn("copy")], ("raw", "copy")))
    chor = h.service.get("watch")
    h.repository.set_triggers(chor.choreography_id, [{"trigger_id": "old1", "kind": "bucket_object_written",
                                                     "bucket": "alice/raw", "prefix": "in/", "version": 1,
                                                     "created_by": "u2", "created_at": "2026-01-01T00:00:00"}])
    assert h.service.get("watch").triggers[0]["trigger_id"] == "old1"   # still works before moving
    assert h.service.migrate_legacy_triggers() == 1
    assert h.service.migrate_legacy_triggers() == 0                      # once
    moved = h.service.get("watch")
    assert moved.version == 2 and moved.legacy_triggers == []
    [trigger] = moved.graph.triggers
    assert (trigger.trigger_id, trigger.created_by, trigger.prefix, trigger.targets) == ("old1", "u2", "in/", ["raw"])
    [started] = h.engine.on_bucket_written("alice/raw", "in/a.csv", 1)
    assert h.wait(started["run_id"])["acting_user_id"] == "u2"
