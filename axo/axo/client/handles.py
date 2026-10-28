from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from option import Err, Ok, Result

from axo.errors import AxoError, AxoJobFailedError, AxoRunFailedError, AxoTimeoutError

if TYPE_CHECKING:
    from axo.client.client import AxoClient

FINISHED = ("COMPLETED", "FAILED", "CANCELLED")


class JobHandle:
    """A submitted job. ``result()`` waits for it; ``cancel()`` stops it."""

    def __init__(self, client: "AxoClient", job_id: str, endpoint_id: Optional[str] = None) -> None:
        self._client = client
        self.job_id = job_id
        self.endpoint_id = endpoint_id

    @classmethod
    def from_answer(cls, client: "AxoClient", body: Dict[str, Any]) -> "JobHandle":
        return cls(client, str(body["job_id"]), body.get("endpoint_id"))

    def status(self) -> Result[Dict[str, Any], AxoError]:
        """The job with every attempt: ``status`` QUEUED / RUNNING /
        WAITING_RETRY / COMPLETED / FAILED / CANCELLED, ``attempt`` of
        ``max_attempts``, the last ``error``."""
        got = self._client.api("GET", f"/jobs/{self.job_id}")
        if got.is_ok:
            self.endpoint_id = got.unwrap().get("endpoint_id") or self.endpoint_id
        return got

    def result(self, timeout: float = 300.0, poll_seconds: float = 0.25,
               sleep: Callable[[float], None] = time.sleep) -> Result[Any, AxoError]:
        """The value the function or method returned, once it finished."""
        deadline = time.monotonic() + timeout
        delay = poll_seconds
        while True:
            got = self.status()
            if got.is_err and getattr(got.unwrap_err(), "name", "") not in _NOT_THERE_YET:
                return got
            if got.is_ok and got.unwrap().get("status") in FINISHED:
                job = got.unwrap()
                if job["status"] != "COMPLETED":
                    return Err(_failed(self.job_id, job))
                return self._client.api("GET", f"/jobs/{self.job_id}/result")
            done = self._from_endpoint()
            if done is not None:
                return done
            if time.monotonic() >= deadline:
                return Err(AxoTimeoutError(f"Job {self.job_id} didn't finish within {timeout}s",
                                           {"job_id": self.job_id}))
            sleep(delay)
            delay = min(delay * 1.5, 2.0)

    def _from_endpoint(self) -> Optional[Result[Any, AxoError]]:
        """The job record follows the endpoint's events a moment later; the
        endpoint that ran it knows first (it keeps a finished job an hour)."""
        if self.endpoint_id is None:
            return None
        got = self._client.api("GET", f"/endpoints/{self.endpoint_id}/jobs/{self.job_id}")
        if got.is_err:
            return None
        body = got.unwrap()
        if body.get("status") == "COMPLETED" and body.get("result_ok") is not False:
            return Ok((body.get("output") or {}).get("value"))
        if body.get("status") in ("FAILED", "COMPLETED"):
            return Err(AxoJobFailedError(str(body.get("error") or "the job failed"),
                                         {"job_id": self.job_id, "status": "FAILED"}))
        return None

    def cancel(self) -> Result[Dict[str, Any], AxoError]:
        """Stops it: a queued job never runs, a running one stops (billed for
        the time it ran), one waiting to retry tries no more."""
        return self._client.api("POST", f"/jobs/{self.job_id}/cancel")

    def __repr__(self) -> str:
        return f"<job {self.job_id}>"


# A job saved while the VE sleeps is known once the VE is awake again.
_NOT_THERE_YET = ("JOB_NOT_FOUND", "VEM_NOT_FOUND")


def _failed(job_id: str, job: Dict[str, Any]) -> AxoJobFailedError:
    error = job.get("error") or {}
    message = error.get("message") or ("the job was cancelled" if job.get("status") == "CANCELLED"
                                       else "the job failed")
    return AxoJobFailedError(message, {"job_id": job_id, "status": job.get("status"), "error": error.get("name"),
                                       "fault": error.get("fault"), "attempt": job.get("attempt")})


