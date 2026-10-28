import time

import pytest

from axo_vem.domain.errors import ConflictError
from axo_vem.domain.events.models import ChoreographyTrigger

from axo_shared.metrics import names as m

from axo_vem.application.choreography.engine import job_id_for
from axo_vem.domain.choreography.inputs import DATA_REF_KEY
from axo_vem.domain.errors import TooManyRequestsError
from axo_vem.domain.events.models import ChoreographySettings, ParamMerge

from tests.application.choreography.fakes import StepFailure
from tests.application.choreography.harness import Harness, bucket, fn, graph, obj

SCHEMA_ROWS = [{"name": "rows", "type": "json", "required": True}, {"name": "limit", "type": "json",
                                                                    "required": False, "default": 10}]


@pytest.fixture
def h():
    harness = Harness()
    yield harness
    harness.close()


def test_a_chain_passes_the_event_and_fills_parameters_from_the_previous_output(h):
    h.function("count", lambda p, e: {"rows": 120, "path": "x.csv"})
    h.function("report", lambda p, e: {"got": p, "path": e["previous"]["count"]["output"]["path"]},
               params=SCHEMA_ROWS)
    h.service.create(user_id="u1", name="etl", graph=graph([fn("count"), fn("report")], ("count", "report")))
    run = h.wait(h.engine.start("etl", user_id="u1", input={"day": "mon"})["run_id"])

    assert run["status"] == "completed"
    assert run["result"] == {"report": {"got": {"rows": 120}, "path": "x.csv"}}
    report_call = h.jobs.calls[1]
    assert report_call["event"]["input"] == {"day": "mon"}
    assert report_call["event"]["run"]["choreography"] == "etl"
    assert report_call["acting_user_id"] == "u1" and report_call["started_by_id"] == run["choreography_id"]
    assert run["steps"]["report"]["target_version"] == 1
    # The run and step go on the job's billing record (step costs).
    assert report_call["step_ref"] == {"choreography_run_id": run["run_id"], "choreography_step": "report",
                                       "choreography_version": 1}
    assert h.publisher.types()[-2:] == ["ChoreographyRunStarted", "ChoreographyRunFinished"]


def test_one_output_goes_to_every_next_step_and_a_failed_branch_doesnt_stop_the_others(h):
    h.function("split", lambda p, e: {"n": 1})
    h.function("bad", lambda p, e: (_ for _ in ()).throw(StepFailure("boom")))
    h.function("good", lambda p, e: "ok")
    h.function("after_bad", lambda p, e: "never")
    h.service.create(user_id="u1", name="fan", graph=graph(
        [fn("split"), fn("bad", max_attempts=2), fn("good"), fn("after_bad")],
        ("split", "bad"), ("split", "good"), ("bad", "after_bad")))
    run = h.wait(h.engine.start("fan", user_id="u1")["run_id"])

    assert run["status"] == "completed_with_errors"
    assert run["steps"]["bad"]["status"] == "failed" and run["steps"]["bad"]["attempt"] == 1
    assert run["steps"]["good"]["status"] == "completed"
    assert run["steps"]["after_bad"]["status"] == "skipped"
    assert h.jobs.targets().count("bad") == 1          # one job ...
    assert h.jobs.tries[run["steps"]["bad"]["job_id"]] == 2   # ... tried twice
    assert "after_bad" not in h.jobs.targets()


def test_a_step_starts_as_soon_as_its_own_inputs_are_done(h):
    gate = h.jobs.hold("slow")
    h.function("slow", lambda p, e: 1)
    h.function("fast", lambda p, e: 2)
    h.function("after_fast", lambda p, e: 3)
    h.service.create(user_id="u1", name="dataflow", graph=graph(
        [fn("slow"), fn("fast"), fn("after_fast")], ("fast", "after_fast")))
    run_id = h.engine.start("dataflow", user_id="u1")["run_id"]
    h.wait(run_id, until=lambda r: r["steps"]["after_fast"]["status"] == "completed")
    assert h.engine.run_info(run_id)["steps"]["slow"]["status"] == "running"
    gate.set()
    assert h.wait(run_id)["status"] == "completed"


