from __future__ import annotations

import hashlib
import mimetypes
import secrets
import time
import zipfile
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from pymongo.database import Database

from axo_control_plane.application.buckets import Buckets
from axo_control_plane.application.people import Actor, People
from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import InvalidError, NotFoundError

GIB = 1024 ** 3
MAX_UPLOAD_BYTES = 5 * GIB            # per file from the browser; jobs write any size
PIECE_BYTES = 8 * 1024 * 1024         # what the browser sends at a time
MAX_ZIP_BYTES = 2 * GIB
MAX_ZIP_FILES = 10_000
LINK_SECONDS = 600.0


class BucketFiles:
    """The files of a bucket as its pages show them (the person's role on
    the bucket checked first; axo_storage checks again and bills the reads
    to them):

    - a page of files under a folder (folders come from "/" in the names),
      each row saying who wrote it -- a person, a job (and so its function
      or choreography step, from the job's attempt record) or a choreography;
    - uploads in pieces: the browser sends each piece, the Control Plane
      passes it straight on to storage and keeps none of it;
    - download links: a short-lived link the browser opens itself (so a big
      file or a zip streams to disk instead of into the page's memory) for
      one file, or several files / folders as one zip built while it
      downloads.
    """

    def __init__(self, db: Database, buckets: Buckets, store: Any, people: People,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._buckets = buckets
        self._store = store
        self._people = people
        self._attempts = db["job_attempts"]
        self._links = db["download_links"]
        self._now_fn = now_fn

    # ── listing ──────────────────────────────────────────────────────────────
    def list(self, actor: Actor, full_name: str, prefix: str = "", after: Optional[str] = None,
             limit: int = 200) -> Dict[str, Any]:
        doc = self._buckets.require(actor, full_name, R.READER)
        if not 0 < limit <= 1000:
            raise InvalidError("limit is between 1 and 1000")
        page = self._store.list_files(actor.user_id, doc["_id"], prefix, after, limit)
        files = [e for e in page["entries"] if e.get("kind") == "file"]
        writers = self._writers(files)
        return {"bucket": doc["full_name"], "prefix": prefix, "next": page.get("next"),
                "entries": [_row(e, writers) for e in page["entries"]]}

    def versions(self, actor: Actor, full_name: str, name: str) -> List[Dict[str, Any]]:
        doc = self._buckets.require(actor, full_name, R.READER)
        rows = self._store.versions(actor.user_id, doc["_id"], name)
        if not rows:
            raise NotFoundError(f"no file {name} in {doc['full_name']}")
        writers = self._writers(rows)
        return [_row(r, writers) for r in sorted(rows, key=lambda r: -r["version"])]

    def _writers(self, rows: List[Dict[str, Any]]) -> Dict[Tuple[Any, Any], Dict[str, Any]]:
        """(created_by, source) -> who wrote it, looked up once per page."""
        names: Dict[Any, Optional[str]] = {}
        jobs: Dict[str, Dict[str, Any]] = {}
        job_ids = [s.split(":", 2)[2] for s in {r.get("source") or "" for r in rows} if s.startswith("job:")]
        if job_ids:
            for attempt in self._attempts.find({"job_id": {"$in": job_ids}}).sort("attempt", 1):
                jobs.setdefault(attempt["job_id"], attempt)
        out: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
        for row in rows:
            key = (row.get("created_by"), row.get("source"))
            if key in out:
                continue
            user_id, source = key
            if user_id not in names:
                names[user_id] = self._name_of(user_id)
            writer: Dict[str, Any] = {"kind": "person", "user_id": user_id, "user_name": names[user_id]}
            if source and source.startswith("job:"):
                _, ve_id, job_id = source.split(":", 2)
                writer.update(kind="job", ve_id=ve_id or None, job_id=job_id)
                attempt = jobs.get(job_id)
                if attempt is not None:
                    writer.update({k: attempt.get(k) for k in (
                        "function_id", "function_version", "object_id", "object_method",
                        "choreography_run_id", "choreography_step")})
                    writer["ve_id"] = writer["ve_id"] or attempt.get("ve_id")
            elif source and source.startswith("choreography:"):
                writer.update(kind="choreography", choreography_id=source.split(":", 1)[1])
            elif source:
                writer["source"] = source
            out[key] = writer
        return out

    def _name_of(self, user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        try:
            return self._people.get(user_id)["username"]
        except NotFoundError:
            return None

    # ── uploads in pieces ────────────────────────────────────────────────────
    def start_upload(self, actor: Actor, full_name: str, name: str, size: int) -> Dict[str, Any]:
        """A new version of ``name`` (the next one when it exists), to be sent
        in ``piece_bytes`` pieces, in any order; it completes by itself with
        the last piece. Refused at once when the bucket has no room."""
        doc = self._buckets.require(actor, full_name, R.WRITER)
        _check_name(name)
        if not isinstance(size, int) or size < 0:
            raise InvalidError("size is the file's number of bytes")
        if size > MAX_UPLOAD_BYTES:
            raise InvalidError(f"a file uploaded from the page can be at most {MAX_UPLOAD_BYTES // GIB} GB; "
                               "bigger ones are written by jobs (dataio.write_chunks)", code="UPLOAD_TOO_BIG")
        record = self._store.start_upload(actor.user_id, doc["_id"], name, size, PIECE_BYTES)
        return {"name": name, "version": record["version"], "piece_bytes": record["chunk_bytes"],
                "pieces": record["total_chunks"], "state": record["state"]}

    def put_piece(self, actor: Actor, full_name: str, name: str, version: int, index: int,
                  data: bytes) -> Dict[str, Any]:
        doc = self._buckets.require(actor, full_name, R.WRITER)
        if len(data) > PIECE_BYTES:
            raise InvalidError(f"a piece is at most {PIECE_BYTES} bytes")
        answer = self._store.put_piece(actor.user_id, doc["_id"], name, version, index, data)
        return {"name": name, "version": version, "index": index, "state": answer.get("state")}

    def cancel_upload(self, actor: Actor, full_name: str, name: str, version: int) -> None:
        """Drops a version still uploading (a finished one is deleted like any)."""
        doc = self._buckets.require(actor, full_name, R.WRITER)
        record = next((r for r in self._store.versions(actor.user_id, doc["_id"], name) if r["version"] == version), None)
        if record is None:
            raise NotFoundError(f"no upload of {name} v{version}")
        if record["state"] != "complete":
            self._store.delete_object(actor.user_id, doc["_id"], name, version)

    def delete(self, actor: Actor, full_name: str, name: str, version: Optional[int]) -> Dict[str, Any]:
        """One version, or (``version`` None) the whole file."""
        doc = self._buckets.require(actor, full_name, R.WRITER)
        return self._store.delete_object(actor.user_id, doc["_id"], name, version)

    # ── download links ───────────────────────────────────────────────────────
    def download_link(self, actor: Actor, full_name: str, files: List[Dict[str, Any]],
                      folders: List[str], base: str = "") -> Dict[str, Any]:
        """A link for one file (``files`` with one entry, no folders) or for a
        zip of several files and folders (paths inside it relative to
        ``base``, the folder the person is in). Up to 2 GB / 10,000 files."""
        doc = self._buckets.require(actor, full_name, R.READER)
        items: List[Dict[str, Any]] = []
        for f in files:
            info = self._store.object_info(actor.user_id, doc["_id"], f["name"], f.get("version"))
            items.append({"name": f["name"], "version": info["version"], "size": info.get("total_size") or 0})
        for prefix in folders:
            if not prefix.endswith("/"):
                raise InvalidError("a folder ends with /")
            items.extend(self._folder(actor, doc["_id"], prefix))
            if len(items) > MAX_ZIP_FILES:
                break
        if not items:
            raise InvalidError("nothing to download: the folders are empty")
        single = len(items) == 1 and not folders
        size = sum(i["size"] for i in items)
        if not single:
            if len(items) > MAX_ZIP_FILES:
                raise InvalidError(f"a zip holds at most {MAX_ZIP_FILES} files; download fewer at a time",
                                   code="DOWNLOAD_TOO_BIG")
            if size > MAX_ZIP_BYTES:
                raise InvalidError(f"a zip can be at most {MAX_ZIP_BYTES // GIB} GB ({size} bytes selected); "
                                   "download the files one by one", code="DOWNLOAD_TOO_BIG")
        for item in items:
            item["path"] = item["name"][len(base):] if base and item["name"].startswith(base) else item["name"]
        if single:
            filename = items[0]["name"].rsplit("/", 1)[-1]
        else:
            filename = f"{(base.rstrip('/').rsplit('/', 1)[-1] if base else doc['name']) or doc['name']}.zip"
        token = secrets.token_urlsafe(32)
        now = self._now_fn()
        self._links.insert_one({"_id": _hash(token), "user_id": actor.user_id, "bucket_id": doc["_id"],
                                "bucket": doc["full_name"], "items": items, "zip": not single,
                                "filename": filename, "created_at": now, "expires_at": now + LINK_SECONDS})
        self._links.delete_many({"expires_at": {"$lt": now}})
        return {"url": f"/downloads/{token}", "filename": filename, "files": len(items), "size": size,
                "zip": not single, "expires_at": now + LINK_SECONDS}

    def _folder(self, actor: Actor, bucket_id: str, prefix: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        after: Optional[str] = None
        while True:
            page = self._store.list_files(actor.user_id, bucket_id, prefix, after, 1000, folders=False)
            for e in page["entries"]:
                if e.get("latest_complete") is not None:
                    size = e.get("total_size") if e.get("version") == e["latest_complete"] else None
                    if size is None:
                        size = self._store.object_info(actor.user_id, bucket_id, e["name"],
                                                       e["latest_complete"]).get("total_size") or 0
                    out.append({"name": e["name"], "version": e["latest_complete"], "size": size})
            after = page.get("next")
            if after is None or len(out) > MAX_ZIP_FILES:
                return out

    def open_link(self, token: str) -> Tuple[str, str, Optional[int], Iterator[bytes]]:
        """(filename, media type, size or None, bytes) of a link still valid;
        the link itself is what lets the browser in, for its few minutes."""
        link = self._links.find_one({"_id": _hash(token), "expires_at": {"$gte": self._now_fn()}})
        if link is None:
            raise NotFoundError("this download link has expired; ask for the download again")
        if self._buckets.by_key(link["bucket_id"]) is None:
            raise NotFoundError("the bucket is gone")
        user, bucket = link["user_id"], link["bucket_id"]
        if not link["zip"]:
            item = link["items"][0]
            media = mimetypes.guess_type(link["filename"])[0] or "application/octet-stream"
            return link["filename"], media, item["size"], self._store.download(user, bucket, item["name"], item["version"])
        return link["filename"], "application/zip", None, _zip(link["items"], lambda i: self._store.download(
            user, bucket, i["name"], i["version"]), self._now_fn())


class _Sink:
    """Where zipfile writes: collects bytes until the generator hands them on."""

    def __init__(self) -> None:
        self.parts: List[bytes] = []
        self.size = 0          # written so far (zipfile asks, to place entries)
        self.pending = 0       # not handed on yet

    def write(self, data: bytes) -> int:
        self.parts.append(bytes(data))
        self.size += len(data)
        self.pending += len(data)
        return len(data)

    def tell(self) -> int:
        return self.size

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = b"".join(self.parts)
        self.parts = []
        self.pending = 0
        return out


def _zip(items: List[Dict[str, Any]], read: Callable[[Dict[str, Any]], Iterator[bytes]], now: float) -> Iterator[bytes]:
    """A zip written while it is sent: never whole in memory."""
    sink = _Sink()
    stamp = time.gmtime(now)[:6]
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:  # type: ignore[call-overload]
        for item in items:
            entry = zipfile.ZipInfo(item["path"], date_time=stamp)
            entry.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(entry, "w", force_zip64=item["size"] >= GIB) as out:
                for chunk in read(item):
                    out.write(chunk)
                    if sink.pending >= 1024 * 1024:
                        yield sink.take()
            yield sink.take()
    yield sink.take()


def _row(entry: Dict[str, Any], writers: Dict[Tuple[Any, Any], Dict[str, Any]]) -> Dict[str, Any]:
    if entry.get("kind") == "folder":
        return {"kind": "folder", "name": entry["name"], "files": entry.get("files", 0)}
    return {"kind": "file", "name": entry["name"], "version": entry.get("version"),
            "versions": entry.get("versions"), "latest_complete": entry.get("latest_complete"),
            "size": entry.get("total_size"), "state": entry.get("state"), "content_hash": entry.get("content_hash"),
            "created_at": entry.get("created_at"), "completed_at": entry.get("completed_at"),
            "by": writers.get((entry.get("created_by"), entry.get("source")))}


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not name or len(name) > 1024 or name.startswith("/") or name.endswith("/") \
            or "//" in name or any(part in (".", "..") for part in name.split("/")):
        raise InvalidError("a file name is a path like reports/2024/march.csv: no leading or trailing /, "
                           "no empty, . or .. parts, at most 1024 characters")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
