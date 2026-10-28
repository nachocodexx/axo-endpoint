from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application import notifications as N
from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.notifications import Notifications, bill_viewers
from axo_control_plane.domain import settings as S
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import NotFoundError

DAY = 86400.0
REMIND_BEFORE_S = 3 * DAY
BUDGET_THRESHOLDS = (80, 100)


def month_label(month: str) -> str:
    """'2026-10' -> 'October 2026'."""
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


class BillingNotices:
    """The billing notices of upgrade.md 13.7 that come from time passing,
    sent to whoever sees the bill (``bill_viewers``: the person, or the
    Team Owner + Team Admins). Run after the renewals and bill closing
    (same lease); every notice has a dedup key, so repeats send nothing:

    - a VE bill / storage bill closed (marked ``notified`` once sent);
    - 3 days before a renewal ("ml-prod renews on 20/11 for $5");
    - 3 days before a cancelled VE stops;
    - budgets: the open period's bill reached 80 % / 100 % of the VE's
      budget -- warnings only, jobs are never stopped.

    Notices for an action (cancel confirmed, downgrade scheduled) are sent
    by ``VeService`` at the moment it happens; Free limit notices by the
    front door."""

    def __init__(self, db: Database, notifications: Notifications, catalog: PlanCatalog, bills: VeBills,
                 grace_s: float = 30 * DAY, now_fn: Callable[[], float] = time.time) -> None:
        self._db = db
        self._ves = db["virtual_environments"]
        self._ve_bills = db["ve_bills"]
        self._storage_bills = db["storage_bills"]
        self._notifications = notifications
        self._catalog = catalog
        self._bills = bills
        self._grace_s = grace_s
        self._now_fn = now_fn

    def tick(self) -> Dict[str, int]:
        return {"bills": self.closed_bills(), "reminders": self.reminders(), "budgets": self.budgets()}

    # ── closed bills ─────────────────────────────────────────────────────────
    def closed_bills(self) -> int:
        sent = 0
        for bill in self._ve_bills.find({"notified": {"$ne": True}}):
            if bill.get("late_only"):
                text = f"{bill.get('ve_name')}: late usage billed, {{total}}"
            else:
                text = f"{bill.get('ve_name')} bill {{start}}–{{end}}: {{total}}"
            sent += self._notifications.notify(
                bill_viewers(self._db, bill.get("owner") or {}), N.BILL_CLOSED, text,
                {"kind": "ve", "bill_id": bill["_id"], "ve_id": bill["ve_id"], "total_cents": bill["total_cents"]},
                dedup_key=f"bill:{bill['_id']}",
                fields={"start": S.date(bill.get("period_start")), "end": S.date(bill.get("period_end")),
                        "total": S.money(bill["total_cents"])})
            self._ve_bills.update_one({"_id": bill["_id"]}, {"$set": {"notified": True}})
        for bill in self._storage_bills.find({"notified": {"$ne": True}}):
            # The month is a billing month (counted in UTC), a name rather than a moment.
            text = f"Storage bill {month_label(bill['month'])}: {{total}}"
            sent += self._notifications.notify(
                bill_viewers(self._db, bill.get("payer") or {}), N.BILL_CLOSED, text,
                {"kind": "storage", "bill_id": bill["_id"], "month": bill["month"],
                 "total_cents": bill["total_cents"]},
                dedup_key=f"bill:{bill['_id']}", fields={"total": S.money(bill["total_cents"])})
            self._storage_bills.update_one({"_id": bill["_id"]}, {"$set": {"notified": True}})
        return sent

    # ── renewal / stop reminders ─────────────────────────────────────────────
    def reminders(self) -> int:
        now = self._now_fn()
        sent = 0
        for doc in self._ves.find({"desired": {"$ne": V.DESIRED_DELETED}, "rental_ended_at": None,
                                   "period_end": {"$gt": now, "$lte": now + REMIND_BEFORE_S}}):
            ve = V.VirtualEnvironment.from_doc(doc)
            viewers = bill_viewers(self._db, ve.owner.to_dict())
            if ve.cancel_at_period_end:
                sent += self._notifications.notify(
                    viewers, N.RENTAL_ENDING,
                    f"{ve.name} stops on {{ends_at}}; data kept until {{delete_after}}",
                    {"ve_id": ve.ve_id, "ends_at": ve.period_end,
                     "delete_after": (ve.period_end or now) + self._grace_s},
                    dedup_key=f"stop-soon:{ve.ve_id}:{ve.period_index}",
                    fields={"ends_at": S.date(ve.period_end),
                            "delete_after": S.date((ve.period_end or now) + self._grace_s)})
                continue
            plan_id = (ve.pending_change or {}).get("plan_id") or ve.plan_id
            fee = self._catalog.get(plan_id).monthly_fee_cents
            if fee <= 0:
                continue                                  # Free: nothing to pay, no reminder
            sent += self._notifications.notify(
                viewers, N.RENEWAL_SOON, f"{ve.name} renews on {{renews_at}} for {{fee}}",
                {"ve_id": ve.ve_id, "renews_at": ve.period_end, "fee_cents": fee, "plan_id": plan_id},
                dedup_key=f"renewal:{ve.ve_id}:{ve.period_index}",
                fields={"renews_at": S.date(ve.period_end), "fee": S.money(fee)})
        return sent

    # ── budgets ──────────────────────────────────────────────────────────────
    def budgets(self) -> int:
        sent = 0
        for doc in self._ves.find({"desired": {"$ne": V.DESIRED_DELETED}, "rental_ended_at": None,
                                   "budget_cents": {"$gt": 0}}):
            try:
                bill = self._bills.current(doc["_id"])
            except NotFoundError:
                continue
            used = 100 * bill["total_cents"] / doc["budget_cents"]
            for threshold in BUDGET_THRESHOLDS:
                if used < threshold:
                    break
                sent += self._notifications.notify(
                    bill_viewers(self._db, doc.get("owner") or {}), N.BUDGET_WARNING,
                    f"{doc.get('name')} reached {threshold}% of its {{budget}} budget this period "
                    "({total} so far)",
                    {"ve_id": doc["_id"], "threshold": threshold, "budget_cents": doc["budget_cents"],
                     "total_cents": bill["total_cents"], "period_index": bill["period_index"]},
                    dedup_key=f"budget:{doc['_id']}:{bill['period_index']}:{threshold}",
                    fields={"budget": S.money(doc["budget_cents"]), "total": S.money(bill["total_cents"])})
        return sent
