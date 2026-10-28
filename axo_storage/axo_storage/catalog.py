from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from option import Err, Ok, Result

from axo_shared.errors import (
    AxoError,
    BucketAlreadyExistsError,
    BucketNotFoundError,
    ObjectNotFoundError,
    ObjectVersionExistsError,
)

UPLOADING = "uploading"
COMPLETE = "complete"
FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS buckets (
    name        TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    created_by  TEXT NOT NULL,
    quota_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS objects (
    bucket        TEXT NOT NULL,
    name          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    format        TEXT NOT NULL,
    total_size    INTEGER,
    chunk_bytes   INTEGER NOT NULL,
    total_chunks  INTEGER,
    declared_hash TEXT,
    content_hash  TEXT,
    hash_algo     TEXT NOT NULL,
    state         TEXT NOT NULL,
    created_at    REAL NOT NULL,
    created_by    TEXT NOT NULL,
    completed_at  REAL,
    source        TEXT,
    PRIMARY KEY (bucket, name, version)
);
CREATE TABLE IF NOT EXISTS chunks (
    bucket  TEXT NOT NULL,
    name    TEXT NOT NULL,
    version INTEGER NOT NULL,
    idx     INTEGER NOT NULL,
    size    INTEGER NOT NULL,
    PRIMARY KEY (bucket, name, version, idx)
);
"""


@dataclass(frozen=True)
class BucketRow:
    name: str
    created_at: float
    created_by: str
    # Stand-in until the Control Plane owns bucket metadata (phase 5): the
    # quota given at creation, used only when the access answer has none.
    quota_bytes: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObjectRow:
    """One immutable version of an object. ``total_size``/``total_chunks``
    are None while a streamed upload (size unknown upfront) is in progress."""

    bucket: str
    name: str
    version: int
    format: str
    total_size: Optional[int]
    chunk_bytes: int
    total_chunks: Optional[int]
    declared_hash: Optional[str]
    content_hash: Optional[str]
    hash_algo: str
    state: str
    created_at: float
    created_by: str
    completed_at: Optional[float] = None
    # What wrote it, when the writer says (e.g. "choreography:<id>"): the
    # "object written" report carries it, so a choreography's own writes
    # don't trigger it again.
    source: Optional[str] = None

    @property
    def streamed(self) -> bool:
        return self.state == UPLOADING and self.total_size is None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StorageCatalog:
    """axo_storage's own records -- buckets, object versions, which chunks
    arrived -- in one SQLite file (":memory:" for tests). The chunk bytes
    themselves live in the StorageLayer, never here. One connection shared
    behind a lock: writes are small and rare next to chunk I/O."""

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(objects)")}
        if "source" not in columns:                       # a catalog from before the column
            self._conn.execute("ALTER TABLE objects ADD COLUMN source TEXT")
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── buckets ──────────────────────────────────────────────────────────────
    def create_bucket(
        self, name: str, created_by: str, now: float, quota_bytes: Optional[int] = None,
    ) -> Result[BucketRow, AxoError]:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO buckets (name, created_at, created_by, quota_bytes) VALUES (?, ?, ?, ?)",
                    (name, now, created_by, quota_bytes),
                )
            except sqlite3.IntegrityError:
                return Err(BucketAlreadyExistsError(f"bucket {name!r} already exists", context={"bucket": name}))
        return Ok(BucketRow(name=name, created_at=now, created_by=created_by, quota_bytes=quota_bytes))

    def get_bucket(self, name: str) -> Result[BucketRow, AxoError]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM buckets WHERE name = ?", (name,)).fetchone()
        if row is None:
            return Err(BucketNotFoundError(f"bucket {name!r} does not exist", context={"bucket": name}))
        return Ok(BucketRow(**dict(row)))

    def delete_bucket(self, name: str) -> None:
        """Removes the bucket's row (its objects must be deleted first)."""
        with self._lock:
            self._conn.execute("DELETE FROM buckets WHERE name = ?", (name,))

    def bucket_usage(self, bucket: str) -> int:
        """Bytes a bucket uses: the declared size of every known-size version
        (complete or still uploading -- reserved up front), plus the chunks
        received so far for streamed uploads."""
        with self._lock:
            declared = self._conn.execute(
                "SELECT COALESCE(SUM(total_size), 0) FROM objects WHERE bucket = ? AND total_size IS NOT NULL",
                (bucket,),
            ).fetchone()[0]
            streamed = self._conn.execute(
                "SELECT COALESCE(SUM(c.size), 0) FROM chunks c JOIN objects d "
                "ON c.bucket = d.bucket AND c.name = d.name AND c.version = d.version "
                "WHERE d.bucket = ? AND d.total_size IS NULL",
                (bucket,),
            ).fetchone()[0]
        return int(declared) + int(streamed)

    def bucket_stats(self, buckets: List[str]) -> Dict[str, Dict[str, Any]]:
        """{bucket key: {used_bytes, files, last_change}} in one go (the
        bucket list): files = names with a complete version, last_change =
        the newest version's time."""
        out: Dict[str, Dict[str, Any]] = {}
        for bucket in buckets:
            with self._lock:
                files, last = self._conn.execute(
                    "SELECT COUNT(DISTINCT CASE WHEN state = ? THEN name END), "
                    "MAX(COALESCE(completed_at, created_at)) FROM objects WHERE bucket = ?",
                    (COMPLETE, bucket),
                ).fetchone()
            out[bucket] = {"used_bytes": self.bucket_usage(bucket), "files": int(files or 0), "last_change": last}
        return out

    def list_files(self, bucket: str, prefix: str = "", delimiter: Optional[str] = "/", after: Optional[str] = None,
                   limit: int = 200) -> Dict[str, Any]:
        """One row per file under ``prefix`` (its newest version, how many
        versions, whether one is complete), folders first when ``delimiter``
        splits the names, ``limit`` entries per page from ``after`` (the
        ``next`` of the page before)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, COUNT(*) AS versions, MAX(version) AS latest, "
                "MAX(CASE WHEN state = ? THEN version END) AS latest_complete "
                "FROM objects WHERE bucket = ? AND substr(name, 1, ?) = ? GROUP BY name ORDER BY name",
                (COMPLETE, bucket, len(prefix), prefix),
            ).fetchall()
        entries: List[Tuple[str, Dict[str, Any]]] = []
        folders: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            rest = row["name"][len(prefix):]
            cut = rest.find(delimiter) if delimiter else -1
            if cut >= 0:
                folder = prefix + rest[:cut + 1]
                entry = folders.get(folder)
                if entry is None:
                    entry = folders[folder] = {"kind": "folder", "name": folder, "files": 0}
                    entries.append((folder, entry))
                entry["files"] += 1
            else:
                entries.append((row["name"], {"kind": "file", "name": row["name"], "versions": row["versions"],
                                              "latest": row["latest"], "latest_complete": row["latest_complete"]}))
        # Folders first, each group by name. A folder's name ends with the
        # delimiter (a file's never does at this level), so ``after`` alone
        # says which group it was in.
        def order(name: str) -> Tuple[bool, str]:
            return (not (delimiter and name.endswith(delimiter)), name)

        entries.sort(key=lambda e: order(e[0]))
        start = 0
        if after is not None:
            start = next((i for i, e in enumerate(entries) if order(e[0]) > order(after)), len(entries))
        page = entries[start:start + limit]
        files = []
        for _, entry in page:
            if entry["kind"] == "file":
                newest = self.get_object(bucket, entry["name"], entry["latest"]).unwrap()
                entry.update(newest.to_dict())
                entry["version"] = newest.version
            files.append(entry)
        more = start + limit < len(entries)
        return {"entries": files, "next": page[-1][0] if more and page else None}

    def all_bucket_sizes(self) -> Dict[str, int]:
        """{bucket key: bytes used} for every bucket (hourly, for billing)."""
        with self._lock:
            names = [r[0] for r in self._conn.execute("SELECT name FROM buckets").fetchall()]
        return {name: self.bucket_usage(name) for name in names}

    # ── objects ─────────────────────────────────────────────────────────────
    def next_version(self, bucket: str, name: str) -> int:
        with self._lock:
            current = self._conn.execute(
                "SELECT MAX(version) FROM objects WHERE bucket = ? AND name = ?", (bucket, name),
            ).fetchone()[0]
        return 1 if current is None else int(current) + 1

    def insert_object(self, row: ObjectRow) -> Result[ObjectRow, AxoError]:
        data = row.to_dict()
        columns = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock:
            try:
                self._conn.execute(f"INSERT INTO objects ({columns}) VALUES ({marks})", tuple(data.values()))
            except sqlite3.IntegrityError:
                return Err(ObjectVersionExistsError(
                    f"{row.bucket}/{row.name} version {row.version} already exists",
                    context={"bucket": row.bucket, "name": row.name, "version": row.version},
                ))
        return Ok(row)

    def get_object(self, bucket: str, name: str, version: Optional[int]) -> Result[ObjectRow, AxoError]:
        """One version; ``version`` None = the latest COMPLETE one."""
        with self._lock:
            if version is None:
                row = self._conn.execute(
                    "SELECT * FROM objects WHERE bucket = ? AND name = ? AND state = ? "
                    "ORDER BY version DESC LIMIT 1",
                    (bucket, name, COMPLETE),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM objects WHERE bucket = ? AND name = ? AND version = ?",
                    (bucket, name, version),
                ).fetchone()
        if row is None:
            what = "no complete version" if version is None else f"version {version}"
            return Err(ObjectNotFoundError(
                f"{bucket}/{name}: {what} found", context={"bucket": bucket, "name": name, "version": version},
            ))
        return Ok(ObjectRow(**dict(row)))

    def list_objects(self, bucket: str, name: Optional[str] = None) -> List[ObjectRow]:
        with self._lock:
            if name is None:
                rows = self._conn.execute(
                    "SELECT * FROM objects WHERE bucket = ? ORDER BY name, version", (bucket,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM objects WHERE bucket = ? AND name = ? ORDER BY version", (bucket, name),
                ).fetchall()
        return [ObjectRow(**dict(row)) for row in rows]

    def record_chunk(self, bucket: str, name: str, version: int, index: int, size: int) -> None:
        """Re-putting the same chunk (a retried upload) just overwrites it."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO chunks (bucket, name, version, idx, size) VALUES (?, ?, ?, ?, ?)",
                (bucket, name, version, index, size),
            )

    def chunk_sizes(self, bucket: str, name: str, version: int) -> Dict[int, int]:
        """index -> size of every chunk received so far."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, size FROM chunks WHERE bucket = ? AND name = ? AND version = ? ORDER BY idx",
                (bucket, name, version),
            ).fetchall()
        return {int(r["idx"]): int(r["size"]) for r in rows}

    def mark_finished(
        self,
        bucket: str,
        name: str,
        version: int,
        state: str,
        total_size: int,
        total_chunks: int,
        content_hash: Optional[str],
        now: float,
    ) -> bool:
        """Moves an uploading version to complete/failed. False if another
        caller already did (two last chunks arriving at once finish it once)."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE objects SET state = ?, total_size = ?, total_chunks = ?, content_hash = ?, "
                "completed_at = ? WHERE bucket = ? AND name = ? AND version = ? AND state = ?",
                (state, total_size, total_chunks, content_hash, now, bucket, name, version, UPLOADING),
            )
        return cursor.rowcount == 1

    def delete_object(self, bucket: str, name: str, version: int) -> Dict[int, int]:
        """Removes one version's records; returns its chunks (index -> size)
        so the caller can delete their bytes."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, size FROM chunks WHERE bucket = ? AND name = ? AND version = ?",
                (bucket, name, version),
            ).fetchall()
            self._conn.execute("BEGIN")
            self._conn.execute(
                "DELETE FROM chunks WHERE bucket = ? AND name = ? AND version = ?", (bucket, name, version),
            )
            self._conn.execute(
                "DELETE FROM objects WHERE bucket = ? AND name = ? AND version = ?", (bucket, name, version),
            )
            self._conn.execute("COMMIT")
        return {int(r["idx"]): int(r["size"]) for r in rows}