def test_join_arrived_runs_with_what_came(h):
    h.function("a", lambda p, e: {"x": 1})
    h.function("b", lambda p, e: (_ for _ in ()).throw(StepFailure("no")))
    h.function("j", lambda p, e: sorted(e["previous"]))
    h.service.create(user_id="u1", name="join", graph=graph(
        [fn("a"), fn("b", max_attempts=1), fn("j", join="arrived")], ("a", "j"), ("b", "j")))
    run = h.wait(h.engine.start("join", user_id="u1")["run_id"])
    assert run["steps"]["j"]["status"] == "completed" and run["result"]["j"] == ["a"]


def test_fan_in_picks_by_priority_and_records_where_params_came_from(h):
    h.function("a", lambda p, e: {"rows": 1})
    h.function("b", lambda p, e: {"rows": 2})
    h.function("needs", lambda p, e: p, params=SCHEMA_ROWS)
    h.service.create(user_id="u1", name="amb", graph=graph(
        [fn("a"), fn("b"), fn("needs")], ("a", "needs"), ("b", "needs")))
    run = h.wait(h.engine.start("amb", user_id="u1")["run_id"])
    assert run["result"]["needs"] == {"rows": 1}
    assert run["steps"]["needs"]["param_origins"] == {"rows": "a"}

    h.service.update(user_id="u1", ref="amb", graph=graph(
        [fn("a"), fn("b"), fn("needs", merge={"rows": ParamMerge(strategy="from", source="b")})],
        ("a", "needs"), ("b", "needs")))
    run = h.wait(h.engine.start("amb", user_id="u1")["run_id"])
    assert run["version"] == 2 and run["result"]["needs"] == {"rows": 2}

    h.service.update(user_id="u1", ref="amb", graph=graph(
        [fn("a"), fn("b"), fn("needs", merge={"rows": ParamMerge(strategy="reduce", reducer="all_equal")})],
        ("a", "needs"), ("b", "needs")))
    run = h.wait(h.engine.start("amb", user_id="u1")["run_id"])
    assert run["steps"]["needs"]["error_name"] == "CHOREOGRAPHY_STEP_PARAM_AMBIGUOUS"
    assert sum(t == "needs" for t in h.jobs.targets()) == 2      # not called the third time, no retries

    h.service.create(user_id="u1", name="missing", graph=graph([fn("needs")]))
    run = h.wait(h.engine.start("missing", user_id="u1")["run_id"])
    assert run["steps"]["needs"]["error_name"] == "CHOREOGRAPHY_STEP_PARAM_MISSING"
    assert run["status"] == "failed"


def test_object_steps_call_the_method_with_the_event(h):
    h.function("make", lambda p, e: {"n": 5})
    h.object("counter-1", {"add": (lambda p, e: {"total": p["n"] + 1, "step": e["step"]}, ["n"], [])})
    h.service.create(user_id="u1", name="objs", graph=graph(
        [fn("make"), obj("inc", "counter-1", "add")], ("make", "inc")))
    run = h.wait(h.engine.start("objs", user_id="u1")["run_id"])
    assert run["result"]["inc"] == {"total": 6, "step": "inc"}
    assert h.jobs.calls[-1]["target"] == "counter-1.add"


def test_an_object_that_disappears_fails_its_step_at_once(h):
    h.object("gone", {"run": (lambda p, e: 1, [], [])})
    h.service.create(user_id="u1", name="o", graph=graph([obj("s", "gone")]))
    del h.objects["gone"]
    run = h.wait(h.engine.start("o", user_id="u1")["run_id"])
    assert run["steps"]["s"]["error_name"] == "ACTIVE_OBJECT_NOT_FOUND" and h.jobs.calls == []


