from __future__ import annotations

import mimetypes
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from axo_control_plane.application.bucket_files import PIECE_BYTES
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


class RoleBody(BaseModel):
    role: str                                 # reader | writer
    principal_type: str = "USER"


class UploadBody(BaseModel):
    name: str                                 # the file's path in the bucket
    size: int


class FileRef(BaseModel):
    name: str
    version: Optional[int] = None             # None = newest complete


class DownloadBody(BaseModel):
    files: List[FileRef] = []
    folders: List[str] = []                   # prefixes ending with /
    base: str = ""                            # the folder the person is in: paths in a zip start there


def build_router() -> APIRouter:
    router = APIRouter(tags=["buckets"])

    @router.get("/buckets")
    def list_buckets(deleted: bool = False, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Every bucket you reach (``deleted=true``: your deleted ones you can
        still restore)."""
        return svc.buckets.list_deleted(who) if deleted else svc.buckets.list_for(who)

    @router.post("/deleted-buckets/{bucket_id}/restore")
    def restore_bucket(bucket_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.buckets.restore(who, bucket_id)

    @router.delete("/deleted-buckets/{bucket_id}", status_code=204)
    def delete_bucket_permanently(bucket_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.buckets.delete_permanently(who, bucket_id)

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
        access = svc.sharing.bucket_access(who, f"{owner}/{name}")
        mine = svc.buckets.get(who, f"{owner}/{name}")["my_role"]
        return {"access": access, "invitations": svc.sharing.outgoing("bucket", doc["_id"]) if mine == R.OWNER else []}

    @router.put("/buckets/{owner}/{name}/access/{principal_id}", status_code=204)
    def set_role(owner: str, name: str, principal_id: str, body: RoleBody, who: Actor = Depends(actor),
                 svc: Services = Depends(services)):
        svc.sharing.set_bucket_role(who, f"{owner}/{name}", principal_id, body.principal_type, body.role)

    @router.post("/buckets/{owner}/{name}/invitations", status_code=201)
    def share(owner: str, name: str, body: ShareBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.invite_to_bucket(who, f"{owner}/{name}", body.who, body.role)

    @router.delete("/buckets/{owner}/{name}/access/{principal_id}", status_code=204)
    def unshare(owner: str, name: str, principal_id: str, principal_type: str = "USER",
                who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.buckets.unshare(who, f"{owner}/{name}", principal_id, principal_type)

    # ── files (the bucket pages) ─────────────────────────────────────────────
    def _bucket(svc: Services, who: Actor, owner: str, name: str, role: str):
        return svc.buckets.require(who, f"{owner}/{name}", role)

    @router.get("/buckets/{owner}/{name}/objects")
    def list_files(owner: str, name: str, prefix: str = "", after: Optional[str] = None, limit: int = 200,
                   who: Actor = Depends(actor), svc: Services = Depends(services)):
        """A page of the files in one folder (``prefix``), folders first; each
        file's newest version, how many it has and who wrote it."""
        return svc.bucket_files.list(who, f"{owner}/{name}", prefix, after, limit)

    @router.get("/buckets/{owner}/{name}/versions/{object_name:path}")
    def versions(owner: str, name: str, object_name: str, who: Actor = Depends(actor),
                 svc: Services = Depends(services)):
        return svc.bucket_files.versions(who, f"{owner}/{name}", object_name)

    @router.put("/buckets/{owner}/{name}/objects/{object_name:path}", status_code=201)
    async def upload(owner: str, name: str, object_name: str, request: Request):
        """The body is the object's bytes (one new version), for scripts and
        small files. The page uploads in pieces (``/uploads``); large objects
        belong in jobs (dataio.write_chunks)."""
        svc: Services = request.app.state.services
        who = await run_in_threadpool(actor_from_request, svc, request)
        doc = await run_in_threadpool(_bucket, svc, who, owner, name, R.WRITER)
        data = await _read_body(request, MAX_PAGE_UPLOAD_BYTES)
        return await run_in_threadpool(svc.object_store.upload, who.user_id, doc["_id"], object_name, data)

    @router.post("/buckets/{owner}/{name}/uploads", status_code=201)
    def start_upload(owner: str, name: str, body: UploadBody, who: Actor = Depends(actor),
                     svc: Services = Depends(services)):
        """Starts a new version; the browser then sends ``pieces`` pieces of
        ``piece_bytes`` (the last one shorter), in any order, and retries any
        that fail."""
        return svc.bucket_files.start_upload(who, f"{owner}/{name}", body.name, body.size)

    @router.put("/buckets/{owner}/{name}/uploads/{version}/pieces/{index}")
    async def put_piece(owner: str, name: str, version: int, index: int, file: str, request: Request):
        svc: Services = request.app.state.services
        who = await run_in_threadpool(actor_from_request, svc, request)
        data = await _read_body(request, PIECE_BYTES)
        return await run_in_threadpool(svc.bucket_files.put_piece, who, f"{owner}/{name}", file, version, index, data)

    @router.delete("/buckets/{owner}/{name}/uploads/{version}", status_code=204)
    def cancel_upload(owner: str, name: str, version: int, file: str, who: Actor = Depends(actor),
                      svc: Services = Depends(services)):
        svc.bucket_files.cancel_upload(who, f"{owner}/{name}", file, version)

    @router.get("/buckets/{owner}/{name}/objects/{object_name:path}")
    def download(owner: str, name: str, object_name: str, version: Optional[int] = None, inline: bool = False,
                 who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The object's bytes; ``inline=true`` (the preview) sends its type
        guessed from the name and lets the browser show it (a read, billed
        like a download). Big files: ask for a download link instead."""
        doc = _bucket(svc, who, owner, name, R.READER)
        info = svc.object_store.object_info(who.user_id, doc["_id"], object_name, version)
        filename = object_name.rsplit("/", 1)[-1]
        media_type = (mimetypes.guess_type(filename)[0] or "application/octet-stream") if inline \
            else "application/octet-stream"
        return StreamingResponse(svc.object_store.download(who.user_id, doc["_id"], object_name, info["version"]),
                                 media_type=media_type,
                                 headers={"Content-Disposition": _disposition("inline" if inline else "attachment", filename),
                                          "X-Object-Version": str(info["version"]),
                                          "X-Object-Size": str(info.get("total_size") or "")})

    @router.delete("/buckets/{owner}/{name}/objects/{object_name:path}")
    def delete_object(owner: str, name: str, object_name: str, version: Optional[int] = None,
                      who: Actor = Depends(actor), svc: Services = Depends(services)):
        """One version, or the whole file (every version) without ``version``."""
        return svc.bucket_files.delete(who, f"{owner}/{name}", object_name, version)

    @router.post("/buckets/{owner}/{name}/downloads", status_code=201)
    def download_link(owner: str, name: str, body: DownloadBody, who: Actor = Depends(actor),
                      svc: Services = Depends(services)):
        """A link the browser opens itself, valid a few minutes: one file, or
        several files and folders as one zip (up to 2 GB)."""
        return svc.bucket_files.download_link(who, f"{owner}/{name}", [f.model_dump() for f in body.files],
                                              body.folders, body.base)

    @router.get("/downloads/{token}")
    def open_download(token: str, svc: Services = Depends(services)):
        """No sign-in: the link itself, for its few minutes, is the permission
        (it was made for one person, after checking their role)."""
        filename, media_type, size, body = svc.bucket_files.open_link(token)
        headers = {"Content-Disposition": _disposition("attachment", filename), "Cache-Control": "no-store"}
        if size is not None:
            headers["Content-Length"] = str(size)
        return StreamingResponse(body, media_type=media_type, headers=headers)

    return router


async def _read_body(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise InvalidError(f"at most {limit} bytes at a time")
    return bytes(data)


def _disposition(kind: str, filename: str) -> str:
    """Content-Disposition that survives any file name (RFC 6266)."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("?", "_")
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
