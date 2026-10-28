from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from axo_control_plane.application.ports import HistoryLog
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.billing import UCENTS_PER_CENT
from axo_control_plane.domain.errors import NotFoundError

_COST_FIELDS = ("request_ucents", "compute_ucents", "memory_ucents", "total_ucents")
_QTY_FIELDS = ("requests", "core_seconds", "gb_seconds")


def ucents_to_cents(ucents: int) -> int:
    """Half up, once per bill (never per attempt, so tiny jobs add up)."""
    return (int(ucents) + UCENTS_PER_CENT // 2) // UCENTS_PER_CENT


class VeBills:
    """One bill per VE per rental period (upgrade.md 13.2): the period's fee
    lines (``ve_charges``) + the usage of its job attempts (their saved
    ``cost``) + its active objects' state, hour by hour
    (``object_state_hours``, GB-months).

    - A bill closes ``close_after_s`` (1 h) after its period ends and never
      changes after that (``ve_bills``).
    - Closing first stamps every attempt not yet on a bill that finished
      before the period end with the bill's id, then adds those up -- so a
      repeat after a crash adds up the same attempts, and an attempt that
      arrives later is left for the next bill.
    - Attempts that finished in an earlier period (they reached the CP after
      that period's bill closed) are listed on it as "late usage" lines.
    - The open period's bill is worked out live (``current``)."""

    def __init__(self, db: Database, history: HistoryLog, close_after_s: float = 3600.0,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._ves = db["virtual_environments"]
        self._charges = db["ve_charges"]
        self._attempts = db["job_attempts"]
        self._object_hours = db["object_state_hours"]
        self._bills = db["ve_bills"]
        self._history = history
        self._close_after_s = close_after_s
        self._now_fn = now_fn

    # ── closing ──────────────────────────────────────────────────────────────
    def close_due(self) -> List[str]:
        """Close every period bill whose period ended over ``close_after_s``
        ago (oldest first). Safe to repeat; run by one CP copy (lease)."""
        cutoff = self._now_fn() - self._close_after_s
        closed: List[str] = []
        due = self._charges.find({"kind": "plan_fee", "period_end": {"$lte": cutoff}, "bill_closed": {"$ne": True}}
                                 ).sort([("ve_id", 1), ("period_index", 1)])
        for fee in due:
            bill = self._close(fee["ve_id"], fee["period_index"])
            self._charges.update_one({"_id": fee["_id"]}, {"$set": {"bill_closed": True}})
            closed.append(bill["bill_id"])
        closed += self._close_leftovers(cutoff)
        return closed

    def _close(self, ve_id: str, index: int) -> Dict[str, Any]:
        bill_id = f"{ve_id}:{index}"
        fee = self._charges.find_one({"ve_id": ve_id, "period_index": index, "kind": "plan_fee"})
        if fee is None:
            raise NotFoundError(f"no period {index} for VE {ve_id}")
        existing = self._bills.find_one({"_id": bill_id})
        if existing is not None:
            # Saved before a crash: never stamp more attempts onto it (they
            # wait for the next bill).
            return self._public(existing)
        self._attempts.update_many({"ve_id": ve_id, "bill_id": None, "finished_at_ts": {"$lt": fee["period_end"]}},
                                   {"$set": {"bill_id": bill_id}})
        self._object_hours.update_many({"ve_id": ve_id, "bill_id": None, "ts": {"$lt": fee["period_end"]}},
                                       {"$set": {"bill_id": bill_id}})
        bill = self._build(ve_id, index, fee, {"bill_id": bill_id}, status="closed")
        try:
            self._bills.insert_one({"_id": bill_id, **bill})
        except DuplicateKeyError:
            return self._public(self._bills.find_one({"_id": bill_id}))
        self._history.append(f"ve-{ve_id}", "VirtualEnvironmentBillClosed",
                             {"bill_id": bill_id, "ve_id": ve_id, "period_index": index,
                              "total_cents": bill["total_cents"], "at": bill["closed_at"]})
        return {**bill, "bill_id": bill_id}

    def _close_leftovers(self, cutoff: float) -> List[str]:
        """A VE with no period left to close (rental ended / deleted) whose
        late usage still arrived: one extra bill holding only that."""
        closed: List[str] = []
        left = {"bill_id": None, "received_by_cp_at": {"$lte": cutoff}}
        ve_ids = set(self._attempts.distinct("ve_id", left)) | set(self._object_hours.distinct("ve_id", left))
        for ve_id in sorted(ve_ids):
            doc = self._ves.find_one({"_id": ve_id}) or {}
            ended = doc.get("desired") == V.DESIRED_DELETED or doc.get("rental_ended_at") is not None
            last = self._charges.find_one({"ve_id": ve_id, "kind": "plan_fee"}, sort=[("period_index", -1)])
            if not ended or last is None or self._bills.find_one({"_id": f"{ve_id}:{last['period_index']}"}) is None:
                continue                       # the next period's bill will take them
            bill_id = f"{ve_id}:late:{int(cutoff)}"
            self._attempts.update_many({"ve_id": ve_id, **left}, {"$set": {"bill_id": bill_id}})
            self._object_hours.update_many({"ve_id": ve_id, **left}, {"$set": {"bill_id": bill_id}})
            bill = self._build(ve_id, last["period_index"], {**last, "period_start": last["period_end"]},
                               {"bill_id": bill_id}, status="closed", fees=False)
            try:
                self._bills.insert_one({"_id": bill_id, **bill, "late_only": True})
                closed.append(bill_id)
            except DuplicateKeyError:
                pass
        return closed

    # ── reading ──────────────────────────────────────────────────────────────
    def current(self, ve_id: str) -> Dict[str, Any]:
        """The open period's bill so far (live; nothing saved)."""
        doc = self._ves.find_one({"_id": ve_id})
        if doc is None:
            raise NotFoundError("no such VE")
        index = int(doc.get("period_index", 0))
        fee = self._charges.find_one({"ve_id": ve_id, "period_index": index, "kind": "plan_fee"})
        if fee is None:
            raise NotFoundError("this VE has no rental period")
        if self._bills.find_one({"_id": f"{ve_id}:{index}"}, {"_id": 1}) is not None:
            return self.get(ve_id, index)
        return {**self._build(ve_id, index, fee, {"bill_id": None}, status="open"), "bill_id": None}

    def get(self, ve_id: str, index: int) -> Dict[str, Any]:
        doc = self._bills.find_one({"_id": f"{ve_id}:{index}"})
        if doc is None:
            raise NotFoundError("no such bill")
        return self._public(doc)

    def list(self, ve_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        return [self._public(d) for d in self._bills.find({"ve_id": ve_id}).sort("period_end", -1).limit(limit)]

    # ── adding up ────────────────────────────────────────────────────────────
    def _build(self, ve_id: str, index: int, fee: Dict[str, Any], match: Dict[str, Any], status: str,
               fees: bool = True) -> Dict[str, Any]:
        start, end = fee["period_start"], fee["period_end"]
        lines = list(self._charges.find({"ve_id": ve_id, "period_index": index}).sort("at", 1)) if fees else []
        base = {"ve_id": ve_id, **match}
        usage = self._sum({**base, "finished_at_ts": {"$gte": start}})
        late = []
        for period, total in self._late(ve_id, {**base, "finished_at_ts": {"$lt": start}}):
            late.append({**period, **total})
        objects = self._object_state(base)
        usage_ucents = usage["total_ucents"] + sum(item["total_ucents"] for item in late) + objects["ucents"]
        fees_cents = sum(int(c["amount_cents"]) for c in lines)
        ve = self._ves.find_one({"_id": ve_id}, {"name": 1, "owner": 1}) or {}
        return {
            "ve_id": ve_id, "ve_name": ve.get("name"), "owner": ve.get("owner") or fee.get("owner"),
            "period_index": index, "period_start": start, "period_end": end, "status": status,
            "closed_at": self._now_fn() if status == "closed" else None,
            "fees": [{"kind": c["kind"], "plan_id": c["plan_id"], "amount_cents": int(c["amount_cents"]),
                      "at": c["at"]} for c in lines],
            "fees_cents": fees_cents,
            "usage": usage,
            "late_usage": late,
            "object_state": objects,
            "usage_ucents": usage_ucents,
            "usage_cents": ucents_to_cents(usage_ucents),
            "total_cents": fees_cents + ucents_to_cents(usage_ucents),
        }

    def _sum(self, match: Dict[str, Any]) -> Dict[str, Any]:
        group: Dict[str, Any] = {"_id": None, "attempts": {"$sum": 1},
                                 "billable_attempts": {"$sum": {"$cond": ["$cost.billable", 1, 0]}}}
        for name in _QTY_FIELDS + _COST_FIELDS:
            group[name] = {"$sum": f"$cost.{name}"}
        rows = list(self._attempts.aggregate([{"$match": match}, {"$group": group}]))
        out = {k: v for k, v in (rows[0] if rows else {}).items() if k != "_id"}
        totals = {"attempts": 0, "billable_attempts": 0, **{n: 0 for n in _QTY_FIELDS + _COST_FIELDS}, **out}
        totals["core_seconds"] = round(float(totals["core_seconds"]), 6)
        totals["gb_seconds"] = round(float(totals["gb_seconds"]), 6)
        for name in ("attempts", "billable_attempts", "requests") + _COST_FIELDS:
            totals[name] = int(totals[name])
        return totals

    def _object_state(self, match: Dict[str, Any]) -> Dict[str, Any]:
        """Its object state hours (an hour that arrived late is simply on
        the next bill): GB-months, cost, the largest size seen."""
        rows = list(self._object_hours.aggregate([{"$match": match}, {"$group": {
            "_id": None, "hours": {"$sum": 1}, "gb_months": {"$sum": "$gb_months"},
            "ucents": {"$sum": "$cost.total_ucents"}, "max_bytes": {"$max": "$bytes"}}}]))
        row = rows[0] if rows else {}
        return {"hours": int(row.get("hours", 0)), "gb_months": round(float(row.get("gb_months", 0.0)), 6),
                "ucents": int(row.get("ucents", 0)), "max_bytes": int(row.get("max_bytes") or 0)}

    def _late(self, ve_id: str, match: Dict[str, Any]) -> List[Any]:
        """Late attempts, grouped by the period they finished in (rare)."""
        if self._attempts.count_documents(match, limit=1) == 0:
            return []
        everything = self._sum(match)
        out = []
        for fee in self._charges.find({"ve_id": ve_id, "kind": "plan_fee"}).sort("period_index", 1):
            window = {**match, "finished_at_ts": {"$gte": fee["period_start"], "$lt": fee["period_end"]}}
            if fee["period_end"] > match["finished_at_ts"]["$lt"]:
                window["finished_at_ts"]["$lt"] = match["finished_at_ts"]["$lt"]
            total = self._sum(window)
            if total["attempts"]:
                out.append(({"from_period_index": fee["period_index"], "from_period_start": fee["period_start"],
                             "from_period_end": fee["period_end"]}, total))
        # Anything outside every period (e.g. finished while the rental had
        # ended): still billed, as one line without a period.
        rest = {k: v - sum(t[k] for _, t in out) for k, v in everything.items()}
        if rest["attempts"] > 0:
            rest["core_seconds"] = round(rest["core_seconds"], 6)
            rest["gb_seconds"] = round(rest["gb_seconds"], 6)
            out.append(({"from_period_index": None, "from_period_start": None, "from_period_end": None}, rest))
        return out

    @staticmethod
    def _public(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        doc = dict(doc or {})
        doc["bill_id"] = doc.pop("_id", None)
        return doc
