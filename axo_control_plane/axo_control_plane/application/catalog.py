from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo import DESCENDING
from pymongo.database import Database

from axo_control_plane.application.ports import HistoryLog
from axo_control_plane.domain.errors import InvalidError, NotFoundError
from axo_control_plane.domain.plans import BASE_SIZES, DEFAULT_PLANS, Plan, PriceList


class PlanCatalog:
    """Plans and the usage price list, managed by the Platform Admin.

    - Plans are seeded from the defaults on the first start and edited later.
    - Every price-list change is kept with the moment it took effect, so past
      usage keeps the price it had (cost views pick the list in force then).
    """

    def __init__(self, db: Database, history: HistoryLog, now_fn: Callable[[], float] = time.time) -> None:
        self._plans = db["plans"]
        self._prices = db["price_lists"]
        self._history = history
        self._now_fn = now_fn

    def seed(self) -> None:
        for plan in DEFAULT_PLANS:
            self._plans.update_one({"_id": plan.plan_id}, {"$setOnInsert": plan.to_dict()}, upsert=True)
            # A field added to plans later: saved plans get its default,
            # nothing the Platform Admin changed is touched.
            stored = self._plans.find_one({"_id": plan.plan_id}) or {}
            missing = {k: v for k, v in plan.to_dict().items() if k not in stored and k != "base"}
            if missing:
                self._plans.update_one({"_id": plan.plan_id}, {"$set": missing})
        if self._prices.count_documents({}) == 0:
            self._prices.insert_one({"effective_from": 0.0, **PriceList().to_dict()})

    # ── plans ────────────────────────────────────────────────────────────────
    def plans(self, include_inactive: bool = False) -> List[Plan]:
        query: Dict[str, Any] = {} if include_inactive else {"active": True}
        return [Plan.from_dict(d) for d in self._plans.find(query).sort("order", 1)]

    def get(self, plan_id: str) -> Plan:
        doc = self._plans.find_one({"_id": plan_id})
        if doc is None:
            raise NotFoundError(f"no plan {plan_id!r}")
        return Plan.from_dict(doc)

    def save_plan(self, plan: Plan, by: str) -> Plan:
        if plan.base_size not in BASE_SIZES:
            raise InvalidError(f"base size must be one of {sorted(BASE_SIZES)}")
        if plan.max_endpoints < 1 or plan.max_retention_days < 1 or plan.monthly_fee_cents < 0:
            raise InvalidError("a plan needs max_endpoints >= 1, max_retention_days >= 1 and a fee >= 0")
        if plan.object_max_state_bytes < 1 or plan.object_hot_max_bytes < 1:
            raise InvalidError("a plan needs object_max_state_bytes >= 1 and object_hot_max_bytes >= 1")
        if not 0 <= plan.object_keep_revisions <= plan.object_max_keep_revisions:
            raise InvalidError("a plan needs 0 <= object_keep_revisions <= object_max_keep_revisions")
        if not (0 < plan.runner_cpu_cores <= plan.runner_max_cpu_cores
                and 64 <= plan.runner_memory_mb <= plan.runner_max_memory_mb):
            raise InvalidError("a plan needs 0 < runner_cpu_cores <= runner_max_cpu_cores and "
                               "64 <= runner_memory_mb <= runner_max_memory_mb")
        self._plans.replace_one({"_id": plan.plan_id}, plan.to_dict(), upsert=True)
        self._history.append("platform-catalog", "PlanSaved", {"plan": plan.to_dict(), "by": by, "at": self._now_fn()})
        return plan

    # ── prices ───────────────────────────────────────────────────────────────
    def prices(self, at: Optional[float] = None) -> PriceList:
        return self.prices_in_force(at)[1]

    def prices_in_force(self, at: Optional[float] = None) -> Tuple[float, PriceList]:
        """(when it took effect, the price list) in force at ``at`` (now if None)."""
        query = {} if at is None else {"effective_from": {"$lte": at}}
        doc = next(iter(self._prices.find(query).sort("effective_from", DESCENDING).limit(1)), None)
        if doc is None:
            return 0.0, PriceList()
        return float(doc.get("effective_from", 0.0)), PriceList.from_dict(doc)

    def set_prices(self, prices: PriceList, by: str) -> PriceList:
        if any(v < 0 for v in prices.to_dict().values()):
            raise InvalidError("prices can't be negative")
        now = self._now_fn()
        self._prices.insert_one({"effective_from": now, **prices.to_dict(), "by": by})
        self._history.append("platform-catalog", "PricesChanged", {"prices": prices.to_dict(), "by": by, "at": now})
        return prices
