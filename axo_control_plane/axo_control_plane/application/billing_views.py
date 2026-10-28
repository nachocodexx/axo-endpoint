from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from pymongo.database import Database

from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.billing import VeBills, ucents_to_cents
from axo_control_plane.application.people import Actor
from axo_control_plane.application.statistics import Statistics
from axo_control_plane.application.storage_metering import StorageMetering, month_name
from axo_control_plane.application.teams import Teams
from axo_control_plane.application.ve_service import VeService
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ForbiddenError, InvalidError, NotFoundError
from axo_control_plane.domain.names import code_bucket_for, objects_bucket_for, results_bucket_for

DAY = 86400.0
MAX_REVENUE_WINDOW_S = 366 * DAY
MAX_CHOREOGRAPHY_WINDOW_S = 92 * DAY


MAX_JOB_COSTS = 200                         # one page of the jobs list, and some


class BillingViews:
    """Who sees which money (upgrade.md 13.6):

    | view            | who                                   |
    |-----------------|---------------------------------------|
    | VE Costs        | VE Owner and Admin                    |
    | My billing      | the person (own VEs + own storage)    |
    | Team billing    | Team Owner and Team Admins            |
    | Admin revenue   | Platform Admin                        |

    VE Developers and Viewers see usage figures (``Statistics``), never
    money. Money views need a signed-in person, not an SDK key."""

    def __init__(self, db: Database, authz: Authorizer, teams: Teams, ves: VeService, bills: VeBills,
                 storage: StorageMetering, statistics: Statistics, now_fn: Callable[[], float] = time.time,
                 bucket_stats: Optional[Callable[[List[str]], Dict[str, Dict[str, Any]]]] = None) -> None:
        self._ves_col = db["virtual_environments"]
        self._charges = db["ve_charges"]
        self._attempts = db["job_attempts"]
        self._storage_usage = db["storage_usage"]
        self._storage_sizes = db["storage_size_hours"]
        self._object_hours = db["object_state_hours"]
        self._bill_col = db["ve_bills"]
        self._buckets_col = db["buckets"]
        self._authz = authz
        self._teams = teams
        self._ves = ves
        self._bills = bills
        self._storage = storage
        self._stats = statistics
        self._now_fn = now_fn
        self._bucket_stats = bucket_stats            # axo_storage's figures, now (the platform buckets)

    # ── VE Costs tab ─────────────────────────────────────────────────────────
    def ve_costs(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        """The open period so far (live), past bills, cost per function this period."""
        self._require_ve_money(actor, ve_id)
        current = self._current_or_none(ve_id)
        functions: List[Dict[str, Any]] = []
        if current is not None:
            start, now = current["period_start"], self._now_fn()
            if now > start:
                functions = self._stats.top(ve_id, "function", start, now, limit=50, sort="cost",
                                            money=True)["rows"]
        budget = (self._ves_col.find_one({"_id": ve_id}, {"budget_cents": 1}) or {}).get("budget_cents")
        return {"ve_id": ve_id, "current": current, "bills": self._bills.list(ve_id), "functions": functions,
                "budget_cents": budget,
                "budget_used_pct": round(100 * current["total_cents"] / budget, 1) if budget and current else None}

    def choreography_costs(self, actor: Actor, ve_id: str, choreography_id: Optional[str] = None,
                           run_id: Optional[str] = None, start: Optional[float] = None,
                           end: Optional[float] = None, limit: int = 50) -> Dict[str, Any]:
        """What choreographies' step jobs cost (information: the jobs are what
        the VE's bill charges, a choreography itself never is). Default window
        = this rental period so far. No ``choreography_id``: one row per
        choreography; with it: per version, per step and per run (newest
        first); with ``run_id`` too: that run's steps. A run another
        choreography's step called counts for both: on that step's line of
        the caller, and in its own (``by_caller``: started by hand or a
        trigger -- None -- or called by which choreography)."""
        self._require_ve_money(actor, ve_id)
        now = self._now_fn()
        if start is None:
            current = self._current_or_none(ve_id)
            start = current["period_start"] if current else now - 30 * DAY
        end = now if end is None else end
        if end <= start or end - start > MAX_CHOREOGRAPHY_WINDOW_S:
            raise InvalidError("the window must be positive and at most 92 days")
        match: Dict[str, Any] = {"ve_id": ve_id, "started_by_type": "choreography",
                                 "finished_at_ts": {"$gte": start, "$lt": end}}
        out: Dict[str, Any] = {"ve_id": ve_id, "start": start, "end": end, "choreography_id": choreography_id,
                               "run_id": run_id}
        if choreography_id is None:
            out["choreographies"] = self._cost_rows(match, "$started_by_id", "choreography_id", limit)
            out["total"] = (self._cost_rows(match, None, "all", 1) or [_empty_cost()])[0]
            out["total"].pop("all", None)
            return out
        # one line per choreography an attempt counts for (its own run and the runs that called it)
        line: Dict[str, Any] = {"choreography_lines.choreography_id": choreography_id}
        if run_id is not None:
            line["choreography_lines.run_id"] = run_id
        stages: List[Dict[str, Any]] = [{"$unwind": "$choreography_lines"}, {"$match": line}]
        if run_id is None:
            out["by_version"] = self._cost_rows(match, "$choreography_lines.version", "version", limit, stages=stages)
            out["runs"] = self._cost_rows(match, "$choreography_lines.run_id", "run_id", limit, newest_first=True,
                                          stages=stages)
            out["by_caller"] = self._cost_rows(match, "$choreography_lines.caller", "called_by", limit,
                                               stages=stages)
        out["by_step"] = self._cost_rows(match, "$choreography_lines.step", "step", limit, stages=stages)
        out["total"] = (self._cost_rows(match, None, "all", 1, stages=stages) or [_empty_cost()])[0]
        out["total"].pop("all", None)
        return out

    def job_costs(self, actor: Actor, ve_id: str, job_ids: List[str]) -> Dict[str, Any]:
        """What each job cost so far, all its attempts together (the jobs
        list's cost column and a job page's attempts; VE Owner and Admin)."""
        self._require_ve_money(actor, ve_id)
        job_ids = list(dict.fromkeys(j for j in job_ids if j))[:MAX_JOB_COSTS]
        jobs = {r["job_id"]: {k: v for k, v in r.items() if k != "job_id"}
                for r in self._cost_rows({"ve_id": ve_id, "job_id": {"$in": job_ids}}, "$job_id", "job_id",
                                         len(job_ids))} if job_ids else {}
        attempts = {}
        if len(job_ids) == 1 and job_ids[0] in jobs:
            attempts = {str(d.get("attempt") or 1): {
                            "cost_ucents": int((d.get("cost") or {}).get("total_ucents") or 0),
                            "cost_cents": ucents_to_cents(int((d.get("cost") or {}).get("total_ucents") or 0))}
                        for d in self._attempts.find({"ve_id": ve_id, "job_id": job_ids[0]},
                                                     {"attempt": 1, "cost.total_ucents": 1})}
        return {"ve_id": ve_id, "jobs": jobs, **({"attempts": attempts} if attempts else {})}

    def _cost_rows(self, match: Dict[str, Any], key: Optional[str], name: str, limit: int,
                   newest_first: bool = False, stages: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        rows = self._attempts.aggregate([
            {"$match": match},
            *(stages or []),
            {"$group": {"_id": key, "attempts": {"$sum": 1},
                        "billable_attempts": {"$sum": {"$cond": ["$cost.billable", 1, 0]}},
                        "execution_ms": {"$sum": "$execution_ms"},
                        "cost_ucents": {"$sum": "$cost.total_ucents"},
                        "first_finished": {"$min": "$finished_at_ts"}, "last_finished": {"$max": "$finished_at_ts"}}},
        ])
        out = [{name: r["_id"], "attempts": int(r["attempts"]), "billable_attempts": int(r["billable_attempts"]),
                "execution_ms": round(float(r["execution_ms"] or 0), 3), "cost_ucents": int(r["cost_ucents"] or 0),
                "cost_cents": ucents_to_cents(int(r["cost_ucents"] or 0)),
                "first_finished": r["first_finished"], "last_finished": r["last_finished"]} for r in rows]
        if newest_first:
            out.sort(key=lambda r: -(r["last_finished"] or 0))
        else:
            out.sort(key=lambda r: (-r["cost_ucents"], str(r[name])))
        return out[:limit]

    def ve_bill(self, actor: Actor, ve_id: str, period_index: int) -> Dict[str, Any]:
        self._require_ve_money(actor, ve_id)
        return self._bills.get(ve_id, period_index)

    def ve_stored(self, actor: Actor, ve_id: str) -> Dict[str, Any]:
        """What the VE keeps besides its own bill lines: job results (its
        results bucket, billed as the owner's storage) and active object
        state. Sizes = the last hourly figure; the cost this rental period
        only for the VE Owner / Admin."""
        role = self._authz.require_ve(actor, ve_id, R.VIEWER)
        money = actor.key_id is None and R.at_least(role, R.ADMIN)
        current = self._current_or_none(ve_id)
        start = current["period_start"] if current else self._now_fn() - 30 * DAY
        out: Dict[str, Any] = {"ve_id": ve_id, "since": start}
        for part, col, match in (("results", self._storage_sizes, {"bucket_id": results_bucket_for(ve_id)}),
                                 ("object_state", self._object_hours, {"ve_id": ve_id})):
            last = col.find_one(match, sort=[("ts", -1)])
            row: Dict[str, Any] = {"bytes": int(last["bytes"]) if last else 0, "at": last["ts"] if last else None}
            if money:
                total = list(col.aggregate([{"$match": {**match, "ts": {"$gte": start}}},
                                            {"$group": {"_id": None, "u": {"$sum": "$cost.total_ucents"}}}]))
                row["period_ucents"] = int(total[0]["u"] or 0) if total else 0
            out[part] = row
        out["platform_buckets"] = self._platform_buckets(ve_id)
        return out

    def _platform_buckets(self, ve_id: str) -> Optional[List[Dict[str, Any]]]:
        """The VE's three buckets the platform looks after (read-only to
        people): its code, its jobs' results and its objects' big values --
        figures from axo_storage now (None when it can't answer)."""
        if self._bucket_stats is None:
            return None
        names = {"functions": code_bucket_for(ve_id), "results": results_bucket_for(ve_id),
                 "objects": objects_bucket_for(ve_id)}
        try:
            stats = self._bucket_stats(list(names.values()))
        except Exception:                            # noqa: BLE001 -- storage down: the panel shows the rest
            return None
        return [{"kind": kind, "bucket": name, "bytes": int((stats.get(name) or {}).get("used_bytes") or 0),
                 "files": int((stats.get(name) or {}).get("files") or 0),
                 "last_change": (stats.get(name) or {}).get("last_change")} for kind, name in names.items()]

    def my_ve_buckets(self, actor: Actor) -> List[Dict[str, Any]]:
        """The Buckets page's "VE buckets" tab: the three platform buckets of
        every VE the person has a role in (read-only; one storage call for
        all). Sizes and file counts only for the VE's Owner / Admin -- anyone
        else sees their own results through the VE (``/results``)."""
        self._person(actor)
        cards = [c for c in self._ves.list_for(actor) if c.get("my_role")]
        names: Dict[str, tuple] = {}
        for card in cards:
            for kind, name in (("functions", code_bucket_for(card["ve_id"])),
                               ("results", results_bucket_for(card["ve_id"])),
                               ("objects", objects_bucket_for(card["ve_id"]))):
                names[name] = (card, kind)
        stats: Dict[str, Any] = {}
        if self._bucket_stats is not None and names:
            try:
                stats = self._bucket_stats(list(names)) or {}
            except Exception:                        # noqa: BLE001 -- storage down: names without figures
                stats = {}
        out = []
        for name, (card, kind) in names.items():
            full = R.at_least(card["my_role"], R.ADMIN)
            figures = stats.get(name) or {}
            out.append({"ve_id": card["ve_id"], "ve_name": card.get("name"), "my_role": card["my_role"],
                        "kind": kind, "bucket": name, "read_only": True,
                        "bytes": int(figures.get("used_bytes") or 0) if full and figures else None,
                        "files": int(figures.get("files") or 0) if full and figures else None,
                        "last_change": figures.get("last_change"), "own_results_only": kind == "results" and not full})
        return out

    def _require_ve_money(self, actor: Actor, ve_id: str) -> None:
        self._person(actor)
        self._authz.require_ve(actor, ve_id, R.ADMIN)

    # ── the dashboard's "My VEs" ─────────────────────────────────────────────
    def my_overview(self, actor: Actor) -> List[Dict[str, Any]]:
        """Every VE the person has a role in, as its card, plus this period's
        bill so far where the person may see money (VE Owner / Admin)."""
        self._person(actor)
        out = []
        for card in self._ves.list_for(actor):
            role = card.get("my_role")
            if not role:
                continue                       # Platform Admins see every VE in lists, not here
            current = self._current_or_none(card["ve_id"]) if R.at_least(role, R.ADMIN) else None
            out.append({**card, "current_total_cents": current["total_cents"] if current else None,
                        "current_usage": (current or {}).get("usage")})
        return out

    # ── My billing / Team billing ────────────────────────────────────────────
    def my_billing(self, actor: Actor) -> Dict[str, Any]:
        self._person(actor)
        return self._payer_view(V.OWNER_USER, actor.user_id)

    def team_billing(self, actor: Actor, team_id: str) -> Dict[str, Any]:
        self._person(actor)
        self._teams.require(actor, team_id, R.ADMIN)
        return {**self._payer_view(V.OWNER_TEAM, team_id), "spending": self._ves.spending(actor, team_id)}

    def my_storage_bill(self, actor: Actor, month: str) -> Dict[str, Any]:
        self._person(actor)
        return self._storage_bill(V.OWNER_USER, actor.user_id, month)

    def team_storage_bill(self, actor: Actor, team_id: str, month: str) -> Dict[str, Any]:
        self._person(actor)
        self._teams.require(actor, team_id, R.ADMIN)
        return self._storage_bill(V.OWNER_TEAM, team_id, month)

    def _payer_view(self, kind: str, owner_id: str) -> Dict[str, Any]:
        """Every VE this payer owns (open bill + past bills, deleted VEs'
        bills included) and its storage bills."""
        live = []
        live_ids = set()
        for doc in self._ves_col.find({"owner.kind": kind, "owner.id": owner_id,
                                       "desired": {"$ne": V.DESIRED_DELETED}}, {"_id": 1, "name": 1}).sort("name", 1):
            live.append({"ve_id": doc["_id"], "name": doc.get("name"), "current": self._current_or_none(doc["_id"])})
            live_ids.add(doc["_id"])
        past = [{**{k: v for k, v in d.items() if k != "_id"}, "bill_id": d["_id"],
                 "ve_deleted": d.get("ve_id") not in live_ids}
                for d in self._bill_col.find({"owner.kind": kind, "owner.id": owner_id}).sort("period_end", -1)
                .limit(100)]
        open_cents = sum((v["current"] or {}).get("total_cents", 0) for v in live)
        storage_now = self._storage.current(kind, owner_id)
        return {"payer": {"kind": kind, "id": owner_id},
                "ves": live, "ve_bills": past,
                "storage": {"current": storage_now, "bills": self._storage.list(kind, owner_id)},
                "open_total_cents": open_cents + storage_now["total_cents"]}

    def _storage_bill(self, kind: str, owner_id: str, month: str) -> Dict[str, Any]:
        if len(month) != 7 or month[4] != "-":
            raise InvalidError("month must look like 2026-10")
        if month == month_name(self._now_fn()):
            return self._named(self._storage.current(kind, owner_id))
        return self._named(self._storage.get(f"storage:{kind}:{owner_id}:{month}"))

    # ── one bill (the Billing page's bill page) ──────────────────────────────
    def bill(self, actor: Actor, bill_id: str) -> Dict[str, Any]:
        """Any bill by its id, for whoever pays it (the person, or the Team
        Owner / Admins): a VE bill (``<ve>:<period>``, ``<ve>:late:<ts>``)
        or a storage bill (``storage:<kind>:<owner>:<YYYY-MM>``; this
        month's = the open one, live)."""
        self._person(actor)
        if bill_id.startswith("storage:"):
            _, kind, rest = bill_id.split(":", 2)
            owner_id, _, month = rest.rpartition(":")
            self._require_payer(actor, {"kind": kind, "id": owner_id})
            return {**self._storage_bill(kind, owner_id, month), "type": "storage"}
        doc = self._bill_col.find_one({"_id": bill_id})
        if doc is None:
            raise NotFoundError("no such bill")
        self._require_payer(actor, doc.get("owner") or {})
        out = {**{k: v for k, v in doc.items() if k != "_id"}, "bill_id": doc["_id"], "type": "ve"}
        return {**out, "ve_deleted": self._ve_deleted(doc.get("ve_id"))}

    def _require_payer(self, actor: Actor, owner: Dict[str, Any]) -> None:
        """Only the payer sees a bill (not found otherwise: no hint it exists)."""
        if owner.get("kind") == V.OWNER_USER and owner.get("id") == actor.user_id:
            return
        if owner.get("kind") == V.OWNER_TEAM and owner.get("id"):
            try:
                self._teams.require(actor, owner["id"], R.ADMIN)
                return
            except (ForbiddenError, NotFoundError):
                pass
        raise NotFoundError("no such bill")

    def _ve_deleted(self, ve_id: Optional[str]) -> bool:
        doc = self._ves_col.find_one({"_id": ve_id}, {"desired": 1}) if ve_id else None
        return doc is None or doc.get("desired") == V.DESIRED_DELETED

    def _named(self, bill: Dict[str, Any]) -> Dict[str, Any]:
        """Storage bill lines carry the bucket's storage key; add the name
        people know it by (its current one)."""
        rows = (bill.get("storage") or {}).get("buckets") or []
        ids = [r["bucket_id"] for r in rows]
        names = {d["_id"]: d.get("full_name") for d in self._buckets_col.find({"_id": {"$in": ids}}, {"full_name": 1})}
        for row in rows:
            row["name"] = names.get(row["bucket_id"]) or row["bucket_id"]
        return bill

    # ── Admin revenue ────────────────────────────────────────────────────────
    def admin_revenue(self, actor: Actor, start: float, end: float, limit: int = 10) -> Dict[str, Any]:
        """Plan fees per plan, usage per VE / owner, storage and object state per payer, and
        the top spenders over ``start``..``end`` (by when each item happened:
        a fee when charged, an attempt when it finished, storage by its
        minute / hour)."""
        self._person(actor)
        if actor.platform_role != R.PLATFORM_ADMIN:
            raise ForbiddenError("Platform Admins only")
        if end <= start or end - start > MAX_REVENUE_WINDOW_S:
            raise InvalidError("the window must be positive and at most 366 days")
        fees_by_plan: Dict[str, Dict[str, int]] = {}
        payers: Dict[str, Dict[str, int]] = {}
        owners = self._ve_owners()
        for c in self._charges.find({"at": {"$gte": start, "$lt": end}}):
            row = fees_by_plan.setdefault(c.get("plan_id") or "", {"lines": 0, "amount_cents": 0, "ves": 0})
            row["lines"] += 1
            row["amount_cents"] += int(c["amount_cents"])
            _add(payers, owners.get(c["ve_id"], "unknown"), "fees_ucents", int(c["amount_cents"]) * 1_000_000)
        for plan_id, ves in self._distinct_ves_by_plan(start, end).items():
            fees_by_plan.setdefault(plan_id, {"lines": 0, "amount_cents": 0, "ves": 0})["ves"] = ves
        usage_by_ve: List[Dict[str, Any]] = []
        for r in self._attempts.aggregate([
                {"$match": {"finished_at_ts": {"$gte": start, "$lt": end}}},
                {"$group": {"_id": "$ve_id", "attempts": {"$sum": 1},
                            "billable_attempts": {"$sum": {"$cond": ["$cost.billable", 1, 0]}},
                            "usage_ucents": {"$sum": "$cost.total_ucents"}}}]):
            usage_by_ve.append({"ve_id": r["_id"], "owner": owners.get(r["_id"], "unknown"),
                                "attempts": int(r["attempts"]), "billable_attempts": int(r["billable_attempts"]),
                                "usage_ucents": int(r["usage_ucents"] or 0)})
            _add(payers, owners.get(r["_id"], "unknown"), "usage_ucents", int(r["usage_ucents"] or 0))
        for col in (self._storage_usage, self._storage_sizes):
            for r in col.aggregate([{"$match": {"ts": {"$gte": start, "$lt": end}}},
                                    {"$group": {"_id": "$payer_key", "u": {"$sum": "$cost.total_ucents"}}}]):
                _add(payers, r["_id"], "storage_ucents", int(r["u"] or 0))
        for r in self._object_hours.aggregate([{"$match": {"ts": {"$gte": start, "$lt": end}}},
                                               {"$group": {"_id": "$ve_id", "u": {"$sum": "$cost.total_ucents"}}}]):
            _add(payers, owners.get(r["_id"], "unknown"), "object_state_ucents", int(r["u"] or 0))
        spenders = []
        for key, parts in payers.items():
            total = sum(parts.values())
            spenders.append({"payer_key": key, **parts, "total_ucents": total, "total_cents": ucents_to_cents(total)})
        spenders.sort(key=lambda r: (-r["total_ucents"], r["payer_key"]))
        usage_by_ve.sort(key=lambda r: (-r["usage_ucents"], str(r["ve_id"])))
        usage_by_owner: Dict[str, int] = {}
        for row in usage_by_ve:
            usage_by_owner[row["owner"]] = usage_by_owner.get(row["owner"], 0) + row["usage_ucents"]
        totals = {k: sum(p.get(k, 0) for p in payers.values())
                  for k in ("fees_ucents", "usage_ucents", "storage_ucents", "object_state_ucents")}
        grand = sum(totals.values())
        return {
            "start": start, "end": end,
            "fees_by_plan": [{"plan_id": k, **v} for k, v in sorted(fees_by_plan.items())],
            "usage_by_ve": usage_by_ve[:limit],
            "usage_by_owner": [{"owner": k, "usage_ucents": v} for k, v in
                               sorted(usage_by_owner.items(), key=lambda kv: -kv[1])][:limit],
            "top_spenders": spenders[:limit],
            "totals": {**totals, "total_ucents": grand, "total_cents": ucents_to_cents(grand)},
        }

    def _ve_owners(self) -> Dict[str, str]:
        return {d["_id"]: f"{(d.get('owner') or {}).get('kind')}:{(d.get('owner') or {}).get('id')}"
                for d in self._ves_col.find({}, {"owner": 1})}

    def _distinct_ves_by_plan(self, start: float, end: float) -> Dict[str, int]:
        out: Dict[str, set] = {}
        for c in self._charges.find({"at": {"$gte": start, "$lt": end}}, {"plan_id": 1, "ve_id": 1}):
            out.setdefault(c.get("plan_id") or "", set()).add(c["ve_id"])
        return {k: len(v) for k, v in out.items()}

    # ── helpers ──────────────────────────────────────────────────────────────
    def _current_or_none(self, ve_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._bills.current(ve_id)
        except NotFoundError:
            return None                        # no rental period (e.g. created before rentals)

    @staticmethod
    def _person(actor: Actor) -> None:
        if actor.key_id is not None:
            raise ForbiddenError("billing is only shown to a signed-in person, not to an API key")
        if actor.password_change_required:
            raise ForbiddenError("change your password first", code="PASSWORD_CHANGE_REQUIRED")


def _empty_cost() -> Dict[str, Any]:
    return {"attempts": 0, "billable_attempts": 0, "execution_ms": 0.0, "cost_ucents": 0, "cost_cents": 0,
            "first_finished": None, "last_finished": None}


def _add(payers: Dict[str, Dict[str, int]], key: str, field: str, ucents: int) -> None:
    row = payers.setdefault(key, {"fees_ucents": 0, "usage_ucents": 0, "storage_ucents": 0, "object_state_ucents": 0})
    row[field] += ucents
