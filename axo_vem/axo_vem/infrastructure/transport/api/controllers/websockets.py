from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from axo_vem.infrastructure.auth.service_token import token_matches
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster


def build_router(broadcaster: Broadcaster, service_token: str) -> APIRouter:
    """Live feeds for the Control Plane, which opens one WebSocket per topic
    to each VEM and relays to browsers. A handshake can't carry headers, so
    the CP's service token comes as ``?token=...``; anything else is closed
    with 1008 (policy violation) before being accepted."""
    router = APIRouter()

    async def _serve(websocket: WebSocket, topic: str, token: Optional[str]) -> None:
        if not token_matches(service_token, token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        broadcaster.register(topic, websocket)
        try:
            while True:
                # Nothing is expected from the CP -- this just blocks until
                # disconnect, keeping the registration alive exactly as long
                # as the socket.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unregister(topic, websocket)

    @router.websocket("/ws/endpoints")
    async def ws_endpoints(websocket: WebSocket, token: Optional[str] = None) -> None:
        """Every endpoint's status changes and container stats."""
        await _serve(websocket, "endpoints", token)

    @router.websocket("/ws/endpoints/{endpoint_id}")
    async def ws_endpoint_scoped(websocket: WebSocket, endpoint_id: str, token: Optional[str] = None) -> None:
        """Same feed, pre-filtered to one endpoint."""
        await _serve(websocket, f"endpoints:{endpoint_id}", token)

    @router.websocket("/ws/functions")
    async def ws_functions(websocket: WebSocket, token: Optional[str] = None) -> None:
        """Every function lifecycle change (registered ... deleted)."""
        await _serve(websocket, "functions", token)

    @router.websocket("/ws/choreographies/{run_id}")
    async def ws_choreography_run(websocket: WebSocket, run_id: str, token: Optional[str] = None) -> None:
        """Live per-node/per-edge status for one choreography run."""
        await _serve(websocket, f"choreographies:{run_id}", token)

    return router
