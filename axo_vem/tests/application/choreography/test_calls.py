"""A choreography step: a call that starts a linked run of another choreography."""

import pytest

from axo_vem.application.choreography.engine import call_run_id
from axo_vem.domain.errors import ConflictError, InvalidRequestError
from axo_vem.domain.events.models import ChoreographyNode, ChoreographySettings

from tests.application.choreography.fakes import StepFailure
from tests.application.choreography.harness import Harness, bucket, fn, graph

ROWS = [{"name": "rows", "type": "json", "required": True}]


@pytest.fixture
def h():
    harness = Harness()
    yield harness
    harness.close()


def call(node_id: str, h: Harness, name: str, **kw) -> ChoreographyNode:
    return ChoreographyNode(node_id=node_id, kind="choreography",
                            choreography_id=h.service.get(name).choreography_id, choreography_name=name, **kw)


def test_a_call_is_a_step_with_the_inner_inputs_and_its_answer(h):
    h.function("clean", lambda p, e: {"rows": p["rows"] * 2}, params=ROWS)
    h.function("count", lambda p, e: {"rows": 3})
    h.function("report", lambda p, e: {"report": p}, params=ROWS)
    h.service.create(user_id="u1", name="clean-visits", graph=graph([fn("clean")]))
    # the inner choreography takes what its first step needs: rows
    inputs = h.catalog.param_info(call("c", h, "clean-visits"))
    assert inputs is not None and inputs.names == ["rows"] and inputs.required == {"rows"}

    h.service.create(user_id="u1", name="daily", graph=graph(
        [fn("count"), call("clean", h, "clean-visits"), fn("report")], ("count", "clean"), ("clean", "report")))
    run = h.wait(h.engine.start("daily", user_id="u1", started_by_name="Ana")["run_id"])

    assert run["status"] == "completed"
    assert run["result"] == {"report": {"report": {"rows": 6}}}
    card = run["steps"]["clean"]
    assert card["params"] == {"rows": 3} and card["param_origins"] == {"rows": "count"}
    assert card["target_version"] == 1 and card["child"]["done"] == card["child"]["total"] == 1
    inner = h.engine.run_info(card["child"]["run_id"])
    assert inner["choreography_name"] == "clean-visits" and inner["input"] == {"rows": 3}
    assert inner["started_by_type"] == "choreography" and inner["started_by_id"] == run["run_id"]
    assert inner["acting_user_id"] == "u1" and inner["started_by_name"] == "Ana"
    assert inner["parent"]["step"] == "clean" and inner["chain"] == [run["choreography_id"]]
    # its jobs know the run that called it (the jobs list nests them, visibility follows)
    inner_job = next(c for c in h.jobs.calls if c["target"] == "clean")
    assert inner_job["choreography"]["parent"]["run_id"] == run["run_id"]
    assert inner_job["choreography"]["chain"] == [run["choreography_id"]]
    assert [r.run_id for r in h.runs.list(parent_run_id=run["run_id"])] == [inner["run_id"]]


def test_several_last_steps_answer_by_step_unless_one_is_picked(h):
    h.function("a", lambda p, e: {"x": 1})
    h.function("b", lambda p, e: {"y": 2})
    h.function("use", lambda p, e: p)
    h.service.create(user_id="u1", name="two-ends", graph=graph([fn("a"), fn("b")]))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "two-ends"), fn("use")], ("c", "use")))
    assert any("no \"Its answer\" picked" in w for w in h.service.detail("outer")["warnings"])
    run = h.wait(h.engine.start("outer", user_id="u1")["run_id"])
    assert run["result"]["use"] == {"a": {"x": 1}, "b": {"y": 2}}

    with pytest.raises(InvalidRequestError):
        h.service.update(user_id="u1", ref="two-ends", graph=h.service.get("two-ends").graph,
                         settings=ChoreographySettings(answer_step="nope"))
    h.service.update(user_id="u1", ref="two-ends", graph=h.service.get("two-ends").graph,
                     settings=ChoreographySettings(answer_step="b"))
    run = h.wait(h.engine.start("outer", user_id="u1")["run_id"])
    assert run["result"]["use"] == {"y": 2}
    assert not any("Its answer" in w for w in h.service.detail("outer")["warnings"])


def test_a_failed_inner_run_fails_the_step_and_a_resume_resumes_that_same_run(h):
    state = {"fail": True}
    h.function("first", lambda p, e: {"v": 1})
    h.function("second", lambda p, e: (_ for _ in ()).throw(StepFailure("boom")) if state["fail"] else p)
    h.service.create(user_id="u1", name="inner", graph=graph(
        [fn("first"), fn("second", max_attempts=1)], ("first", "second")))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "inner")]))
    run_id = h.engine.start("outer", user_id="u1")["run_id"]
    run = h.wait(run_id)
    card = run["steps"]["c"]
    assert run["status"] == "failed" and card["error_name"] == "CHOREOGRAPHY_CALL_FAILED"
    assert "step second" in card["error"] and "boom" in card["error"]
    child_id = card["child"]["run_id"]
    assert h.engine.run_info(child_id)["status"] == "failed"

    state["fail"] = False
    run = h.wait(h.engine.resume(run_id)["run_id"])
    assert run["status"] == "completed" and run["steps"]["c"]["child"]["run_id"] == child_id
    resumed = h.engine.run_info(child_id)
    assert resumed["status"] == "completed" and resumed["resumes"] == 1
    assert h.jobs.targets().count("first") == 1                 # only its failed part ran again