def test_process_to_bucket_saves_the_output_and_bucket_to_process_passes_it_as_data(h):
    h.buckets.objects["alice/out"] = []
    h.function("produce", lambda p, e: {"rows": [1, 2]})
    h.function("consume", lambda p, e: {"data": p["data"], "file": e["file"]})
    h.service.create(user_id="u1", name="io", graph=graph(
        [fn("produce"), bucket("save", "alice/out"), fn("consume")], ("produce", "save"), ("save", "consume")))
    run = h.wait(h.engine.start("io", user_id="u1")["run_id"])

    upload = h.buckets.uploads[0]
    assert upload["name"] == f"io/{run['run_id']}/produce.json" and upload["user_id"] == "u1"
    assert upload["data"] == b'{"rows": [1, 2]}'
    assert h.jobs.calls[0]["output_buckets"] == ["alice/out"]
    file = {"bucket": "alice/out", "name": upload["name"], "version": 1, "format": "json", "size": 16,
            "source": "produce"}
    assert run["result"]["consume"]["data"] == {DATA_REF_KEY: file}
    assert run["result"]["consume"]["file"] == file


def test_a_bucket_with_several_files_runs_the_next_step_once_per_file(h):
    for name in ("a.csv", "b.csv", "c.csv"):
        h.buckets.put("alice/in", name)
    h.buckets.put("alice/in", "a.csv")           # a.csv v2: only the newest version is read
    h.function("each", lambda p, e: {"file": e["file"]["name"], "v": e["file"]["version"]})
    h.service.create(user_id="u1", name="map", graph=graph(
        [bucket("in", "alice/in"), fn("each", parallelism=2)], ("in", "each")))
    run = h.wait(h.engine.start("map", user_id="u1")["run_id"])
    assert run["result"]["each"] == [{"file": "a.csv", "v": 2}, {"file": "b.csv", "v": 1},
                                     {"file": "c.csv", "v": 1}]
    assert len(run["steps"]["each"]["items"]) == 3

    h.service.update(user_id="u1", ref="map", graph=graph(
        [bucket("in", "alice/in", selected_items=[{"name": "b.csv"}]), fn("each")], ("in", "each")))
    run = h.wait(h.engine.start("map", user_id="u1")["run_id"])
    assert run["result"]["each"] == {"file": "b.csv", "v": 1}          # one file: one plain run


def test_all_at_once_gets_every_file_in_one_call(h):
    for name in ("a.csv", "b.csv"):
        h.buckets.put("alice/in", name)
    h.function("join", lambda p, e: {"names": [d["__axo_data__"]["name"] for d in p["data"]],
                                     "event": [f["name"] for f in e["files"]]},
               params=[{"name": "data", "type": "json"}])
    h.service.create(user_id="u1", name="joined", graph=graph(
        [bucket("in", "alice/in"), fn("join", files_mode="all_at_once")], ("in", "join")))
    run = h.wait(h.engine.start("joined", user_id="u1")["run_id"])
    assert run["result"]["join"] == {"names": ["a.csv", "b.csv"], "event": ["a.csv", "b.csv"]}
    assert h.jobs.targets().count("join") == 1 and not run["steps"]["join"]["items"]


def test_a_failed_file_completes_with_errors_and_a_resume_runs_only_it(h):
    for name in ("a.csv", "b.csv", "c.csv"):
        h.buckets.put("alice/in", name)
    broken = {"b.csv"}

    def each(p, e):
        if e["file"]["name"] in broken:
            raise StepFailure("bad file")
        return e["file"]["name"]

    h.function("each", each)
    h.function("total", lambda p, e: {"got": e["previous"]["each"]["output"],
                                      "status": e["previous"]["each"]["status"]})
    h.service.create(user_id="u1", name="map", graph=graph(
        [bucket("in", "alice/in"), fn("each", max_attempts=1), fn("total")], ("in", "each"), ("each", "total")))
    run = h.wait(h.engine.start("map", user_id="u1")["run_id"])
    assert run["status"] == "completed_with_errors"
    assert run["steps"]["each"]["status"] == "completed_with_errors"
    assert "1 of 3 files failed" in run["steps"]["each"]["error"]
    assert run["result"]["total"] == {"got": ["a.csv", "c.csv"], "status": "completed_with_errors"}

    broken.clear()
    h.engine.resume(run["run_id"])
    run = h.wait(run["run_id"], until=lambda r: r["status"] not in ("queued", "running") and r["resumes"] == 1)
    assert run["status"] == "completed"
    assert run["result"]["total"]["got"] == ["a.csv", "b.csv", "c.csv"]
    each_calls = [c for c in h.jobs.calls if c["target"] == "each"]
    assert [c["event"]["file"]["name"] for c in each_calls].count("a.csv") == 1     # a and c kept


