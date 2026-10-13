from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from axo_control_plane.domain.errors import DomainError
from axo_control_plane.infrastructure.api.deps import Services, authenticate

POLL_SECONDS = 2.0


def build_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def live(websocket: WebSocket):
        """The Control Plane's own live channel for the UI: new notifications
        (the bell) and changes to the VE cards. Credentials as query
        parameters (access_token + temporal_secret)."""
        svc: Services = websocket.app.state.services
        params = websocket.query_params
        try:
            who = await run_in_threadpool(authenticate, svc, params.get("access_token"),
                                          params.get("temporal_secret", ""), params.get("key", ""))
        except DomainError as err:
            await websocket.close(code=4401, reason=err.message[:120])
            return
        await websocket.accept()
        last_note = 0.0
        cards: Dict[str, Any] = {}
        try:
            while True:
                notes = await run_in_threadpool(svc.notifications.list, who.user_id, False, 50,
                                                last_note or None)
                for note in reversed(notes):
                    await websocket.send_json({"type": "notification", **note})
                    last_note = max(last_note, note["created_at"])
                if not last_note:
                    last_note = 1e-9
                ves = await run_in_threadpool(svc.ves.list_for, who)
                current = {v["ve_id"]: v for v in ves}
                for ve_id, card in current.items():
                    if cards.get(ve_id) != card:
                        await websocket.send_json({"type": "ve", "ve": card})
                for ve_id in set(cards) - set(current):
                    await websocket.send_json({"type": "ve_removed", "ve_id": ve_id})
                cards = current
                await asyncio.sleep(POLL_SECONDS)
        except (WebSocketDisconnect, RuntimeError):
            return
        except DomainError as err:
            await websocket.close(code=4403, reason=err.message[:120])

    return router
