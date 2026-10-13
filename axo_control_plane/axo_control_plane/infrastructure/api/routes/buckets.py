from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from axo_control_plane.application.people import Actor
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import InvalidError
from axo_control_plane.infrastructure.api.deps import Services, actor, actor_from_request, services

MAX_PAGE_UPLOAD_BYTES = 256 * 1024 * 1024     # bigger objects: dataio.write_chunks from a job


class BucketBody(BaseModel):
    name: str
    owner: Optional[str] = None               # your name (default) or a team you manage
    quota_bytes: Optional[int] = None


class QuotaBody(BaseModel):
    quota_bytes: int


class AliasBody(BaseModel):
    alias: str                                # owner/name


class AliasMoveBody(BaseModel):
    target: str                               # owner/name of the bucket it should point at


class ShareBody(BaseModel):
    who: str
    role: str


def build_router() -> APIRouter:
    router = APIRouter(tags=["buckets"])

    @router.get("/buckets")
    def list_buckets(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.buckets.list_for(who)

    @router.post("/buckets", status_code=201)
    def create_bucket(body: BucketBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.buckets.create(who, body.name, body.owner, body.quota_bytes)

    @router.get("/buckets/{owner}/{name}")
    def get_bucket(owner: str, name: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.buckets.get(who, f"{owner}/{name}")

    @router.patch("/buckets/{owner}/{name}")
    def set_quota(owner: str, name: str, body: QuotaBody, who: Actor = Depends(actor),
                  svc: Services = Depends(services)):
        return svc.buckets.set_quota(who, f"{owner}/{name}", body.quota_bytes)

    @router.delete("/buckets/{owner}/{name}", status_code=204)
    def delete_bucket(owner: str, name: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.buckets.delete(who, f"{owner}/{name}")

    @router.post("/buckets/{owner}/{name}/aliases", status_code=201)
    def add_alias(owner: str, name: str, body: AliasBody, who: Actor = Depends(actor),
                  svc: Services = Depends(services)):
        return svc.buckets.add_alias(who, f"{owner}/{name}", body.alias)

    @router.put("/aliases/{owner}/{name}")
    def move_alias(owner: str, name: str, body: AliasMoveBody, who: Actor = Depends(actor),
                   svc: Services = Depends(services)):
        return svc.buckets.move_alias(who, f"{owner}/{name}", body.target)

    @router.delete("/aliases/{owner}/{name}", status_code=204)
    def remove_alias(owner: str, name: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.buckets.remove_alias(who, f"{owner}/{name}")

    @router.get("/buckets/{owner}/{name}/access")
    def access(owner: str, name: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        doc = svc.buckets.resolve(f"{owner}/{name}")
        return {"access": svc.buckets.access_list(who, f"{owner}/{name}"),
                "invitations": svc.sharing.outgoing("bucket", doc["_id"])}

    @router.post("/buckets/{owner}/{name}/invitations", status_code=201)
    def share(owner: str, name: str, body: ShareBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.invite_to_bucket(who, f"{owner}/{name}", body.who, body.role)

    @router.delete("/buckets/{owner}/{name}/access/{principal_id}", status_code=204)
    def unshare(owner: str, name: str, principal_id: str, principal_type: str = "USER",
                who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.buckets.unshare(who, f"{owner}/{name}", principal_id, principal_type)

    # ── objects (the bucket pages) ───────────────────────────────────────────
    def _bucket(svc: Services, who: Actor, owner: str, name: str, role: str):
        return svc.buckets.require(who, f"{owner}/{name}", role)

    @router.get("/buckets/{owner}/{name}/objects")
    def list_objects(owner: str, name: str, prefix: Optional[str] = None, who: Actor = Depends(actor),
                     svc: Services = Depends(services)):
        doc = _bucket(svc, who, owner, name, R.READER)
        return svc.object_store.list_objects(who.user_id, doc["_id"], prefix)

    @router.put("/buckets/{owner}/{name}/objects/{object_name:path}", status_code=201)
    async def upload(owner: str, name: str, object_name: str, request: Request):
        """The body is the object's bytes (one new version). Large objects
        belong in jobs (dataio.write_chunks)."""
        svc: Services = request.app.state.services
        who = await run_in_threadpool(actor_from_request, svc, request)
        doc = await run_in_threadpool(_bucket, svc, who, owner, name, R.WRITER)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_PAGE_UPLOAD_BYTES:
                raise InvalidError(f"uploads from the page are limited to {MAX_PAGE_UPLOAD_BYTES} bytes")
        return await run_in_threadpool(svc.object_store.upload, who.user_id, doc["_id"], object_name, bytes(data))

    @router.get("/buckets/{owner}/{name}/objects/{object_name:path}")
    def download(owner: str, name: str, object_name: str, version: Optional[int] = None,
                 who: Actor = Depends(actor), svc: Services = Depends(services)):
        doc = _bucket(svc, who, owner, name, R.READER)
        info = svc.object_store.object_info(who.user_id, doc["_id"], object_name, version)
        filename = object_name.rsplit("/", 1)[-1]
        return StreamingResponse(svc.object_store.download(who.user_id, doc["_id"], object_name, info["version"]),
                                 media_type="application/octet-stream",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                          "X-Object-Version": str(info["version"])})

    @router.delete("/buckets/{owner}/{name}/objects/{object_name:path}")
    def delete_object(owner: str, name: str, object_name: str, version: Optional[int] = None,
                      who: Actor = Depends(actor), svc: Services = Depends(services)):
        doc = _bucket(svc, who, owner, name, R.WRITER)
        return svc.object_store.delete_object(who.user_id, doc["_id"], object_name, version)

    return router