def test_without_completed_with_errors_the_first_error_fails_the_run(h):
    def bad(p, e):
        deadline = time.monotonic() + 5
        while "slow" not in h.jobs.targets() and time.monotonic() < deadline:   # slow is running first
            time.sleep(0.002)
        raise StepFailure("boom")

    h.function("bad", bad)
    h.function("slow", lambda p, e: "late")
    h.function("after", lambda p, e: "never")
    h.jobs.hold("slow")
    h.service.create(user_id="u1", name="strict", graph=graph(
        [fn("bad", max_attempts=1), fn("slow"), fn("after")], ("slow", "after")),
        settings=ChoreographySettings(allow_completed_with_errors=False))
    run = h.wait(h.engine.start("strict", user_id="u1")["run_id"])
    assert run["status"] == "failed" and run["stopped_by"] == "bad"
    assert run["steps"]["bad"]["status"] == "failed"
    assert run["steps"]["slow"]["status"] == "cancelled" and "stopped: step bad failed" in run["steps"]["slow"]["error"]
    assert run["steps"]["after"]["status"] == "cancelled" and h.jobs.cancelled


def test_without_completed_with_errors_a_failed_file_fails_the_step(h):
    for name in ("a.csv", "b.csv"):
        h.buckets.put("alice/in", name)
    h.function("each", lambda p, e: (_ for _ in ()).throw(StepFailure("bad")) if e["file"]["name"] == "b.csv"
               else "ok")
    h.service.create(user_id="u1", name="map", graph=graph(
        [bucket("in", "alice/in"), fn("each", max_attempts=1, parallelism=1)], ("in", "each")),
        settings=ChoreographySettings(allow_completed_with_errors=False))
    run = h.wait(h.engine.start("map", user_id="u1")["run_id"])
    assert run["status"] == "failed" and run["steps"]["each"]["status"] == "failed"
    assert run["steps"]["each"]["error_name"] == "CHOREOGRAPHY_STEP_FAILED"


def test_conditions_pick_the_branch_and_the_step_after_both_runs(h):
    rows = {"n": 120}
    h.function("count", lambda p, e: {"rows": rows["n"]})
    h.function("report", lambda p, e: "report")
    h.function("alert", lambda p, e: "alert")
    h.function("end", lambda p, e: sorted(e["previous"]))
    positive = {"condition": {"rules": [{"field": "rows", "op": ">", "value": 0}]}}
    h.service.create(user_id="u1", name="branch", graph=graph(
        [fn("count"), fn("report"), fn("alert"), fn("end")],
        ("count", "report", positive), ("count", "alert", {"otherwise": True}), ("report", "end"), ("alert", "end")))
    run = h.wait(h.engine.start("branch", user_id="u1")["run_id"])
    assert run["status"] == "completed"
    assert run["steps"]["alert"]["status"] == "not_taken" and run["steps"]["report"]["status"] == "completed"
    assert run["result"]["end"] == ["report"]
    assert run["edge_states"] == {"e0": "taken", "e1": "not_taken", "e2": "taken"}   # alert never ran
    assert "alert" not in h.jobs.targets()

    rows["n"] = 0
    run = h.wait(h.engine.start("branch", user_id="u1")["run_id"])
    assert run["steps"]["report"]["status"] == "not_taken" and run["result"]["end"] == ["alert"]


def test_a_condition_that_cant_be_compared_is_false_and_warns(h):
    h.function("count", lambda p, e: {"rows": "many"})
    h.function("report", lambda p, e: "report")
    h.service.create(user_id="u1", name="warn", graph=graph(
        [fn("count"), fn("report")],
        ("count", "report", {"condition": {"rules": [{"field": "rows", "op": ">", "value": 0}]}})))
    run = h.wait(h.engine.start("warn", user_id="u1")["run_id"])
    assert run["status"] == "completed" and run["steps"]["report"]["status"] == "not_taken"
    assert "can't be compared" in run["steps"]["count"]["warnings"][0]