def test_an_inner_run_with_errors_completes_the_step_with_a_warning(h):
    h.function("ok", lambda p, e: {"fine": True})
    h.function("bad", lambda p, e: (_ for _ in ()).throw(StepFailure("nope")))
    h.service.create(user_id="u1", name="inner", graph=graph([fn("ok"), fn("bad", max_attempts=1)]),
                     settings=ChoreographySettings(answer_step="ok"))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "inner")]))
    run = h.wait(h.engine.start("outer", user_id="u1")["run_id"])
    assert run["status"] == "completed" and run["result"]["c"] == {"fine": True}
    assert "completed with errors" in run["steps"]["c"]["warnings"][0]


def test_cancelling_the_outer_run_cancels_the_inner_one(h):
    h.jobs.hold("long")
    h.function("long", lambda p, e: "x")
    h.service.create(user_id="u1", name="inner", graph=graph([fn("long")]))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "inner")]))
    run_id = h.engine.start("outer", user_id="u1")["run_id"]
    run = h.wait(run_id, until=lambda r: (r["steps"]["c"]["child"] or {}).get("status") == "running")
    h.engine.cancel(run_id)
    assert h.wait(run_id)["status"] == "cancelled"
    assert h.wait(run["steps"]["c"]["child"]["run_id"])["status"] == "cancelled"


def test_an_inner_run_cancelled_on_its_own_fails_the_step(h):
    h.jobs.hold("long")
    h.function("long", lambda p, e: "x")
    h.service.create(user_id="u1", name="inner", graph=graph([fn("long")]))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "inner")]))
    run_id = h.engine.start("outer", user_id="u1")["run_id"]
    run = h.wait(run_id, until=lambda r: (r["steps"]["c"]["child"] or {}).get("status") == "running")
    h.engine.cancel(run["steps"]["c"]["child"]["run_id"])
    run = h.wait(run_id)
    assert run["status"] == "failed" and run["steps"]["c"]["error_name"] == "CANCELLED"


def test_per_file_calls_start_one_inner_run_per_file_with_it_as_its_starting_file(h):
    for name in ("a.csv", "b.csv", "c.csv"):
        h.buckets.put("alice/in", name)
    h.function("each", lambda p, e: (_ for _ in ()).throw(StepFailure("bad"))
               if e["file"]["name"] == "b.csv" else e["file"]["name"])
    h.service.create(user_id="u1", name="per-file", graph=graph(
        [bucket("start", "alice/other"), fn("each", max_attempts=1)], ("start", "each")))
    h.service.create(user_id="u1", name="outer", graph=graph(
        [bucket("in", "alice/in"), call("c", h, "per-file", parallelism=2)], ("in", "c")))
    run = h.wait(h.engine.start("outer", user_id="u1")["run_id"])
    card = run["steps"]["c"]
    assert run["status"] == "completed_with_errors" and card["status"] == "completed_with_errors"
    assert [it["status"] for it in card["items"]] == ["completed", "failed", "completed"]
    assert run["result"]["c"] == ["a.csv", "c.csv"]
    inner = h.engine.run_info(card["items"][0]["child"]["run_id"])
    assert inner["files"]["start"][0]["name"] == "a.csv" and inner["parent"]["item"] == 0


def test_all_at_once_starts_one_inner_run_with_every_file(h):
    for name in ("a.csv", "b.csv"):
        h.buckets.put("alice/in", name)
    h.function("join", lambda p, e: [f["name"] for f in e["files"]], params=[{"name": "data", "type": "json"}])
    h.service.create(user_id="u1", name="joiner", graph=graph(
        [bucket("start", "alice/x"), fn("join", files_mode="all_at_once")], ("start", "join")))
    h.service.create(user_id="u1", name="outer", graph=graph(
        [bucket("in", "alice/in"), call("c", h, "joiner", files_mode="all_at_once")], ("in", "c")))
    run = h.wait(h.engine.start("outer", user_id="u1")["run_id"])
    assert run["result"]["c"] == ["a.csv", "b.csv"] and not run["steps"]["c"]["items"]


