from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import httpx

from axo_control_plane.application.ports import VemGateway
from axo_control_plane.domain.errors import ConflictError, DomainError, InvalidError, NotFoundError, UnavailableError

CP_ACTOR = "control-plane"
# How long the CP held a job submission (ms), recorded on the job's attempt
# as its "control_plane" wait (the VEM reads it, see its jobs controller).
CP_WAIT_HEADER = "X-Axo-Wait-Ms"


class HttpVemGateway(VemGateway):
    """Each VE's VEM over HTTP on the control network
    (``http://ve-<id>-vem:<port>``), with that VE's own service token."""

    def __init__(self, token_fn: Callable[[str], str], port: int = 8080, timeout_s: float = 30.0,
                 base_url_fn: Optional[Callable[[str], str]] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._token_fn = token_fn
        self._base_url_fn = base_url_fn or (lambda ve_id: f"http://ve-{ve_id}-vem:{port}")
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def base_url(self, ve_id: str) -> str:
        return self._base_url_fn(ve_id)

    def headers(self, ve_id: str, acting_user: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token_fn(ve_id)}", "X-Acting-User": acting_user}

    def _call(self, ve_id: str, method: str, path: str, acting_user: str = CP_ACTOR,
              json: Any = None, params: Optional[Dict[str, Any]] = None,
              extra_headers: Optional[Dict[str, str]] = None) -> Any:
        try:
            response = self._http.request(method, f"{self.base_url(ve_id)}{path}", json=json, params=params,
                                          headers={**self.headers(ve_id, acting_user), **(extra_headers or {})})
        except httpx.HTTPError as exc:
            raise UnavailableError(f"the VE manager of {ve_id} can't be reached ({exc.__class__.__name__})")
        if response.status_code < 400:
            return response.json() if response.content else None
        raise _error(response)

    def status(self, ve_id):
        return self._call(ve_id, "GET", "/ve")

    def stop(self, ve_id, by):
        self._call(ve_id, "POST", "/ve/stop", acting_user=by)

    def start(self, ve_id, by):
        self._call(ve_id, "POST", "/ve/start", acting_user=by)

    def prepare_delete(self, ve_id, by):
        self._call(ve_id, "POST", "/ve/prepare-delete", acting_user=by)

    def set_limits(self, ve_id, limits, by):
        self._call(ve_id, "PUT", "/ve/limits", acting_user=by, json=limits)

    def repair(self, ve_id, by, steps, automatic):
        return self._call(ve_id, "POST", "/ve/repair", acting_user=by, json={"steps": steps, "automatic": automatic})

    def endpoint_ids(self, ve_id):
        listed = self._call(ve_id, "GET", "/endpoints", params={"include_removed": True})
        return {e.get("endpoint_id") or e.get("_id"): e.get("lifecycle") or e.get("status") or "unknown"
                for e in listed or [] if e.get("endpoint_id") or e.get("_id")}

    def submit_job(self, ve_id, request):
        waited = request.get("wait_ms")
        return self._call(ve_id, "POST", request["path"], acting_user=request["acting_user"], json=request["body"],
                          extra_headers={CP_WAIT_HEADER: f"{float(waited):.3f}"} if waited is not None else None)


def _error(response: httpx.Response) -> DomainError:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    message = str(detail)[:300]
    if response.status_code == 404:
        return NotFoundError(message)
    if response.status_code == 409:
        return ConflictError(message)
    if response.status_code in (400, 422):
        return InvalidError(message)
    return UnavailableError(f"VE manager error {response.status_code}: {message}")
