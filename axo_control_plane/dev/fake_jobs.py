"""The dev VEM's functions and jobs (see ui_server.FakeVemApi): registered
functions with their code and settings, jobs that queue, run, fail, try
again and finish as time passes, their attempts and results, and a few
choreography step jobs (started by fake_choreographies) that group in the jobs list. Each finished
attempt is also reported to the CP intake as a JobAttemptRecorded, so the
cost column has figures. Shapes follow the real VEM's routes."""

from __future__ import annotations

import email.parser
import email.policy
import hashlib
import json
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

KEEP = {"1h": 3600, "1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400, "1y": 365 * 86400,
        "never": None}
ACTIVE = ("QUEUED", "RUNNING", "WAITING_RETRY")
FAULTS = {"failed_user": "code", "failed_platform": "platform", "timed_out_in_queue": "queue",
          "cancelled": "cancelled"}

SOURCES = {
    "train-model": ('''import json

from axo_endpoint import dataio
from axo_endpoint.core.dataio import IORef


def train_model(dataset: str, epochs: int = 10, learning_rate: float = 0.001, shuffle: bool = True, ctx=None):
    """Trains the model on a dataset and saves it.

    Args:
        dataset: The bucket file with the training rows.
        epochs: How many passes over the data.
        learning_rate: How big each step is.
        shuffle: Mix the rows before each pass.
    """
    rows = dataio.read(IORef(bucket="demo/ml-data", name=dataset, format="csv"))
    loss = 1.0
    for epoch in range(epochs):
        loss *= 0.82
    ref = dataio.write({"loss": loss}, IORef(bucket="demo/ml-models", name="model.json", format="pickle"))
    return {"loss": round(loss, 4), "rows": len(rows), "model": ref.name}
''', "process"),
    "predict": ('''def predict(features: list, threshold: float = 0.5, ctx=None):
    """Scores one row of features.

    Args:
        features: The row's numbers, in training order.
        threshold: Above it the answer is "yes".
    """
    score = sum(features) / (len(features) or 1) / 10
    return {"score": round(score, 3), "label": "yes" if score > threshold else "no"}
''', "container"),
    "build-features": ('''def build_features(source: str, window_days: int = 30, ctx=None):
    """Builds the feature table from the raw events."""
    return {"rows": 12_000, "columns": 48, "window_days": window_days}
''', "process"),
    "ingest-csv": ('''from axo import DataRef


def ingest_csv(file: DataRef, delimiter: str = ",", ctx=None):
    """Reads a CSV upload into the clean bucket."""
    return {"rows": 5321, "rejected": 12}
''', "process"),
    "clean-rows": ('''def clean_rows(rows: int, drop_empty: bool = True, ctx=None):
    """Removes broken rows."""
    return {"kept": rows - 40, "dropped": 40}
''', "process"),
    "daily-report": ('''def daily_report(day: str, email_to: str = "", ctx=None):
    """Makes the daily report PDF."""
    return {"pages": 4, "file": f"reports/{day}.pdf"}
''', "process"),
    "hello": ('''def hello(name: str = "world", ctx=None):
    """Says hello."""
    return {"greeting": f"Hello, {name}!"}
''', "process"),
}

TRACEBACK = '''Traceback (most recent call last):
  File "/runner/axo/runtime/invoke.py", line 88, in call_function
    return fn(**params)
  File "axo_code_{fn}", line 14, in {fn}
    rows = dataio.read(IORef(bucket="demo/ml-data", name=dataset, format="csv"))
  File "/runner/axo_endpoint/dataio.py", line 61, in read
    raise DataIOError(reply.error)
axo_endpoint.dataio.DataIOError: OBJECT_NOT_FOUND: no object named 'missing.csv' in demo/ml-data'''


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def _epoch(value: Optional[str]) -> Optional[float]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else None


def _hash(text: str) -> str:
    h = hashlib.sha256(b"source")
    h.update(b":")
    h.update(text.encode())
    return h.hexdigest()


def _schema(source: str, name: str) -> Dict[str, Any]:
    from axo_shared.functions.signature import check_source
    return check_source(source.encode(), name)