class ObjectHandle:
    """An object of the VE. ``handle.method(**params)`` (or ``call``) runs
    one method as a job; ``run`` is the default method."""

    def __init__(self, client: "AxoClient", name: str, created: Optional[JobHandle] = None) -> None:
        self._client = client
        self.name = name
        self.created = created

    def call(self, method: str = "run", max_wait: Optional[float] = None, job_id: Optional[str] = None,
             keep_result: Optional[str] = None, **params: Any) -> Result[JobHandle, AxoError]:
        body: Dict[str, Any] = {"method": method, "params": params}
        if keep_result is not None:
            body["keep_result"] = keep_result
        if max_wait is not None:
            body["max_wait"] = max_wait
        if job_id is not None:
            body["job_id"] = job_id
        return self._client.api("POST", f"/objects/{self.name}/calls", json=body).map(
            lambda b: JobHandle.from_answer(self._client, b))

    def __getattr__(self, method: str) -> Callable[..., Result[JobHandle, AxoError]]:
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda **params: self.call(method, **params)

    def info(self) -> Result[Dict[str, Any], AxoError]:
        return self._client.api("GET", f"/objects/{self.name}")

    def state(self) -> Result[Dict[str, Any], AxoError]:
        """The saved attributes (small ones in full, big ones by size)."""
        return self._client.api("GET", f"/objects/{self.name}/state")

    def history(self) -> Result[list, AxoError]:
        return self._client.api("GET", f"/objects/{self.name}/history")

    def upgrade(self, class_version: Optional[int] = None) -> Result[JobHandle, AxoError]:
        """Moves the object to a newer class version (the latest when None)."""
        return self._client.api("POST", f"/objects/{self.name}/upgrade",
                                json={"class_version": class_version}).map(
            lambda b: JobHandle.from_answer(self._client, b))

    def clone(self, name: str) -> Result["ObjectHandle", AxoError]:
        return self._client.clone(self.name, name)

    def delete(self) -> Result[None, AxoError]:
        return self._client.api("DELETE", f"/objects/{self.name}").map(lambda _: None)

    def __repr__(self) -> str:
        return f"<object {self.name}>"


RUN_FINISHED = ("completed", "completed_with_errors", "failed", "cancelled")


class RunHandle:
    """A choreography run. ``result()`` waits for it and gives the last
    steps' outputs (``{step: output}``)."""

    def __init__(self, client: "AxoClient", run_id: str, choreography: Optional[str] = None) -> None:
        self._client = client
        self.run_id = run_id
        self.choreography = choreography

    @classmethod
    def from_answer(cls, client: "AxoClient", body: Dict[str, Any]) -> "RunHandle":
        return cls(client, str(body["run_id"]), body.get("choreography_name"))

    def info(self) -> Result[Dict[str, Any], AxoError]:
        """The run with every step's status, params, job and error."""
        return self._client.api("GET", f"/choreography-runs/{self.run_id}")

    def wait(self, timeout: float = 600.0, poll_seconds: float = 0.5,
             sleep: Callable[[float], None] = time.sleep) -> Result[Dict[str, Any], AxoError]:
        """The finished run (any outcome)."""
        deadline = time.monotonic() + timeout
        delay = poll_seconds
        while True:
            got = self.info()
            if got.is_err or got.unwrap().get("status") in RUN_FINISHED:
                return got
            if time.monotonic() >= deadline:
                return Err(AxoTimeoutError(f"Run {self.run_id} didn't finish within {timeout}s",
                                           {"run_id": self.run_id}))
            sleep(delay)
            delay = min(delay * 1.5, 5.0)

    def result(self, timeout: float = 600.0, poll_seconds: float = 0.5,
               sleep: Callable[[float], None] = time.sleep) -> Result[Any, AxoError]:
        """The last steps' outputs, or an error if the run didn't complete
        (``completed_with_errors`` counts as an error too: see ``wait``)."""
        got = self.wait(timeout, poll_seconds, sleep)
        if got.is_err:
            return got
        run = got.unwrap()
        if run.get("status") != "completed":
            failed = {k: s.get("error") for k, s in (run.get("steps") or {}).items() if s.get("status") == "failed"}
            return Err(AxoRunFailedError(f"Run {self.run_id} ended {run.get('status')}",
                                         {"run_id": self.run_id, "status": run.get("status"), "failed": failed}))
        # The run keeps which job ran each step; their answers are the jobs' results.
        self.choreography = self.choreography or run.get("choreography_name")
        return self._client.api("GET", f"{self._path()}/result").map(lambda body: body.get("result"))

    def _path(self) -> str:
        return f"/choreographies/{self.choreography}/runs/{self.run_id}"

    def _named(self) -> Result[str, AxoError]:
        if self.choreography is None:
            got = self.info()
            if got.is_err:
                return got
            self.choreography = got.unwrap().get("choreography_name")
        return Ok(self._path())

    def cancel(self) -> Result[Dict[str, Any], AxoError]:
        return self._named().flatmap(lambda p: self._client.api("POST", f"{p}/cancel"))

    def resume(self) -> Result["RunHandle", AxoError]:
        """Runs again what failed, was skipped or cancelled, and what came after."""
        return self._named().flatmap(lambda p: self._client.api("POST", f"{p}/resume")).map(lambda _: self)

    def __repr__(self) -> str:
        return f"<run {self.run_id}>"
