import pytest

from axo_vem.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from axo_vem.domain.events.models import ChoreographySettings, ChoreographyTrigger, ParamMerge

from tests.application.choreography.harness import Harness, bucket, fn, graph, obj


@pytest.fixture
def h():
    harness = Harness()
    harness.function("a", params=[{"name": "rows", "type": "json"}])
    harness.function("b")
    yield harness
    harness.close()


def test_create_then_every_edit_is_a_new_version(h):
    created = h.service.create(user_id="u1", name="etl", graph=graph([fn("a")]))
    assert created["version"] == 1 and created["existing"] is False
    same = h.service.update(user_id="u2", ref="etl", graph=graph([fn("a")]))
    assert same["existing"] is True and same["version"] == 1
    v2 = h.service.update(user_id="u2", ref="etl", graph=graph([fn("a"), fn("b")], ("a", "b")))
    assert v2["version"] == 2 and v2["updated_by"] == "u2"
    assert [v["version"] for v in h.service.versions("etl")] == [2, 1]
    assert len(h.service.get_version("etl", 1).graph.nodes) == 1
    assert h.service.detail("etl", version=1)["latest_version"] == 2
    with pytest.raises(NotFoundError) as err:
        h.service.get_version("etl", 9)
    assert err.value.error_name == "CHOREOGRAPHY_VERSION_NOT_FOUND"
    assert h.publisher.types() == ["ChoreographyCreated", "ChoreographyUpdated"]


def test_names_are_unique_in_the_ve_and_follow_renames(h):
    h.service.create(user_id="u1", name="one", graph=graph([fn("a")]))
    with pytest.raises(ConflictError) as err:
        h.service.create(user_id="u1", name="one", graph=graph([fn("a")]))
    assert err.value.error_name == "CHOREOGRAPHY_EXISTS"
    h.service.update(user_id="u1", ref="one", name="uno", graph=graph([fn("a")]))
    assert h.service.get("uno").version == 2
    h.service.create(user_id="u1", name="one", graph=graph([fn("b")]))       # the old name is free again
    with pytest.raises(InvalidRequestError):
        h.service.create(user_id="u1", name="bad name!", graph=graph([fn("a")]))


def test_save_creates_or_adds_a_version_by_name(h):
    assert h.service.save(user_id="u1", name="s", graph=graph([fn("a")]))["version"] == 1
    assert h.service.save(user_id="u1", name="s", graph=graph([fn("b")]))["version"] == 2
    assert h.service.get(h.service.get("s").choreography_id).name == "s"       # by id too


def test_steps_must_name_things_that_exist(h):
    with pytest.raises(InvalidRequestError) as err:
        h.service.create(user_id="u1", name="x", graph=graph([fn("nope"), obj("o", "counter-9")]))
    assert err.value.error_name == "CHOREOGRAPHY_INVALID"
    assert "no function nope" in str(err.value) and "no object counter-9" in str(err.value)
    h.object("counter-9", {"add": (lambda p, e: 1, ["n"], [])})
    check = h.service.check(graph([obj("o", "counter-9", "sub")]))
    assert check["ok"] is False and "has no method sub" in check["problems"][0]


def test_warnings(h):
    h.object("c", {"run": (lambda p, e: 1, [], [])})
    g = graph([fn("a"), fn("b"), fn("j", function_id="a", merge={"rows": ParamMerge(strategy="random")}),
               obj("o1", "c"), obj("o2", "c")], ("a", "j"), ("b", "j"))
    warnings = h.service.check(g)["warnings"]
    assert any("o1, o2 all call c" in w for w in warnings)
    assert any("step j: rows can come from a different step in each run" in w for w in warnings)
    assert any("step a starts the run and needs rows" in w for w in warnings)


def test_steps_show_each_parameter_for_the_node_dialog(h):
    h.function("c", params=[{"name": "rows", "type": "json"}, {"name": "limit", "type": "json", "required": False,
                                                                "default": 10}, {"name": "day", "type": "json"}])
    g = graph([fn("a"), fn("b"), fn("j", function_id="c", params={"day": "mon"},
                                    merge={"limit": ParamMerge(strategy="reduce", reducer="max")}),
               fn("k", function_id="b", merge={"*": ParamMerge(strategy="collect")})],
              ("a", "j"), ("b", "j"), ("a", "k"), ("b", "k"))
    steps = h.service.check(g)["steps"]
    assert steps["j"]["senders"] == ["a", "b"]
    assert steps["j"]["params"] == [
        {"name": "rows", "required": True, "merge": ParamMerge().model_dump()},           # priority: the default
        {"name": "limit", "required": False, "default": 10,
         "merge": ParamMerge(strategy="reduce", reducer="max").model_dump()},
        {"name": "day", "required": True, "value": "mon"}]
    assert steps["k"]["params"] is None                                                 # b has no schema
    assert steps["k"]["merge_all"]["strategy"] == "collect"
    assert steps["a"] == {"senders": [], "files_from": None, "exists": True,
                          "params": [{"name": "rows", "required": True}], "merge_all": None}
    saved = h.service.save(user_id="u1", name="fan", graph=g)
    assert saved["steps"] == steps and "warnings" in saved