def test_a_run_keeps_only_its_jobs_and_resumes_while_their_answers_are_kept(h):
    state = {"fail": True}
    h.function("first", lambda p, e: {"v": 1})
    h.function("second", lambda p, e: (_ for _ in ()).throw(StepFailure("x")) if state["fail"] else p)
    h.service.create(user_id="u1", name="keep", graph=graph([fn("first"), fn("second")], ("first", "second")),
                     settings=ChoreographySettings(keep_results="1d"))
    run = h.wait(h.engine.start("keep", user_id="u1")["run_id"])
    first = run["steps"]["first"]
    assert "output" not in first and first["job_id"] and first["output_bytes"] > 0
    assert h.jobs.calls[0]["keep_result"] == "1d"
    assert h.jobs.calls[0]["choreography"]["run_id"] == run["run_id"]
    assert h.jobs.calls[0]["choreography"]["step"] == "first"

    h.jobs.expired.add(first["job_id"])               # its keep time is over
    state["fail"] = False
    with pytest.raises(ConflictError) as err:
        h.engine.resume(run["run_id"])
    assert err.value.error_name == "CHOREOGRAPHY_RUN_NOT_RESUMABLE"


def test_bucket_triggers_start_one_run_per_file_and_never_retrigger_themselves(h):
    h.buckets.objects["alice/raw"] = []
    h.buckets.objects["alice/raw-copy"] = []
    h.function("copy", lambda p, e: {"from": e["trigger"]["name"]})
    h.service.create(user_id="u1", name="on-new", graph=graph(
        [bucket("raw", "alice/raw"), fn("copy"), bucket("out", "alice/raw")], ("raw", "copy"), ("copy", "out")))
    h.service.add_trigger(user_id="u2", ref="on-new", trigger=ChoreographyTrigger(bucket="alice/raw", prefix="in/"))

    assert h.engine.on_bucket_written("alice/raw", "other/x", 1) == []
    first = h.engine.on_bucket_written("alice/raw", "in/x.csv", 3)
    again = h.engine.on_bucket_written("alice/raw", "in/x.csv", 3)
    assert first[0]["run_id"] == again[0]["run_id"]
    run = h.wait(first[0]["run_id"])
    assert run["status"] == "completed" and run["acting_user_id"] == "u2" and run["started_by_type"] == "trigger"
    assert run["steps"]["raw"]["files"][0]["name"] == "in/x.csv"
    time.sleep(0.05)
    assert len(h.runs.list(run["choreography_id"])) == 1            # its own write didn't start another

    # The same write reported again through the CP (axo_storage -> CP ->
    # /bucket-events) carries the choreography's tag: still no new run.
    written = h.buckets.uploads[-1]
    assert written["source"] == f"choreography:{run['choreography_id']}"
    assert h.engine.on_bucket_written("alice/raw", written["name"], written["version"],
                                      source_choreography_id=run["choreography_id"]) == []
    assert len(h.runs.list(run["choreography_id"])) == 1


def test_a_trigger_starts_runs_only_when_its_creator_may_read_the_bucket(h):
    h.buckets.objects["alice/raw"] = []
    h.function("copy", lambda p, e: "ok")
    h.service.create(user_id="u1", name="watch", graph=graph([bucket("raw", "alice/raw"), fn("copy")], ("raw", "copy")))
    h.service.add_trigger(user_id="u2", ref="watch", trigger=ChoreographyTrigger(bucket="alice/raw"))

    assert h.engine.on_bucket_written("alice/raw", "a.csv", 1, allowed_users=["someone-else"]) == []
    [started] = h.engine.on_bucket_written("alice/raw", "a.csv", 1, allowed_users=["u2"])
    assert h.wait(started["run_id"])["status"] == "completed"


def test_parallel_runs_are_capped_and_the_rest_wait_in_line(h):
    gate = h.jobs.hold("work")
    h.function("work", lambda p, e: "done")
    h.service.create(user_id="u1", name="capped", graph=graph([fn("work")]),
                     settings=ChoreographySettings(max_parallel_runs=2, queue_limit=2))
    ids = [h.engine.start("capped", user_id="u1")["run_id"] for _ in range(4)]
    statuses = [h.engine.run_info(i)["status"] for i in ids]
    assert statuses == ["running", "running", "queued", "queued"]
    with pytest.raises(TooManyRequestsError):
        h.engine.start("capped", user_id="u1")
    gate.set()
    assert [h.wait(i)["status"] for i in ids] == ["completed"] * 4


