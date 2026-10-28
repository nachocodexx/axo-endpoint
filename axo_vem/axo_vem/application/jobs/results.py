"""Where job results are kept: every result of any size, one file per job
in the VE's platform bucket ``axo/ve-<id>-results`` (created by the Control
Plane with the VE, billed to its owner as storage), written as the VEM
(``vem:<ve>``). Without axo_storage (local dev) the VEM's own Mongo keeps
them."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict

from pymongo.collection import Collection

from axo_shared.axo_storage import AxoStorageClient
from axo_shared.errors import BucketAlreadyExistsError

from axo_vem.domain.errors import ConflictError, NotFoundError


def results_bucket_name(virtual_environment_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", virtual_environment_id.lower()).strip("-") or "ve"
    return f"axo/ve-{slug}-results"[:67]


class ResultNotSaved(ConflictError):
    """Saving failed (e.g. the VE's storage is full); the message says why."""


class JobResultStore(ABC):
    @abstractmethod
    def put(self, job_id: str, value: Any) -> Dict[str, Any]:
        """Saves the job's result; returns where (bucket, name, version, size)."""

    @abstractmethod
    def get(self, job_id: str) -> Any: ...

    @abstractmethod
    def delete(self, job_id: str) -> None: ...


def _encode(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MongoJobResultStore(JobResultStore):
    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def put(self, job_id: str, value: Any) -> Dict[str, Any]:
        data = _encode(value)
        self._collection.update_one({"_id": job_id}, {"$set": {"value": data.decode("utf-8"), "saved_at": _now()}},
                                    upsert=True)
        return {"store": "vem", "name": f"{job_id}.json", "size": len(data)}

    def get(self, job_id: str) -> Any:
        doc = self._collection.find_one({"_id": job_id})
        if doc is None:
            raise NotFoundError("the job's result is not kept (any more)", error_name="JOB_RESULT_NOT_KEPT")
        return json.loads(doc["value"])

    def delete(self, job_id: str) -> None:
        self._collection.delete_one({"_id": job_id})


class StorageJobResultStore(JobResultStore):
    def __init__(self, client: AxoStorageClient, virtual_environment_id: str) -> None:
        self._client = client
        self._bucket = results_bucket_name(virtual_environment_id)
        self._principal = f"vem:{virtual_environment_id}"
        self._ready = False

    @property
    def bucket(self) -> str:
        return self._bucket

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        if self._client.bucket_info(self._principal, self._bucket).is_err:
            created = self._client.create_bucket(self._principal, self._bucket)
            if created.is_err and not isinstance(created.unwrap_err(), BucketAlreadyExistsError):
                raise ResultNotSaved(f"the results bucket is unavailable: {created.unwrap_err()}")
        self._ready = True

    def put(self, job_id: str, value: Any) -> Dict[str, Any]:
        self._ensure_bucket()
        data = _encode(value)
        name = f"{job_id}.json"
        saved = self._client.upload(self._principal, self._bucket, name, data, format="json")
        if saved.is_err:
            raise ResultNotSaved(str(saved.unwrap_err()))
        return {"bucket": self._bucket, "name": name, "version": saved.unwrap().get("version"), "size": len(data)}

    def get(self, job_id: str) -> Any:
        data = self._client.download(self._principal, self._bucket, f"{job_id}.json", None)
        if data.is_err:
            raise NotFoundError("the job's result is not kept (any more)", error_name="JOB_RESULT_NOT_KEPT")
        return json.loads(data.unwrap().decode("utf-8"))

    def delete(self, job_id: str) -> None:
        self._client.delete(self._principal, self._bucket, f"{job_id}.json", None)