def test_designer_describes_every_setting(h):
    from axo_vem.domain.choreography.designer import INTERNAL_FIELDS
    from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyNode

    d = h.service.designer()
    described = {row["name"] for row in d["step_settings"]}
    assert described == set(ChoreographyNode.model_fields) - INTERNAL_FIELDS
    assert {row["name"] for row in d["arrow_settings"]} == set(ChoreographyEdge.model_fields) - INTERNAL_FIELDS
    assert {row["name"] for row in d["trigger_settings"]} == set(ChoreographyTrigger.model_fields) - INTERNAL_FIELDS
    assert d["event_types"][0]["type"] == "file.written"
    assert {row["name"] for row in d["choreography_settings"]} == set(ChoreographySettings.model_fields)
    rows = {row["name"]: row for row in d["step_settings"]}
    assert rows["join"]["choices"] == ["all", "arrived"] and rows["join"]["default"] == "all"
    assert rows["max_attempts"] == {**rows["max_attempts"], "default": None, "min": 1, "max": 10}
    assert rows["retry_backoff"]["choices"] == ["fixed", "exponential"]
    assert rows["files_mode"]["choices"] == ["per_file", "all_at_once"]
    assert all(row["description"] for row in d["step_settings"] + d["arrow_settings"])
    assert d["merge_strategies"][0]["name"] == "priority"
    assert {o["op"] for o in d["condition_operators"]} >= {"==", ">", "in", "exists", "is_empty"}


def test_triggers(h):
    h.service.create(user_id="u1", name="t", graph=graph([bucket("in", "alice/raw"), fn("b")], ("in", "b")))
    trigger = h.service.add_trigger(user_id="u3", ref="t", trigger=ChoreographyTrigger(bucket="alice/raw", prefix="in/"))
    assert trigger["created_by"] == "u3" and trigger["version"] == 2
    [active] = h.service.get("t").triggers
    assert active["trigger_id"] == trigger["trigger_id"] and active["prefix"] == "in/"
    assert [c.name for c in h.repository.triggers_for_bucket("alice/raw")] == ["t"]
    # triggers are saved with each version: an edit by someone else keeps who added it
    kept = h.service.get("t").graph
    h.service.update(user_id="u1", ref="t", graph=graph([bucket("in", "alice/raw"), fn("a")], ("in", "a"))
                     .model_copy(update={"triggers": [kept.triggers[0].model_copy(update={"created_by": "u1"})]}))
    assert h.service.get("t").triggers[0]["created_by"] == "u3"
    assert h.service.get_version("t", 1).triggers == []                  # older versions show their own
    h.service.remove_trigger(user_id="u1", ref="t", trigger_id=trigger["trigger_id"])
    with pytest.raises(NotFoundError):
        h.service.remove_trigger(user_id="u1", ref="t", trigger_id=trigger["trigger_id"])


def test_delete_frees_the_name(h):
    h.service.create(user_id="u1", name="gone", graph=graph([fn("a")]),
                     settings=ChoreographySettings(max_parallel_runs=2))
    h.service.delete(user_id="u1", ref="gone")
    with pytest.raises(NotFoundError):
        h.service.get("gone")
    h.service.create(user_id="u1", name="gone", graph=graph([fn("a")]))


def test_a_change_to_the_watched_buckets_is_told_at_once(h):
    told = []
    h.service.on_triggers_changed = lambda: told.append(sorted(b["bucket"] for b in h.repository.trigger_buckets()))
    h.service.create(user_id="u1", name="t", graph=graph([bucket("in", "alice/raw"), fn("b")], ("in", "b")))
    assert told == []                                                   # no trigger yet: nothing to tell
    trigger = h.service.add_trigger(user_id="u3", ref="t", trigger=ChoreographyTrigger(bucket="alice/raw"))
    h.service.remove_trigger(user_id="u1", ref="t", trigger_id=trigger["trigger_id"])
    h.service.add_trigger(user_id="u3", ref="t", trigger=ChoreographyTrigger(bucket="alice/raw"))
    h.service.delete(user_id="u1", ref="t")
    assert told == [["alice/raw"], [], ["alice/raw"], []]
    h.service.on_triggers_changed = lambda: 1 / 0                      # a failing heartbeat never fails the change
    h.service.create(user_id="u1", name="u", graph=graph([fn("a")]))
    assert h.service.add_trigger(user_id="u1", ref="u", trigger=ChoreographyTrigger(bucket="alice/raw"))["bucket"] == "alice/raw"