def test_retries_then_success_and_step_timeouts(h):
    tries = {"n": 0}

    def flaky(p, e):
        tries["n"] += 1
        if tries["n"] < 3:
            raise StepFailure("not yet")
        return "ok"

    h.function("flaky", flaky)
    h.service.create(user_id="u1", name="retry", graph=graph([fn("flaky", max_attempts=4)]))
    run = h.wait(h.engine.start("retry", user_id="u1")["run_id"])
    # the step's job is tried again by the endpoint, with the step's settings
    assert run["status"] == "completed" and h.jobs.tries[run["steps"]["flaky"]["job_id"]] == 3
    assert h.jobs.calls[0]["attempts"]["max_attempts"] == 4

    h.jobs.hold("stuck")
    h.function("stuck", lambda p, e: "late")
    h.service.create(user_id="u1", name="slow", graph=graph([fn("stuck", max_attempts=1, timeout_seconds=0.05)]))
    run = h.wait(h.engine.start("slow", user_id="u1")["run_id"])
    assert run["steps"]["stuck"]["error_name"] == "CHOREOGRAPHY_STEP_TIMEOUT" and h.jobs.cancelled


def test_cancel_and_resume(h):
    gate = h.jobs.hold("long")
    h.function("long", lambda p, e: "x")
    h.function("next", lambda p, e: "y")
    h.service.create(user_id="u1", name="stop", graph=graph([fn("long"), fn("next")], ("long", "next")))
    run_id = h.engine.start("stop", user_id="u1")["run_id"]
    h.wait(run_id, until=lambda r: r["steps"]["long"]["job_id"])
    h.engine.cancel(run_id)
    run = h.wait(run_id)
    assert run["status"] == "cancelled" and run["steps"]["next"]["status"] == "cancelled"

    del h.jobs.gates["long"]
    gate.clear()
    resumed = h.wait(h.engine.resume(run_id)["run_id"])
    assert resumed["status"] == "completed" and resumed["resumes"] == 1
    assert job_id_for(run_id, "long", None, 1) in [c["job_id"] for c in h.jobs.calls]


def test_resume_keeps_finished_steps(h):
    state = {"fail": True}
    h.function("first", lambda p, e: {"v": 1})
    h.function("second", lambda p, e: (_ for _ in ()).throw(StepFailure("x")) if state["fail"] else p)
    h.service.create(user_id="u1", name="res", graph=graph(
        [fn("first"), fn("second", max_attempts=1)], ("first", "second")))
    run_id = h.engine.start("res", user_id="u1")["run_id"]
    assert h.wait(run_id)["status"] == "failed"
    state["fail"] = False
    run = h.wait(h.engine.resume(run_id)["run_id"])
    assert run["status"] == "completed" and run["result"]["second"] == {"v": 1}
    assert h.jobs.targets().count("first") == 1


def test_a_restarted_vem_carries_on_without_running_a_step_twice(h):
    gate = h.jobs.hold("a")
    h.function("a", lambda p, e: {"n": 1})
    h.function("b", lambda p, e: p)
    h.service.create(user_id="u1", name="restart", graph=graph([fn("a"), fn("b")], ("a", "b")))
    run_id = h.engine.start("restart", user_id="u1")["run_id"]
    h.wait(run_id, until=lambda r: r["steps"]["a"]["job_id"])
    h.engine.shutdown()                              # the VEM goes away mid-step ...

    h.engine = h.new_engine()                        # ... and comes back
    assert h.engine.recover() == 1
    gate.set()
    run = h.wait(run_id)
    assert run["status"] == "completed" and run["result"]["b"] == {"n": 1}
    assert h.jobs.targets().count("a") == 1