class FakeFunctions:
    def __init__(self, report: Callable[[str, Dict[str, Any]], None], user_name: Callable[[str], str]) -> None:
        self._report = report
        self._name_of = user_name
        self._ves: Dict[str, Dict[str, Any]] = {}
        self._rng = random.Random(11)
        self.people = {"owner": "demo", "other": "ana"}       # user keys (DevPlatform sets them)

    # ── state ────────────────────────────────────────────────────────────────
    def _ve(self, ve_id: str, functions: List[tuple], endpoints: List[str], owner: str) -> Dict[str, Any]:
        ve = self._ves.get(ve_id)
        if ve is None:
            ve = self._ves[ve_id] = {"functions": {}, "code": {}, "jobs": {}, "attempts": {}, "results": {},
                                     "runs": {}, "endpoints": endpoints, "owner": owner, "secrets": {}, "defaults": {}}
            self._seed(ve, ve_id, functions)
        ve["endpoints"] = endpoints or ve["endpoints"]
        self._advance(ve, ve_id)
        return ve

    def _seed(self, ve, ve_id, functions) -> None:
        now = time.time()
        for fid, fname, ms, fail in functions:
            source, runtime = SOURCES.get(fname, (f"def {fname.replace('-', '_')}(ctx=None):\n    return {{}}\n", "process"))
            pyname = fname.replace("-", "_")
            versions = 2 if fname in ("train-model", "predict") else 1
            for v in range(1, versions + 1):
                text = source if v == versions else source.replace("0.82", "0.9").replace("/ 10", "/ 12")
                check = _schema(text, pyname)
                spec = {"type": runtime, "call_style": "kwargs", "max_concurrency": 2 if runtime == "container" else 1,
                        "idle_ttl_seconds": None, "max_attempts": 1, "retry_delay_seconds": 1.0,
                        "retry_backoff": "fixed", "keep_result": None,
                        "env_vars": {"MODEL_DIR": "/models", "API_TOKEN": None} if fname == "train-model" else {},
                        "secret_env": ["API_TOKEN"] if fname == "train-model" else []}
                if runtime == "container":
                    spec.update(python_version="3.12", requirements=["numpy==2.2.4", "scikit-learn"])
                registered = now - (12 - v * 3) * 86400
                ve["functions"][(fid, v)] = {
                    "function_id": fid, "version": v, "name": pyname, "runtime_spec": spec,
                    "params_schema": check["params_schema"], "description": check["description"],
                    "code_format": "source", "code_hash": _hash(text), "endpoint_id": list(ve["endpoints"][:1]),
                    "container_status": "running" if runtime == "container" else None, "deleted_at": None,
                    "registered_by": self.people["other"] if fname == "build-features" else ve["owner"],
                    "registered_by_name": "Ana Lopez" if fname == "build-features" else "Dana Demo",
                    "registered_at": _iso(registered), "has_display_source": False,
                    "virtual_environment_id": ve_id,
                }
                ve["code"][(fid, v)] = text
            if fname == "train-model":
                ve["secrets"][fid] = {"API_TOKEN": "s3cret"}
        # The past two days of jobs (newest last).
        names = list(ve["functions"])
        for k in range(90):
            fid, v = max((key for key in names if key[0] == names[k % len(names)][0]), key=lambda key: key[1])
            fn = next(f for f in functions if f[0] == fid)
            at = now - (90 - k) * 1900 - self._rng.uniform(0, 600)
            r = self._rng.random()
            plan = ("ok" if r > fn[3] * 6 else "retry-ok" if r > fn[3] * 4 else "code" if r > fn[3] * 2
                    else "platform-ok" if r > fn[3] else "cancelled")
            self._past_job(ve, ve_id, fid, v, fn[2], at, plan, started_by=("api_key" if k % 4 == 0 else "user"))
        # One running now, one queued.
        live = names[0]
        self._new_job(ve, ve_id, live[0], live[1], {"dataset": "2026-10.csv", "epochs": 30}, ve["owner"], "user", now - 20)
        self._new_job(ve, ve_id, live[0], live[1], {"dataset": "2026-09.csv"}, ve["owner"], "user", now - 2)

    def _past_job(self, ve, ve_id, fid, v, ms, at, plan, started_by="user", choreography=None):
        params = self._sample_params(ve, fid, v)
        who = "key-ci" if started_by == "api_key" else ve["owner"]
        job = self._job_doc(ve, fid, v, params, who, started_by, at, choreography=choreography)
        t = at
        tries = {"ok": ["completed"], "retry-ok": ["failed_user", "completed"], "code": ["failed_user"] * 2,
                 "platform-ok": ["failed_platform", "completed"], "cancelled": ["cancelled"]}[plan]
        job["max_attempts"] = 3 if plan in ("retry-ok",) else 2 if plan == "code" else 1
        for n, outcome in enumerate(tries, start=1):
            run_ms = max(20.0, self._rng.lognormvariate(0, 0.4) * ms) * (0.4 if outcome == "cancelled" else 1)
            reason = "first" if n == 1 else ("platform_failed" if tries[n - 2] == "failed_platform" else "code_failed")
            last = n == len(tries)
            t = self._finish_attempt(ve, ve_id, job, n, outcome, t, run_ms, reason,
                                     retry_at=None if last else t + 2)
            t += 2
        if job["status"] == "COMPLETED":
            self._keep_result(ve, job, at)
        return job

    def _sample_params(self, ve, fid, v) -> Dict[str, Any]:
        doc = ve["functions"][(fid, v)]
        out = {}
        for p in doc["params_schema"] or []:
            if p["required"]:
                out[p["name"]] = {"number": self._rng.randrange(1, 50), "string": f"2026-{self._rng.randrange(1, 11):02d}.csv",
                                  "boolean": True, "json": [1.5, 3.2, 8.0],
                                  "data_ref": {"bucket": "demo/ml-data", "name": "raw/2026-09.csv"}}.get(p["type"], None)
        return out

    def _job_doc(self, ve, fid, v, params, who, started_by_type, at, choreography=None, keep=None, attempts=None):
        doc = ve["functions"][(fid, v)]
        job_id = (f"ch-{uuid.uuid4().hex[:20]}" if choreography else uuid.uuid4().hex)
        name = "ci-runner" if who == "key-ci" else self._name_of(who)
        spec = doc["runtime_spec"] or {}
        job = {"job_id": job_id, "function_id": fid, "status": "QUEUED", "function_version": v, "duration_ms": None,
               "params": params, "endpoint_id": self._rng.choice(ve["endpoints"]) if ve["endpoints"] else None,
               "created_at": _iso(at), "attempt": 1,
               "max_attempts": int((attempts or {}).get("max_attempts") or spec.get("max_attempts") or 1),
               "next_attempt_at": None, "started_at": None, "finished_at": None, "outcome": None, "error": None,
               "kind": "function", "function_name": doc["name"], "object_name": None, "object_method": None,
               "acting_user_id": ve["owner"] if who == "key-ci" else who, "started_by_type": started_by_type,
               "started_by_id": (choreography or {}).get("choreography_id") or who,
               "started_by_name": (choreography or {}).get("choreography_name") or name,
               "choreography": choreography, "keep_result": keep, "keep_until": None, "result": None,
               "cancelled_by": None, "retried_by": None, "warnings": [],
               "_plan": None, "_attempts_cfg": attempts or {}}
        ve["jobs"][job_id] = job
        ve["attempts"][job_id] = []
        return job

    def _finish_attempt(self, ve, ve_id, job, n, outcome, start, run_ms, reason, retry_at=None, cancelled_by=None):
        waits = {"control_plane": self._rng.uniform(1, 6), "vem": self._rng.uniform(2, 15),
                 "endpoint": self._rng.uniform(1, 40), "runner": self._rng.uniform(0, 30)}
        started = start + sum(waits.values()) / 1000
        finished = started + run_ms / 1000
        fname = job["function_name"]
        failure = None
        if outcome == "failed_user":
            failure = {"error_class": "OBJECT_NOT_FOUND", "error_code": 9306, "component": "dataio.read",
                       "error_type": "DataIOError",
                       "message": "no object named 'missing.csv' in demo/ml-data",
                       "traceback": TRACEBACK.format(fn=fname)}
        elif outcome == "failed_platform":
            failure = {"error_class": "CONTAINER_CRASH_ERROR", "error_code": 3006, "component": "axo_endpoint.runtime",
                       "error_type": None, "message": "the runner stopped while running the job (out of memory)"}
        elif outcome == "cancelled":
            failure = {"error_class": "JOB_CANCELLED", "error_code": 3008, "component": "axo_vem.jobs",
                       "error_type": None, "message": f"cancelled by {cancelled_by or 'Dana Demo'}"}
        files = []
        if outcome == "completed" and fname == "train_model":
            files = [{"bucket": "demo/ml-models", "name": "model.json", "version": 1, "size": 96, "format": "json"},
                     {"bucket": "demo/ml-models", "name": "loss-curve.png", "version": 1, "size": 1_700,
                      "format": "raw"},
                     {"bucket": "demo/ml-models", "name": "notes.txt", "version": 1, "size": 27, "format": "raw"},
                     {"bucket": "demo/ml-models", "name": "weights.bin", "version": 1, "size": 16_384,
                      "format": "raw"}]
        record = {"job_id": job["job_id"], "attempt": n, "trace_id": uuid.uuid4().hex, "outcome": outcome,
                  "owner_type": "user", "owner_id": ve["owner"],
                  "started_by_type": "user" if job["started_by_type"] == "trigger" else job["started_by_type"],
                  "started_by_id": job["started_by_id"], "function_id": job["function_id"],
                  "function_version": job["function_version"], "endpoint_cores": 1.0, "endpoint_ram_mb": 2048,
                  "endpoint_id": job["endpoint_id"], "received_at": _iso(start), "started_at": _iso(started),
                  "finished_at": _iso(finished), "layer_waits_ms": waits, "execution_ms": run_ms,
                  "bytes_read": {"demo/ml-data": self._rng.randrange(10_000, 3_000_000)} if fname == "train_model" else {},
                  "bytes_written": {"demo/ml-models": 66_642} if files else {},
                  "cache_hits": self._rng.randrange(0, 3), "cache_misses": self._rng.randrange(0, 2),
                  "reason": reason, "retried_by": job.get("retried_by") if reason == "manual" else None,
                  "max_attempts": job["max_attempts"], "failure": failure, "retry_at": _iso(retry_at),
                  "files_written": files, "virtual_environment_id": ve_id}
        if job["choreography"]:
            record.update(choreography_run_id=job["choreography"]["run_id"],
                          choreography_step=job["choreography"]["step"],
                          choreography_version=job["choreography"]["version"])
        ve["attempts"][job["job_id"]].append(record)
        self._report(ve_id, {**record, "received_at": start, "started_at": started, "finished_at": finished,
                             "retry_at": retry_at, "failure": {k: v for k, v in (failure or {}).items()
                                                               if k != "traceback"} or None})
        job.update(attempt=n, started_at=_iso(started), endpoint_id=job["endpoint_id"])
        if retry_at:
            job.update(status="WAITING_RETRY", next_attempt_at=_iso(retry_at),
                       error=self._error(failure, outcome))
        else:
            job.update(status={"completed": "COMPLETED", "cancelled": "CANCELLED"}.get(outcome, "FAILED"),
                       outcome=outcome, finished_at=_iso(finished), next_attempt_at=None,
                       duration_ms=(finished - (_epoch(job["created_at"]) or start)) * 1000,
                       error=self._error(failure, outcome) if outcome != "completed" else None)
        return finished

    @staticmethod
    def _error(failure, outcome):
        if not failure:
            return None
        return {"name": failure["error_class"], "code": failure["error_code"], "message": failure["message"],
                "part": failure["component"], "error_type": failure.get("error_type"), "fault": FAULTS.get(outcome)}

    def _keep_result(self, ve, job, at) -> None:
        fname = job["function_name"]
        value = {"train_model": {"loss": round(self._rng.uniform(0.01, 0.3), 4), "rows": 5321,
                                 "model": "model.json", "history": [round(0.8 ** i, 3) for i in range(12)]},
                 "predict": {"score": round(self._rng.random(), 3), "label": self._rng.choice(["yes", "no"])}
                 }.get(fname, {"rows": self._rng.randrange(100, 9000), "ok": True})
        ve["results"][job["job_id"]] = value
        choice = job.get("keep_result") or "7d"
        size = len(json.dumps(value))
        seconds = KEEP.get(choice)
        finished = _epoch(job["finished_at"]) or at
        job.update(keep_result=choice, keep_until=None if seconds is None else _iso(finished + seconds),
                   result={"stored": True, "bucket": "axo/ve-results", "name": f"{job['job_id']}.json",
                           "version": 1, "size": size, "saved_at": job["finished_at"], "deleted_at": None})
        if seconds is not None and finished + seconds < time.time():
            ve["results"].pop(job["job_id"], None)
            job["result"]["deleted_at"] = _iso(finished + seconds)

    def _new_job(self, ve, ve_id, fid, v, params, who, started_by_type, at, keep=None, attempts=None):
        job = self._job_doc(ve, fid, v, params, who, started_by_type, at, keep=keep, attempts=attempts)
        fail = params.get("dataset") == "missing.csv" or params.get("fail") is True
        job["_plan"] = {"fail": fail, "phase_at": at, "run_ms": self._rng.uniform(2500, 9000)}
        return job

    def _advance(self, ve, ve_id) -> None:
        now = time.time()
        for job in ve["jobs"].values():
            plan = job.get("_plan")
            if not plan or job["status"] not in ACTIVE:
                continue
            if job["status"] == "QUEUED" and now - plan["phase_at"] > 1.5:
                job.update(status="RUNNING", started_at=_iso(plan["phase_at"] + 1.5))
                plan["phase_at"] += 1.5
            elif job["status"] == "RUNNING" and now - plan["phase_at"] > plan["run_ms"] / 1000:
                n = job["attempt"]
                more = plan["fail"] and n < job["max_attempts"]
                cfg = job["_attempts_cfg"] or {}
                delay = float(cfg.get("retry_delay_seconds") or 2.0)
                if (cfg.get("retry_backoff") == "exponential"):
                    delay *= 2 ** (n - 1)
                start = _epoch(job["created_at"]) if n == 1 else plan["phase_at"] - 1.5
                done = self._finish_attempt(ve, ve_id, job, n, "failed_user" if plan["fail"] else "completed",
                                            start, plan["run_ms"], plan.get("reason", "first"),
                                            retry_at=(plan["phase_at"] + plan["run_ms"] / 1000 + delay) if more else None)
                if more:
                    plan.update(phase_at=done + delay, reason="code_failed")
                elif job["status"] == "COMPLETED":
                    self._keep_result(ve, job, now)
            elif job["status"] == "WAITING_RETRY" and now > plan["phase_at"]:
                job.update(status="QUEUED", attempt=job["attempt"] + 1, next_attempt_at=None)
                plan["phase_at"] = now - 1.0

    # ── views ────────────────────────────────────────────────────────────────
    @staticmethod
    def _public(job) -> Dict[str, Any]:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    @staticmethod
    def _default_of(ve, fid) -> Optional[int]:
        live = [d["version"] for (f, _), d in ve["functions"].items() if f == fid and not d["deleted_at"]]
        if not live:
            return None
        chosen = ve["defaults"].get(fid)
        return chosen if chosen in live else max(live)

    def _function_view(self, ve, doc, role) -> Dict[str, Any]:
        out = json.loads(json.dumps(doc))
        out["is_default"] = self._default_of(ve, doc["function_id"]) == doc["version"]
        spec = out.get("runtime_spec") or {}
        secret = set(spec.get("secret_env") or [])
        dev = role in ("developer", "admin", "owner", None, "")
        spec["env_vars"] = {k: (v if dev and k not in secret else None) for k, v in (spec.get("env_vars") or {}).items()}
        return out

    def _match(self, job, q) -> bool:
        def split(name):
            return [x for x in (q.get(name) or "").split(",") if x]
        if q.get("function_id") and job["function_id"] != q["function_id"]:
            return False
        if q.get("version") and str(job["function_version"]) != q["version"]:
            return False
        if split("status") and job["status"] not in split("status"):
            return False
        if split("outcome") and job["outcome"] not in split("outcome"):
            return False
        if q.get("started_by_type") and job["started_by_type"] != q["started_by_type"]:
            return False
        if q.get("started_by_id") and job["started_by_id"] != q["started_by_id"]:
            return False
        if q.get("error") and (job["error"] or {}).get("name") != q["error"]:
            return False
        if q.get("job_id") and not job["job_id"].startswith(q["job_id"]):
            return False
        if q.get("endpoint_id") and job["endpoint_id"] != q["endpoint_id"]:
            return False
        if q.get("since") and job["created_at"] < q["since"]:
            return False
        if q.get("until") and job["created_at"] >= q["until"]:
            return False
        if q.get("run_id") and q["run_id"] not in ((job["choreography"] or {}).get("run_id"),
                                                    (job["choreography"] or {}).get("root_run_id")):
            return False
        return True

    def _page(self, ve, q) -> Dict[str, Any]:
        jobs = [j for j in ve["jobs"].values() if self._match(j, q)]
        rows: Dict[str, Dict[str, Any]] = {}
        for job in jobs:
            # a called run's jobs go in the row of the outermost run (like the VEM)
            run_id = (job["choreography"] or {}).get("root_run_id") or (job["choreography"] or {}).get("run_id")
            key = f"run:{run_id}" if run_id else job["job_id"]
            row = rows.setdefault(key, {"row_id": key, "row_at": job["created_at"], "jobs": [], "run_id": run_id})
            row["jobs"].append(job)
            if run_id:
                row["row_at"] = (ve["runs"].get(run_id) or {}).get("queued_at") or min(row["row_at"], job["created_at"])
        ordered = sorted(rows.values(), key=lambda r: (r["row_at"], r["row_id"]), reverse=True)
        if q.get("before"):
            last = (q["before"], q.get("before_id") or "")
            ordered = [r for r in ordered if (r["row_at"], r["row_id"]) < last]
        limit = int(q.get("limit") or 50)
        page, more = ordered[:limit], len(ordered) > limit
        out = []
        for r in page:
            if r["run_id"]:
                run = ve["runs"].get(r["run_id"]) or {"run_id": r["run_id"]}
                steps = sorted(r["jobs"], key=lambda j: j["created_at"])
                out.append({"type": "run", "row_id": r["row_id"], "row_at": r["row_at"], "run": run,
                            "jobs": [self._public(j) for j in steps], "shown": len(steps),
                            "steps": run.get("steps_total")})
            else:
                out.append({"type": "job", "row_id": r["row_id"], "row_at": r["row_at"],
                            "job": self._public(r["jobs"][0])})
        last = page[-1] if page else None
        return {"rows": out, "next": {"before": last["row_at"], "before_id": last["row_id"]} if more and last else None}

    # ── routes ───────────────────────────────────────────────────────────────
    def function_history(self, ve_id: str, functions: List[tuple], endpoints: List[str], owner: str,
                         fid: str) -> List[Dict[str, Any]]:
        """A function's history rows (registered / changed / deleted versions,
        its finished jobs), shaped like the VEM's unified_activity."""
        ve = self._ve(ve_id, functions, endpoints, owner)
        rows: List[Dict[str, Any]] = []

        def add(kind: str, at: Optional[str], user: Optional[str], meta: Dict[str, Any], key: str) -> None:
            if at:
                rows.append({"event_id": hashlib.sha1(key.encode()).hexdigest()[:16], "created_at": at,
                             "endpoint_id": meta.pop("endpoint_id", None), "user_id": user, "event_type": kind,
                             "area": "job" if kind.startswith("Job") else "function",
                             "meta": {"function_id": fid, **meta}})
        for (f, v), d in ve["functions"].items():
            if f != fid:
                continue
            add("FunctionRegistered", d["registered_at"], d["registered_by"], {"name": d["name"], "version": v}, f"r{v}")
            add("FunctionDeleted", d["deleted_at"], None, {"name": d["name"], "version": v}, f"d{v}")
        for j in ve["jobs"].values():
            if j["function_id"] != fid or j["status"] not in ("COMPLETED", "FAILED"):
                continue
            meta = {"job_id": j["job_id"], "version": j["function_version"], "endpoint_id": j["endpoint_id"],
                    "outcome": j.get("outcome"), "duration_ms": j.get("duration_ms")}
            if j["status"] == "FAILED":
                meta["error"] = (j.get("error") or {}).get("message")
            add("JobCompleted" if j["status"] == "COMPLETED" else "JobFailed", j["finished_at"], j.get("started_by_id"),
                meta, j["job_id"])
        return rows

    def route(self, ve_id: str, functions: List[tuple], endpoints: List[str], owner: str, request, body,
              who: str, role: str, ok, err):
        """The response for a function / job route, or None (not one of them)."""
        method, path, q = request.method, request.url.path, request.url.params
        if not (path.startswith("/functions") or path.startswith("/jobs") or path == "/ve/results/shortened"):
            return None
        ve = self._ve(ve_id, functions, endpoints, owner)
        dev = role in ("developer", "admin", "owner", None, "")
        admin = role in ("admin", "owner", None, "")

        if method == "GET" and path == "/functions":
            return ok([self._function_view(ve, d, role) for d in ve["functions"].values()])
        if method == "POST" and path == "/functions/check":
            out = _schema(body.get("source") or "", body.get("name") or "")
            same = [d["version"] for d in ve["functions"].values()
                    if d["function_id"] == body.get("function_id") and d["code_hash"] == out["code_hash"]
                    and not d["deleted_at"]]
            return ok({**out, "existing_version": max(same) if same else None})
        if method == "POST" and path == "/functions":
            answer = self._register(ve, ve_id, request, who, ok, err)
            body_out = answer.json() if answer.status_code < 300 else {}
            if body_out.get("function_id") and not body_out.get("existing"):
                ve["defaults"].pop(body_out["function_id"], None)      # a new version becomes the default
            return answer
        default = re.match(r"^/functions/([^/]+)/default$", path)
        if method == "PUT" and default:
            if not dev:
                return err(403, "VEM_FORBIDDEN", 9602, "Developers and above pick the default version")
            doc = ve["functions"].get((default.group(1), int(body.get("version") or 0)))
            if doc is None or doc["deleted_at"]:
                return err(404, "VEM_NOT_FOUND", 9601, "function version not found")
            ve["defaults"][default.group(1)] = doc["version"]
            return ok({"function_id": default.group(1), "default_version": doc["version"]})
        m = re.match(r"^/functions/([^/]+)(?:/(\d+))?(/code|/jobs|/purge)?$", path)
        if m:
            fid, version, action = m.group(1), m.group(2), m.group(3)
            docs = [d for (f, _), d in ve["functions"].items() if f == fid]
            if not docs:
                return err(404, "VEM_NOT_FOUND", 9601, "function not found")
            if version is None and method == "GET" and action is None:
                return ok([self._function_view(ve, d, role) for d in sorted(docs, key=lambda d: d["version"])])
            if version is None and method == "GET" and action == "/jobs":
                return ok([self._public(j) for j in ve["jobs"].values() if j["function_id"] == fid])
            doc = ve["functions"].get((fid, int(version or 0)))
            if doc is None:
                return err(404, "VEM_NOT_FOUND", 9601, "function version not found")
            if method == "GET" and action == "/code":
                return ok({"function_id": fid, "version": doc["version"], "name": doc["name"],
                           "code_format": doc["code_format"], "code_hash": doc["code_hash"],
                           "text": ve["code"][(fid, doc["version"])], "downloadable": doc["code_format"] == "source",
                           "runtime_spec": self._function_view(ve, doc, role)["runtime_spec"],
                           "registered_by": doc["registered_by"], "registered_by_name": doc["registered_by_name"],
                           "registered_at": doc["registered_at"]})
            if method == "POST" and action == "/jobs":
                if doc["deleted_at"]:
                    return err(404, "VEM_NOT_FOUND", 9601, "the function version was deleted")
                attempts = {k: body.get(k) for k in ("max_attempts", "retry_delay_seconds", "retry_backoff")
                            if body.get(k) is not None}
                job = self._new_job(ve, ve_id, fid, doc["version"], body.get("params") or {}, who,
                                    "api_key" if request.headers.get("X-Acting-Key") else "user", time.time(),
                                    keep=body.get("keep_result"), attempts=attempts)
                return ok({"job_id": job["job_id"], "endpoint_id": job["endpoint_id"], "status": "QUEUED"}, 202)
            if method == "PATCH" and action is None:
                return self._update(ve, doc, body, ok, err)
            if method == "DELETE":
                if not (admin or doc["registered_by"] == who):
                    return err(403, "VEM_FORBIDDEN", 9602,
                               "only an Admin, or the Developer who registered this version, can delete it")
                if action == "/purge":
                    ve["functions"].pop((fid, doc["version"]))
                    return ok({"function_id": fid, "version": doc["version"], "purged": True})
                doc["deleted_at"] = _iso(time.time())
                return ok({"function_id": fid, "version": doc["version"]})

        if method == "GET" and path == "/jobs":
            return ok(self._page(ve, q))
        m = re.match(r"^/jobs/([^/]+)(/result|/cancel|/retry)?$", path)
        if m:
            job = ve["jobs"].get(m.group(1))
            if job is None:
                return err(404, "VEM_NOT_FOUND", 9601, "job not found")
            action = m.group(2)
            if method == "GET" and action is None:
                attempts = ve["attempts"][job["job_id"]]
                if not dev:
                    attempts = [{**a, "failure": {**a["failure"], "traceback": None}} if a.get("failure") else a
                                for a in attempts]
                return ok({**self._public(job), "attempts": attempts})
            if method == "GET" and action == "/result":
                if job["job_id"] not in ve["results"]:
                    return err(404, "JOB_RESULT_NOT_KEPT", 9625, "the job's result is not kept (any more)")
                return ok(ve["results"][job["job_id"]])
            if method == "POST" and action == "/cancel":
                if job["status"] not in ACTIVE:
                    return err(409, "JOB_NOT_ACTIVE", 9626, "the job already finished")
                running_ms = 0.0
                if job["status"] == "RUNNING" and job["started_at"]:
                    running_ms = (time.time() - (_epoch(job["started_at"]) or time.time())) * 1000
                self._finish_attempt(ve, ve_id, job, job["attempt"], "cancelled",
                                     _epoch(job["created_at"]) or time.time(), running_ms, "first",
                                     cancelled_by=self._name_of(who))
                job.update(cancelled_by=who, _plan=None)
                return ok({"job_id": job["job_id"], "cancelled": True})
            if method == "POST" and action == "/retry":
                if job["status"] not in ("FAILED", "CANCELLED"):
                    return err(409, "JOB_NOT_RETRYABLE", 9627, "only a failed or cancelled job can be tried again")
                if job["choreography"]:
                    return err(409, "JOB_NOT_RETRYABLE", 9627,
                               "a choreography step or an object call can't be tried again on its own")
                job.update(status="QUEUED", attempt=job["attempt"] + 1, retried_by=who, outcome=None,
                           finished_at=None, error=None, cancelled_by=None,
                           _plan={"fail": False, "phase_at": time.time(), "run_ms": 3000.0, "reason": "manual"})
                return ok({"job_id": job["job_id"], "attempt": job["attempt"], "endpoint_id": job["endpoint_id"]})
        if method == "GET" and path == "/ve/results/shortened":
            seconds = KEEP.get(q.get("longest") or "never")
            n = 0 if seconds is None else sum(
                1 for j in ve["jobs"].values()
                if j.get("result") and not j["result"].get("deleted_at")
                and (j["keep_until"] is None or _epoch(j["keep_until"]) - _epoch(j["finished_at"]) > seconds))
            return ok({"longest": q.get("longest"), "shortened": n})
        return err(404, "VEM_NOT_FOUND", 9601, f"the dev VEM has no {method} {path} yet")

    def _update(self, ve, doc, body, ok, err):
        spec = dict(doc["runtime_spec"] or {})
        runtime = body.get("runtime") or {}
        if "max_attempts" in runtime and not (1 <= int(runtime["max_attempts"] or 0) <= 10):
            return err(409, "VEM_CONFLICT", 9603, "max_attempts must be between 1 and 10")
        bad = sorted(set(runtime) - {"max_attempts", "retry_delay_seconds", "retry_backoff", "keep_result",
                                     "idle_ttl_seconds", "max_concurrency"})
        if bad:
            return err(409, "VEM_CONFLICT", 9603, f"these settings can't be changed in place: {', '.join(bad)}")
        spec.update(runtime)
        if body.get("env_vars_replace"):
            secrets = body.get("secret_env") if body.get("secret_env") is not None else spec.get("secret_env") or []
            env = {k: v for k, v in (body.get("env_vars") or {}).items()}
            kept = ve["secrets"].setdefault(doc["function_id"], {})
            for name in secrets:
                if env.get(name) is not None:
                    kept[name] = env[name]
            spec["env_vars"] = {k: (None if k in secrets else v) for k, v in env.items()
                                if v is not None or k in secrets}
            spec["secret_env"] = [n for n in secrets if n in spec["env_vars"]]
        if body.get("params_schema_replace"):
            doc["params_schema"] = body.get("params_schema") or []
        doc["runtime_spec"] = spec
        return ok({"function_id": doc["function_id"], "version": doc["version"]})

    def _register(self, ve, ve_id, request, who, ok, err):
        raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)
        form: Dict[str, Any] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            form[name] = part.get_content() if part.get_filename() is None else part.get_payload(decode=True)
        name = str(form.get("name") or "").strip()
        code = form.get("code") or b""
        source = code.decode() if isinstance(code, bytes) else str(code)
        check = _schema(source, name)
        if not check["ok"]:
            problem = check["problems"][0]
            if "no longer supported" in problem["message"]:
                return err(422, "FUNCTION_STYLE_UNSUPPORTED", 9628, problem["message"])
            return err(422, "VEM_INVALID", 9605, problem["message"])
        fid = form.get("function_id") or f"fn-{name.replace('_', '-')}-{hashlib.sha1((who + name).encode()).hexdigest()[:6]}"
        versions = [d for (f, _), d in ve["functions"].items() if f == fid]
        same = [d for d in versions if d["code_hash"] == check["code_hash"] and not d["deleted_at"]]
        if same:
            top = max(same, key=lambda d: d["version"])
            return ok({"function_id": fid, "name": name, "version": top["version"], "existing": True,
                       "endpoint_id": (top["endpoint_id"] or [None])[0]}, 201)
        version = max((d["version"] for d in versions), default=0) + 1
        spec = json.loads(form.get("runtime_spec") or "{}")
        spec.setdefault("type", "process")
        spec["call_style"] = "kwargs"
        secrets = spec.get("secret_env") or []
        kept = ve["secrets"].setdefault(fid, {})
        for k in secrets:
            if (spec.get("env_vars") or {}).get(k) is not None:
                kept[k] = spec["env_vars"][k]
        spec["env_vars"] = {k: (None if k in secrets else v) for k, v in (spec.get("env_vars") or {}).items()}
        ve["functions"][(fid, version)] = {
            "function_id": fid, "version": version, "name": name, "runtime_spec": spec,
            "params_schema": check["params_schema"], "description": check["description"], "code_format": "source",
            "code_hash": check["code_hash"], "endpoint_id": list(ve["endpoints"][:1]),
            "container_status": "running" if spec["type"] == "container" else None, "deleted_at": None,
            "registered_by": who, "registered_by_name": self._name_of(who), "registered_at": _iso(time.time()),
            "has_display_source": False, "virtual_environment_id": ve_id}
        ve["code"][(fid, version)] = source
        return ok({"function_id": fid, "name": name, "version": version, "endpoint_id": (ve["endpoints"] or [None])[0]},
                  201)
