from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from axo_control_plane.application.front_door import match
from axo_control_plane.application.people import Actor
from axo_control_plane.application.ve_service import PurchaseRequest
from axo_control_plane.domain.errors import DomainError
from axo_control_plane.infrastructure.api.deps import Services, actor, actor_from_request, authenticate, services

# Response headers passed back from a VEM (everything else is dropped).
# (content-length is recomputed: the body is passed on decoded.)
_PASS_HEADERS = ("content-type", "content-disposition", "retry-after")


class BuyBody(BaseModel):
    name: str
    plan_id: str
    quota_cpu_cores: float = 1.0
    quota_memory_mb: int = 1024
    endpoint_size: str = "small"
    endpoint_cpu_cores: Optional[float] = None
    endpoint_memory_mb: Optional[int] = None
    target_endpoints: int = 1
    min_endpoints: int = 1
    team_id: Optional[str] = None


class PlanChangeBody(BaseModel):
    plan_id: Optional[str] = None
    quota_cpu_cores: Optional[float] = None
    quota_memory_mb: Optional[int] = None


class VeInviteBody(BaseModel):
    who: str
    role: str


class AccessBody(BaseModel):
    principal_type: str = "USER"
    role: Optional[str] = None           # None = remove their access


def build_router() -> APIRouter:
    router = APIRouter()

    # ── plans ────────────────────────────────────────────────────────────────
    @router.get("/plans", tags=["plans"])
    def plans(svc: Services = Depends(services)):
        return {"plans": [p.to_dict() for p in svc.catalog.plans()], "prices": svc.catalog.prices().to_dict()}

    # ── VEs ──────────────────────────────────────────────────────────────────
    @router.get("/ves", tags=["virtual environments"])
    def list_ves(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.list_for(who)

    @router.post("/ves", status_code=201, tags=["virtual environments"])
    def buy(body: BuyBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Created at once (status "created") or, for a Team Admin over a
        spending limit, waiting for the Team Owner (status "pending")."""
        return svc.ves.buy(who, PurchaseRequest(**body.model_dump()))

    @router.get("/ves/{ve_id}", tags=["virtual environments"])
    def get_ve(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.get(who, ve_id)

    @router.post("/ves/{ve_id}/stop", status_code=202, tags=["virtual environments"])
    def stop(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.stop(who, ve_id)

    @router.post("/ves/{ve_id}/start", status_code=202, tags=["virtual environments"])
    def start(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.start(who, ve_id)

    @router.delete("/ves/{ve_id}", status_code=202, tags=["virtual environments"])
    def delete(ve_id: str, confirm: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """``confirm`` must be the VE's name."""
        return svc.ves.delete(who, ve_id, confirm)

    @router.patch("/ves/{ve_id}/plan", tags=["virtual environments"])
    def change_plan(ve_id: str, body: PlanChangeBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.change_plan(who, ve_id, body.plan_id, body.quota_cpu_cores, body.quota_memory_mb)

    @router.get("/ves/{ve_id}/access", tags=["virtual environments"])
    def access(ve_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return {"access": svc.sharing.ve_access(who, ve_id), "invitations": svc.sharing.outgoing("ve", ve_id)}

    @router.put("/ves/{ve_id}/access/{principal_id}", status_code=204, tags=["virtual environments"])
    def set_access(ve_id: str, principal_id: str, body: AccessBody, who: Actor = Depends(actor),
                   svc: Services = Depends(services)):
        svc.sharing.set_ve_role(who, ve_id, principal_id, body.principal_type, body.role)

    @router.post("/ves/{ve_id}/invitations", status_code=201, tags=["virtual environments"])
    def invite(ve_id: str, body: VeInviteBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.invite_to_ve(who, ve_id, body.who, body.role)

    # ── the front door: everything else goes to the VE's VEM ─────────────────
    @router.api_route("/ves/{ve_id}/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                      tags=["virtual environments"])
    async def pass_through(ve_id: str, path: str, request: Request):
        """Any VEM call this person's role allows (see application/front_door.py)."""
        svc: Services = request.app.state.services
        vem_path = "/" + path
        who = await run_in_threadpool(actor_from_request, svc, request)
        rule = match(request.method, vem_path)
        body: Optional[Dict[str, Any]] = None
        raw: Optional[bytes] = None
        if rule is not None and rule[1] is not None:
            raw = await request.body()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
        decision = await run_in_threadpool(svc.front_door.decide, who, ve_id, request.method, vem_path, body)
        if decision.action == "answer":
            return JSONResponse(decision.body, status_code=decision.status_code, headers=decision.headers)
        client: httpx.AsyncClient = request.app.state.http
        url = f"{svc.vem_base_url(ve_id)}{vem_path}"
        headers = {**svc.vem_headers(ve_id, who.user_id)}
        if request.headers.get("content-type"):
            headers["Content-Type"] = request.headers["content-type"]
        content = raw if raw is not None else request.stream()
        try:
            upstream = await client.send(client.build_request(request.method, url, params=request.query_params,
                                                              headers=headers, content=content), stream=True)
        except httpx.HTTPError:
            return JSONResponse({"error": "UNAVAILABLE", "message": "the VE manager can't be reached right now"},
                                status_code=503, headers={"Retry-After": "5"})
        passed = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_HEADERS}

        async def body_iter():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=passed)

    @router.websocket("/ves/{ve_id}/ws/{path:path}")
    async def live_updates(websocket: WebSocket, ve_id: str, path: str):
        """The VEM's live updates, relayed after the same role check. Browsers
        can't set headers on a WebSocket, so credentials come as query
        parameters (access_token + temporal_secret, or key)."""
        svc: Services = websocket.app.state.services
        params = websocket.query_params
        try:
            who = await run_in_threadpool(authenticate, svc, params.get("access_token"),
                                          params.get("temporal_secret", ""), params.get("key", ""))
            await run_in_threadpool(svc.front_door.check_ws, who, ve_id, "/ws/" + path)
        except DomainError as err:
            await websocket.close(code=4403 if err.status_code == 403 else 4401, reason=err.message[:120])
            return
        await websocket.accept()
        import websockets   # local: only needed when relaying

        try:
            async with websockets.connect(svc.vem_ws_url(ve_id, "/ws/" + path), open_timeout=10) as upstream:
                async def down():
                    async for message in upstream:
                        await websocket.send_text(message if isinstance(message, str) else message.decode())

                async def up():
                    while True:
                        await upstream.send(await websocket.receive_text())

                done, pending = await asyncio.wait([asyncio.create_task(down()), asyncio.create_task(up())],
                                                   return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
        except (WebSocketDisconnect, OSError, Exception):
            pass
        finally:
            try:
                await websocket.close()
            except RuntimeError:
                pass

    return router