def test_a_job_the_endpoint_forgot_is_sent_again_after_a_restart(h):
    gate = h.jobs.hold("a")
    h.function("a", lambda p, e: "ok")
    h.service.create(user_id="u1", name="lost", graph=graph([fn("a")]))
    run_id = h.engine.start("lost", user_id="u1")["run_id"]
    job_id = h.wait(run_id, until=lambda r: r["steps"]["a"]["job_id"])["steps"]["a"]["job_id"]
    h.engine.shutdown()
    h.jobs.forget.add(job_id)
    del h.jobs.gates["a"]
    gate.set()
    h.engine = h.new_engine()
    h.engine.recover()
    assert h.wait(run_id)["status"] == "completed"


def test_an_answer_over_1_mb_reaches_the_next_step_as_its_kept_file(h):
    h.function("big", lambda p, e: {"blob": "x" * 1_100_000})
    h.function("after", lambda p, e: {"ref": e["previous"]["big"]["output_ref"]})
    h.service.create(user_id="u1", name="big", graph=graph([fn("big"), fn("after")], ("big", "after")))
    run = h.wait(h.engine.start("big", user_id="u1")["run_id"])
    job_id = run["steps"]["big"]["job_id"]
    assert run["result"]["after"]["ref"][DATA_REF_KEY]["name"] == f"{job_id}.json"


def test_metrics_and_live_messages(h):
    h.function("a", lambda p, e: 1)
    h.service.create(user_id="u1", name="metrics", graph=graph([fn("a")]))
    run = h.wait(h.engine.start("metrics", user_id="u1")["run_id"])
    records = h.engine.drain_metrics(now=time.time() + 20)
    by_metric = {r.metric: dict(r.dims) for r in records}
    assert by_metric[m.CHOREOGRAPHY_RUNS] == {"choreography": "metrics", "choreography_version": "1",
                                              "outcome": "completed"}
    assert by_metric[m.CHOREOGRAPHY_STEPS]["step"] == "a"
    topics = {t for t, _m in h.broadcaster.messages}
    assert {"choreographies", f"choreographies:{run['run_id']}"} <= topics
    steps = [msg for t, msg in h.broadcaster.messages if msg["type"] == "step"]
    assert [s["status"] for s in steps][-1] == "completed"


def test_usage(h):
    h.function("a", lambda p, e: 1)
    h.service.create(user_id="u1", name="used", graph=graph([fn("a")]))
    for user in ("u1", "u1", "u2"):
        h.wait(h.engine.start("used", user_id=user)["run_id"])
    usage = h.runs.usage(h.service.get("used").choreography_id)
    assert usage["runs"] == 3 and usage["by_user"] == {"u1": 2, "u2": 1}
    assert usage["success_rate"] == 1.0 and usage["by_trigger"] == {"user": 3}


def test_an_on_failure_arrow_gets_the_error_and_the_run_completes_with_errors(h):
    fails = {"on": True}
    h.function("train", lambda p, e: (_ for _ in ()).throw(StepFailure("no memory", "OUT_OF_MEMORY"))
               if fails["on"] else {"acc": 0.9})
    h.function("save", lambda p, e: "saved")
    h.function("alert", lambda p, e: {"told": e["failure"]["error_name"], "step": e["failure"]["step"]})
    # a strict run: a handled failure must not stop it
    h.service.create(user_id="u1", name="safe", graph=graph(
        [fn("train", max_attempts=1), fn("save"), fn("alert")],
        ("train", "save"), ("train", "alert", {"on_failure": True})),
        settings=ChoreographySettings(allow_completed_with_errors=False))
    run = h.wait(h.engine.start("safe", user_id="u1")["run_id"])
    assert run["status"] == "completed_with_errors" and run["stopped_by"] is None
    assert run["steps"]["train"]["status"] == "failed"
    assert run["steps"]["save"]["status"] == "skipped"
    assert run["result"]["alert"] == {"told": "OUT_OF_MEMORY", "step": "train"}
    assert run["edge_states"]["e1"] == "taken"

    fails["on"] = False                    # it worked: the on-failure arrow isn't followed
    run = h.wait(h.engine.start("safe", user_id="u1")["run_id"])
    assert run["status"] == "completed"
    assert run["steps"]["alert"]["status"] == "not_taken" and run["edge_states"]["e1"] == "not_taken"


