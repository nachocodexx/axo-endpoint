"""Stand-ins for the endpoints and axo_storage in choreography tests."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from axo_vem.application.choreography.jobs import COMPLETED, FAILED, PENDING, UNKNOWN, JobStatus, StepJobs
from axo_vem.domain.errors import NotFoundError


class StepFailure(Exception):
    """Raise from a fake step to fail its job (``name`` = the error name)."""

    def __init__(self, message: str, name: Optional[str] = None) -> None:
        super().__init__(message)
        self.name = name


class FakeStepJobs(StepJobs):
    """Runs a step's job as a Python callable ``(params, event) -> output``,
    registered per function id or object name, tried up to the job's
    ``max_attempts`` like an endpoint does. ``pending_polls`` = how many
    times a job answers PENDING first. Same job id twice = one run.

    Also the kept results (the engine's ``results``): a finished job's
    output, until its id is put in ``expired``."""

    def __init__(self) -> None:
        self.handlers: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Any]] = {}
        self.calls: List[Dict[str, Any]] = []
        self.results: Dict[str, JobStatus] = {}
        self.pending_polls = 0
        self.gates: Dict[str, threading.Event] = {}     # handler name -> released when set
        self.cancelled: List[str] = []
        self.forget: set = set()                          # job ids an endpoint "lost"
        self.expired: set = set()                         # job ids whose kept result is gone
        self.tries: Dict[str, int] = {}                   # job id -> how many times its code ran
        self._polls: Dict[str, int] = {}
        self._lock = threading.Lock()

    def on(self, name: str, handler: Callable[[Dict[str, Any], Dict[str, Any]], Any]) -> None:
        self.handlers[name] = handler

    def hold(self, name: str) -> threading.Event:
        """Jobs of ``name`` stay PENDING until the returned event is set."""
        self.gates[name] = threading.Event()
        return self.gates[name]

    def _start(self, target: str, job_id: str, params, event, attempts=None, **extra) -> Dict[str, Any]:
        with self._lock:
            if job_id in self.results or any(c["job_id"] == job_id for c in self.calls):
                return {"job_id": job_id, "endpoint_id": "ep-0", "status": "DUPLICATE"}
            self.calls.append({"target": target, "job_id": job_id, "params": params, "event": event,
                               "attempts": attempts, **extra})
        most = int((attempts or {}).get("max_attempts") or 1)
        handler = self.handlers.get(target)
        if handler is None:
            raise NotFoundError(f"no {target}", error_name="FUNCTION_NOT_FOUND")
        gate = self.gates.get(target)
        if gate is not None:
            threading.Thread(target=self._run_later, args=(gate, handler, job_id, params, event, most),
                             daemon=True).start()
        else:
            self.results[job_id] = self._run(handler, job_id, params, event, most)
        return {"job_id": job_id, "endpoint_id": "ep-0"}

    def _run_later(self, gate, handler, job_id, params, event, most) -> None:
        gate.wait(10)
        self.results[job_id] = self._run(handler, job_id, params, event, most)

    def _run(self, handler, job_id, params, event, most) -> JobStatus:
        while True:
            self.tries[job_id] = self.tries.get(job_id, 0) + 1
            try:
                return JobStatus(COMPLETED, output=handler(params, event), duration_ms=1.0)
            except StepFailure as exc:
                failed = JobStatus(FAILED, error=str(exc), error_name=exc.name)
            except Exception as exc:
                failed = JobStatus(FAILED, error=f"{type(exc).__name__}: {exc}")
            if self.tries[job_id] >= most:
                return failed

    def submit_function(self, *, function_id, version, params, event, output_buckets, job_id, acting_user_id,
                        choreography_id, max_wait, step_ref=None, attempts=None, keep_result=None,
                        choreography=None):
        return self._start(function_id, job_id, params, event, attempts=attempts, version=version,
                           output_buckets=output_buckets, acting_user_id=acting_user_id,
                           started_by_id=choreography_id, step_ref=step_ref, keep_result=keep_result,
                           choreography=choreography)

    def call_object(self, *, name, method, params, event, output_buckets, job_id, acting_user_id,
                    choreography_id, max_wait, step_ref=None, keep_result=None, choreography=None):
        return self._start(f"{name}.{method or 'run'}", job_id, params, event, output_buckets=output_buckets,
                           acting_user_id=acting_user_id, started_by_id=choreography_id, step_ref=step_ref,
                           keep_result=keep_result, choreography=choreography)

    # the kept results
    def result(self, job_id: str) -> Any:
        done = self.results.get(job_id)
        if done is None or done.status != COMPLETED or job_id in self.expired:
            raise NotFoundError("the job's result is not kept", error_name="JOB_RESULT_NOT_KEPT")
        return done.output

    def kept(self, job_id: str) -> bool:
        done = self.results.get(job_id)
        return done is not None and done.status == COMPLETED and job_id not in self.expired

    def result_ref(self, job_id: str) -> Optional[Dict[str, Any]]:
        return {"bucket": "axo/ve-ve1-results", "name": f"{job_id}.json", "version": 1, "format": "json"}

    def status(self, endpoint_id: str, job_id: str) -> JobStatus:
        if job_id in self.forget:
            self.forget.discard(job_id)
            self.results.pop(job_id, None)
            self.calls = [c for c in self.calls if c["job_id"] != job_id]
            return JobStatus(UNKNOWN, error="job not found")
        with self._lock:
            seen = self._polls.get(job_id, 0)
            self._polls[job_id] = seen + 1
        if seen < self.pending_polls or job_id not in self.results:
            return JobStatus(PENDING)
        return self.results[job_id]

    def cancel(self, endpoint_id: str, job_id: str) -> None:
        self.cancelled.append(job_id)
        for gate in self.gates.values():
            gate.set()

    def targets(self) -> List[str]:
        return [c["target"] for c in self.calls]


class FakeBucketStorage:
    """``list_objects`` / ``upload`` like the VEM's BucketStorage, in memory."""

    def __init__(self) -> None:
        self.objects: Dict[str, List[Dict[str, Any]]] = {}
        self.uploads: List[Dict[str, Any]] = []
        self.fail_uploads = 0

    def put(self, bucket: str, name: str, data: bytes = b"x", format: str = "raw",
            source: Optional[str] = None) -> Dict[str, Any]:
        items = self.objects.setdefault(bucket, [])
        version = 1 + max((i["version"] for i in items if i["name"] == name), default=0)
        item = {"name": name, "version": version, "format": format, "size": len(data), "state": "complete",
                "data": data, "source": source}
        items.append(item)
        return item

    def list_objects(self, user_id: str, bucket: str) -> List[Dict[str, Any]]:
        if bucket not in self.objects:
            raise NotFoundError(f"no bucket {bucket}", error_name="BUCKET_NOT_FOUND")
        return [{k: v for k, v in i.items() if k != "data"} for i in self.objects[bucket]]

    def upload(self, user_id: str, bucket: str, key: str, data: bytes, format: str = "raw", version=None,
               source=None):
        if self.fail_uploads:
            self.fail_uploads -= 1
            from axo_vem.domain.errors import UpstreamTimeoutError
            raise UpstreamTimeoutError("storage is down")
        item = self.put(bucket, key, data, format, source=source)
        self.uploads.append({"user_id": user_id, "bucket": bucket, "name": key, "version": item["version"],
                             "data": data, "source": source})
        return {k: v for k, v in item.items() if k != "data"}
