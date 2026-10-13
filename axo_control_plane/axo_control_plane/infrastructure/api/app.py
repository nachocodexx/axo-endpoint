from __future__ import annotations

from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from axo_control_plane.domain.errors import DomainError
from axo_control_plane.infrastructure.api.deps import Services
from axo_control_plane.infrastructure.api.routes import admin, buckets, live, people, ves


def create_app(svc: Services, cors_origins: Optional[List[str]] = None,
               http_client: Optional[httpx.AsyncClient] = None) -> FastAPI:
    """The platform's one front door: axo-ui, axo-admin-ui and SDKs call only this."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        yield
        await app.state.http.aclose()

    app = FastAPI(title="axo Control Plane", version="0.1.0", lifespan=lifespan)
    app.state.services = svc
    if http_client is not None:
        app.state.http = http_client
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, err: DomainError):
        headers = {"Retry-After": "5"} if err.status_code == 503 else None
        return JSONResponse(err.to_dict(), status_code=err.status_code, headers=headers)

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    app.include_router(people.build_router())
    app.include_router(ves.build_router())
    app.include_router(buckets.build_router())
    app.include_router(admin.build_router())
    app.include_router(live.build_router())
    return app