def test_an_on_failure_arrow_after_files_lists_the_failed_ones(h):
    for name in ("a.csv", "b.csv"):
        h.buckets.put("alice/in", name)
    h.function("each", lambda p, e: (_ for _ in ()).throw(StepFailure("bad")) if e["file"]["name"] == "b.csv"
               else "ok")
    h.function("fix", lambda p, e: [f["file"]["name"] for f in e["failure"]["failed_files"]])
    h.function("next", lambda p, e: e["previous"]["each"]["output"])
    h.service.create(user_id="u1", name="map", graph=graph(
        [bucket("in", "alice/in"), fn("each", max_attempts=1), fn("fix"), fn("next")],
        ("in", "each"), ("each", "fix", {"on_failure": True}), ("each", "next")))
    run = h.wait(h.engine.start("map", user_id="u1")["run_id"])
    assert run["steps"]["each"]["status"] == "completed_with_errors"
    assert run["result"]["fix"] == ["b.csv"] and run["result"]["next"] == ["ok"]


def test_on_failure_arrows_are_checked_when_saved(h):
    h.function("a", lambda p, e: 1)
    h.function("b", lambda p, e: 1)
    cond = {"rules": [{"field": "x", "op": ">", "value": 0}]}
    bad = graph([fn("a"), fn("b")], ("a", "b", {"on_failure": True, "condition": cond}))
    assert any("can't also have a condition" in p for p in h.service.problems(bad))
    into_bucket = graph([fn("a"), bucket("out", "alice/out")], ("a", "out", {"on_failure": True}))
    assert any("'on failure' arrows go from" in p for p in h.service.problems(into_bucket))


def test_a_run_can_pick_its_own_starting_files(h):
    for name in ("a.csv", "b.csv", "c.csv"):
        h.buckets.put("alice/in", name)
    h.function("each", lambda p, e: e["file"]["name"])
    h.service.create(user_id="u1", name="map", graph=graph(
        [bucket("in", "alice/in", selected_items=[{"name": "a.csv"}]), fn("each")], ("in", "each")))
    picked = h.wait(h.engine.start("map", user_id="u1", files={"in": [{"name": "b.csv"}, {"name": "c.csv"}]})["run_id"])
    assert picked["result"]["each"] == ["b.csv", "c.csv"]
    every = h.wait(h.engine.start("map", user_id="u1", files={"in": "all"})["run_id"])
    assert every["result"]["each"] == ["a.csv", "b.csv", "c.csv"]
    assert h.wait(h.engine.start("map", user_id="u1")["run_id"])["result"]["each"] == "a.csv"
    with pytest.raises(Exception, match="isn't a bucket step"):
        h.engine.start("map", user_id="u1", files={"each": "all"})


def test_a_middle_bucket_passes_on_only_this_runs_files(h):
    h.buckets.put("alice/out", "old.png")                  # there before: never passed on

    def render(p, e):
        job = job_id_for(e["run"]["run_id"], e["step"], None, 0)
        h.buckets.put("alice/out", f"{e['run']['run_id']}/chart.png", source=f"job:ve1:{job}")
        return {"charts": 1}

    h.function("render", render)
    h.function("publish", lambda p, e: [f["name"].split("/")[-1] for f in e["files"]])
    h.service.create(user_id="u1", name="charts", graph=graph(
        [fn("render"), bucket("out", "alice/out"), fn("publish", files_mode="all_at_once")],
        ("render", "out"), ("out", "publish")))
    run = h.wait(h.engine.start("charts", user_id="u1")["run_id"])
    assert sorted(run["result"]["publish"]) == ["chart.png", "render.json"]
    assert [f.get("written_by") for f in run["steps"]["out"]["files"]] == ["job", None]

    h.service.update(user_id="u1", ref="charts", graph=graph(
        [fn("render"), bucket("out", "alice/out", save_result=False), fn("publish", files_mode="all_at_once")],
        ("render", "out"), ("out", "publish")))
    run = h.wait(h.engine.start("charts", user_id="u1")["run_id"])
    assert run["result"]["publish"] == ["chart.png"]