def test_loops_depth_and_deleting_a_called_choreography_are_refused(h):
    h.function("f", lambda p, e: 1)
    h.service.create(user_id="u1", name="b", graph=graph([fn("f")]))
    h.service.create(user_id="u1", name="a", graph=graph([call("cb", h, "b")]))
    with pytest.raises(InvalidRequestError) as caught:
        h.service.update(user_id="u1", ref="b", graph=graph([fn("f"), call("ca", h, "a")]))
    assert caught.value.error_name == "CHOREOGRAPHY_LOOP" and "b → a → b" in str(caught.value)
    with pytest.raises(InvalidRequestError) as caught:
        h.service.update(user_id="u1", ref="b", graph=graph([call("self", h, "b")]))
    assert caught.value.error_name == "CHOREOGRAPHY_LOOP"
    assert h.service.detail("b")["used_by"][0]["name"] == "a"
    assert h.service.detail("a")["uses"][0]["name"] == "b"

    with pytest.raises(ConflictError) as refused:
        h.service.delete(user_id="u1", ref="b")
    assert refused.value.error_name == "CHOREOGRAPHY_IN_USE"

    # five levels at most: l1 -> l2 -> l3 -> l4 -> l5 is fine, a sixth is too deep
    h.service.create(user_id="u1", name="l5", graph=graph([fn("f")]))
    for level in (4, 3, 2, 1):
        h.service.create(user_id="u1", name=f"l{level}", graph=graph([call("down", h, f"l{level + 1}")]))
    with pytest.raises(InvalidRequestError) as caught:
        h.service.create(user_id="u1", name="l0", graph=graph([call("down", h, "l1")]))
    assert caught.value.error_name == "CHOREOGRAPHY_TOO_DEEP"
    assert h.wait(h.engine.start("l1", user_id="u1")["run_id"])["status"] == "completed"


def test_a_busy_inner_choreography_makes_the_step_wait_then_fail_after_its_max_wait(h):
    gate = h.jobs.hold("slow")
    h.function("slow", lambda p, e: "x")
    h.service.create(user_id="u1", name="inner", graph=graph([fn("slow")]),
                     settings=ChoreographySettings(max_parallel_runs=1))
    busy = h.engine.start("inner", user_id="u1")["run_id"]
    h.service.create(user_id="u1", name="patient", graph=graph([call("c", h, "inner")]))
    h.service.create(user_id="u1", name="hasty", graph=graph([call("c", h, "inner", max_wait=0.05)]))

    patient = h.engine.start("patient", user_id="u1")["run_id"]
    h.wait(patient, until=lambda r: (r["steps"]["c"]["child"] or {}).get("position") == 1)
    hasty = h.wait(h.engine.start("hasty", user_id="u1")["run_id"])
    assert hasty["steps"]["c"]["error_name"] == "CHOREOGRAPHY_TOO_BUSY" and "in line" in hasty["steps"]["c"]["error"]
    gate.set()
    assert h.wait(busy)["status"] == "completed"
    assert h.wait(patient)["status"] == "completed"


def test_versions_are_fixed_when_the_run_is_queued(h):
    gate = h.jobs.hold("block")
    h.function("block", lambda p, e: "x")
    h.function("f", lambda p, e: {"version": 1})
    h.service.create(user_id="u1", name="inner", graph=graph([fn("f")]))
    h.service.create(user_id="u1", name="outer", graph=graph([fn("block"), call("c", h, "inner"), fn("f2", "f")],
                                                             ("block", "c"), ("c", "f2")))
    run_id = h.engine.start("outer", user_id="u1")["run_id"]
    # new versions arrive while the run goes: it keeps the ones it started with
    h.function("f", params=None, version=2)
    h.service.update(user_id="u1", ref="inner", graph=graph([fn("f"), fn("g", "f")]))
    gate.set()
    run = h.wait(run_id)
    assert run["versions"] == {"block": 1, "c": 1, "f2": 1}
    assert run["steps"]["c"]["target_version"] == 1 and run["steps"]["f2"]["target_version"] == 1
    assert h.engine.run_info(run["steps"]["c"]["child"]["run_id"])["version"] == 1


def test_a_restarted_vem_links_the_step_to_its_inner_run_again(h):
    gate = h.jobs.hold("a")
    h.function("a", lambda p, e: {"n": 1})
    h.service.create(user_id="u1", name="inner", graph=graph([fn("a")]))
    h.service.create(user_id="u1", name="outer", graph=graph([call("c", h, "inner")]))
    run_id = h.engine.start("outer", user_id="u1")["run_id"]
    h.wait(run_id, until=lambda r: (r["steps"]["c"]["child"] or {}).get("status") == "running")
    h.engine.shutdown()

    h.engine = h.new_engine()
    assert h.engine.recover() == 2                   # the outer run and the one it called
    gate.set()
    run = h.wait(run_id)
    assert run["status"] == "completed" and run["result"]["c"] == {"n": 1}
    assert run["steps"]["c"]["child"]["run_id"] == call_run_id(run_id, "c", None)
    assert h.jobs.targets().count("a") == 1 and len(h.runs.list(parent_run_id=run_id)) == 1
