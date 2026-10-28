from __future__ import annotations

import calendar
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application.billing import ucents_to_cents
from axo_control_plane.application.buckets import CP_PRINCIPAL
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.ports import HistoryLog
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import NotFoundError
from axo_control_plane.domain.names import ve_of_results_bucket
from axo_control_plane.domain.periods import BILLING_CLOCK, BILLING_TIME_ZONE, add_months

STORAGE_USAGE_REPORTED = "StorageUsageReported"
STORAGE_SIZES_REPORTED = "StorageSizesReported"
GIB = 1024 ** 3


def month_start(ts: float) -> float:
    dt = datetime.fromtimestamp(ts, tz=BILLING_CLOCK)
    return datetime(dt.year, dt.month, 1, tzinfo=BILLING_CLOCK).timestamp()


def month_name(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=BILLING_CLOCK).strftime("%Y-%m")


def hours_in_month(ts: float) -> int:
    dt = datetime.fromtimestamp(ts, tz=BILLING_CLOCK)
    return calendar.monthrange(dt.year, dt.month)[1] * 24


def _payer_key(payer: Dict[str, Any]) -> str:
    return f"{payer['kind']}:{payer['id']}"


class StorageMetering:
    """Bucket storage billing (upgrade.md 13.3), from what axo_storage
    reports to the intake:

    - every minute, requests and bytes per (bucket, VE, user) -> the
      **reader** pays: the VE's owner for a job, the person for the bucket
      pages; one price per request, transfer only for bytes read;
    - every hour, each bucket's size -> the **bucket owner** pays,
      GB-month = bytes / GiB x (1 / hours in that month).

    Costs are worked out once on arrival (price list in force at the
    minute / hour) and saved. Buckets the CP doesn't know (``axo/...``
    platform buckets) and the CP's own requests are never billed -- except
    a VE's results bucket (``axo/ve-<id>-results``, every job's result),
    billed to the VE's owner like a bucket they own.

    One storage bill per payer (user or team) per calendar month (UTC),
    closed ``close_after_s`` after the month ends and never changed; items
    that arrive later go on the next bill as late lines."""

    def __init__(self, db: Database, catalog: PlanCatalog, history: HistoryLog, close_after_s: float = 3600.0,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._buckets = db["buckets"]
        self._ves = db["virtual_environments"]
        self._usage = db["storage_usage"]
        self._sizes = db["storage_size_hours"]
        self._bills = db["storage_bills"]
        self._catalog = catalog
        self._history = history
        self._close_after_s = close_after_s
        self._now_fn = now_fn

    # ── intake ───────────────────────────────────────────────────────────────
    def usage(self, source_id: str, data: Dict[str, Any]) -> bool:
        start = float(data.get("bin_start") or 0.0)
        prices = self._catalog.prices(start)
        for row in data.get("rows") or []:
            bucket = self._buckets.find_one({"_id": row.get("bucket")}, {"owner": 1})
            billed = bucket is not None or ve_of_results_bucket(str(row.get("bucket") or "")) is not None
            payer = self._reader(row) if billed else None
            if payer is None:
                continue                       # platform bucket / the CP's own request
            requests = int(row.get("requests") or 0)
            read = int(row.get("bytes_read") or 0)
            request_ucents = requests * prices.storage_per_request_ucents
            transfer_ucents = round(read / GIB * prices.transfer_per_gb_ucents)
            doc = {"_id": f"{source_id}:{int(start)}:{row.get('bucket')}:{row.get('ve_id') or ''}:{row.get('user_id')}",
                   "ts": start, "bin_start": start, "payer": payer, "payer_key": _payer_key(payer),
                   "bucket_id": row.get("bucket"),
                   "ve_id": row.get("ve_id"), "user_id": row.get("user_id"), "requests": requests,
                   "bytes_read": read, "bytes_written": int(row.get("bytes_written") or 0),
                   "cost": {"request_ucents": request_ucents, "transfer_ucents": transfer_ucents,
                            "total_ucents": request_ucents + transfer_ucents},
                   "bill_id": None, "received_at": self._now_fn()}
            try:
                self._usage.insert_one(doc)
            except DuplicateKeyError:
                pass
        return True

    def sizes(self, source_id: str, data: Dict[str, Any]) -> bool:
        hour = float(data.get("hour_start") or 0.0)
        prices = self._catalog.prices(hour)
        hours = hours_in_month(hour)
        for key, size in (data.get("sizes") or {}).items():
            bucket = self._buckets.find_one({"_id": key, "deleted": {"$ne": True}}, {"owner": 1})
            owner = bucket["owner"] if bucket is not None else self._results_bucket_owner(key)
            if owner is None:
                continue
            payer = {k: owner.get(k) for k in ("kind", "id", "name")}
            gb_months = int(size) / GIB / hours
            doc = {"_id": f"{source_id}:{int(hour)}:{key}", "ts": hour, "payer": payer,
                   "payer_key": _payer_key(payer), "bucket_id": key, "bytes": int(size),
                   "gb_months": gb_months,
                   "cost": {"total_ucents": round(gb_months * prices.storage_per_gb_month_ucents)},
                   "bill_id": None, "received_at": self._now_fn()}
            try:
                self._sizes.insert_one(doc)
            except DuplicateKeyError:
                pass
        return True

    def _results_bucket_owner(self, key: str) -> Optional[Dict[str, Any]]:
        """A VE's results bucket (``axo/ve-<id>-results``) is the VE owner's to pay."""
        ve_id = ve_of_results_bucket(key)
        ve = self._ves.find_one({"_id": ve_id}, {"owner": 1}) if ve_id else None
        return ve["owner"] if ve is not None else None

    def _reader(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A job's requests -> the owner of the VE it ran in; the bucket
        pages -> the person."""
        if row.get("ve_id"):
            ve = self._ves.find_one({"_id": row["ve_id"]}, {"owner": 1})
            if ve is not None:
                return {k: ve["owner"].get(k) for k in ("kind", "id", "name")}
        user_id = row.get("user_id")
        if not user_id or user_id == CP_PRINCIPAL:
            return None
        return {"kind": V.OWNER_USER, "id": user_id, "name": None}

    # ── bills ────────────────────────────────────────────────────────────────
    def close_due(self) -> List[str]:
        """Closes every month that ended over ``close_after_s`` ago, oldest
        first, for every payer with something not yet billed in it (or
        before it). Safe to repeat; one CP copy at a time (lease)."""
        cutoff = self._now_fn() - self._close_after_s
        oldest = [d["ts"] for col in (self._usage, self._sizes)
                  for d in col.find({"bill_id": None}, {"ts": 1}).sort("ts", 1).limit(1)]
        if not oldest:
            return []
        closed: List[str] = []
        start = month_start(min(oldest))
        while add_months(start, 1) <= cutoff:
            end = add_months(start, 1)
            payers = set()
            for col in (self._usage, self._sizes):
                payers.update(col.distinct("payer_key", {"bill_id": None, "ts": {"$lt": end}}))
            for payer_key in sorted(payers):
                bill_id = self._close(payer_key, start, end)
                if bill_id:
                    closed.append(bill_id)
            start = end
        return closed

    def _close(self, payer_key: str, start: float, end: float) -> Optional[str]:
        bill_id = f"storage:{payer_key}:{month_name(start)}"
        if self._bills.find_one({"_id": bill_id}, {"_id": 1}) is not None:
            # Already closed: what's left from then waits for the next bill
            # (stamping it here would put it on a bill that never counts it).
            return None
        for col in (self._usage, self._sizes):
            col.update_many({"payer_key": payer_key, "bill_id": None, "ts": {"$lt": end}},
                            {"$set": {"bill_id": bill_id}})
        bill = self._build(payer_key, start, end, {"bill_id": bill_id}, "closed")
        try:
            self._bills.insert_one({"_id": bill_id, **bill})
            self._history.append(f"billing-{payer_key}", "StorageBillClosed",
                                 {"bill_id": bill_id, "payer_key": payer_key, "month": bill["month"],
                                  "total_cents": bill["total_cents"], "at": bill["closed_at"]})
        except DuplicateKeyError:
            pass
        return bill_id

    def current(self, kind: str, owner_id: str) -> Dict[str, Any]:
        """This month's storage bill so far, live (late items included)."""
        now = self._now_fn()
        start = month_start(now)
        return {**self._build(f"{kind}:{owner_id}", start, add_months(start, 1), {"bill_id": None}, "open"),
                "bill_id": None}

    def get(self, bill_id: str) -> Dict[str, Any]:
        doc = self._bills.find_one({"_id": bill_id})
        if doc is None:
            raise NotFoundError("no such bill")
        return {**{k: v for k, v in doc.items() if k != "_id"}, "bill_id": doc["_id"]}

    def list(self, kind: str, owner_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        docs = self._bills.find({"payer_key": f"{kind}:{owner_id}"}).sort("period_start", -1).limit(limit)
        return [{**{k: v for k, v in d.items() if k != "_id"}, "bill_id": d["_id"]} for d in docs]

    def _build(self, payer_key: str, start: float, end: float, match: Dict[str, Any], status: str) -> Dict[str, Any]:
        base = {"payer_key": payer_key, **match}
        storage = self._sum_sizes({**base, "ts": {"$gte": start, "$lt": end}})
        requests = self._sum_usage({**base, "ts": {"$gte": start, "$lt": end}})
        late = self._late(base, start)
        late_ucents = sum(item["storage_ucents"] + item["requests_ucents"] for item in late)
        total_ucents = storage["ucents"] + requests["total_ucents"] + late_ucents
        kind, _, owner_id = payer_key.partition(":")
        return {
            "payer_key": payer_key, "payer": {"kind": kind, "id": owner_id},
            "month": month_name(start), "period_start": start, "period_end": end,
            "billing_time_zone": BILLING_TIME_ZONE, "status": status,
            "closed_at": self._now_fn() if status == "closed" else None,
            "storage": storage, "requests": requests, "late": late,
            "total_ucents": total_ucents, "total_cents": ucents_to_cents(total_ucents),
        }

    def _sum_sizes(self, match: Dict[str, Any]) -> Dict[str, Any]:
        rows = list(self._sizes.aggregate([
            {"$match": match},
            {"$group": {"_id": "$bucket_id", "gb_months": {"$sum": "$gb_months"},
                        "ucents": {"$sum": "$cost.total_ucents"}, "max_bytes": {"$max": "$bytes"}}}]))
        buckets = [{"bucket_id": r["_id"], "gb_months": round(r["gb_months"], 6), "ucents": int(r["ucents"]),
                    "max_bytes": int(r["max_bytes"])} for r in sorted(rows, key=lambda r: r["_id"])]
        return {"gb_months": round(sum(b["gb_months"] for b in buckets), 6),
                "ucents": sum(b["ucents"] for b in buckets), "buckets": buckets}

    def _sum_usage(self, match: Dict[str, Any]) -> Dict[str, Any]:
        rows = list(self._usage.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "requests": {"$sum": "$requests"}, "bytes_read": {"$sum": "$bytes_read"},
                        "bytes_written": {"$sum": "$bytes_written"},
                        "request_ucents": {"$sum": "$cost.request_ucents"},
                        "transfer_ucents": {"$sum": "$cost.transfer_ucents"},
                        "total_ucents": {"$sum": "$cost.total_ucents"}}}]))
        row = rows[0] if rows else {}
        return {k: int(row.get(k, 0)) for k in ("requests", "bytes_read", "bytes_written", "request_ucents",
                                                "transfer_ucents", "total_ucents")}

    def _late(self, base: Dict[str, Any], start: float) -> List[Dict[str, Any]]:
        """Items of earlier months (they arrived after that month's bill
        closed), one line per month."""
        months: Dict[float, Tuple[int, int]] = {}
        for col, field in ((self._sizes, 0), (self._usage, 1)):
            for doc in col.find({**base, "ts": {"$lt": start}}, {"ts": 1, "cost.total_ucents": 1}):
                m = month_start(doc["ts"])
                pair = list(months.get(m, (0, 0)))
                pair[field] += int(doc["cost"]["total_ucents"])
                months[m] = (pair[0], pair[1])
        return [{"from_month": month_name(m), "storage_ucents": s, "requests_ucents": r}
                for m, (s, r) in sorted(months.items())]
