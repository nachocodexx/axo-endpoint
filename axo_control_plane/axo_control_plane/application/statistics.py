from __future__ import annotations

import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from axo_shared.metrics import StatSummary, Tier, bin_start
from pymongo.database import Database

from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.metric_tiers import tier_collection
from axo_control_plane.domain import roles as R
from axo_control_plane.domain import settings as S
from axo_control_plane.domain.errors import InvalidError, NotFoundError

DAY = 86400.0
VeIds = Union[str, Sequence[str]]               # one VE, or several (the person's dashboard)
MAX_RECORD_WINDOW_S = 90 * DAY            # top lists / heatmap read attempt records
MAX_BINS_PER_POINT = 6                     # a chart point merges at most this many bins of its tier
STATISTICS = ("count", "total", "avg", "std", "min", "max", "median", "p90", "p95", "p99")
DEFAULT_STATISTICS = ("count", "avg", "median", "p95", "p99")
# Order of the per-job time bar; any other layer goes after these.
LAYER_ORDER = ("control_plane", "vem", "endpoint", "runner")
TOP_BY = ("function", "version", "user", "api_key", "outcome", "bucket")
TOP_SORT = ("attempts", "execution_ms", "cost")


def _zone(name: str) -> ZoneInfo:
    """An IANA time zone (the person's ``time_zone`` setting)."""
    if not S.is_time_zone(name):
        raise InvalidError(f"{name!r} isn't a known time zone (e.g. America/Mexico_City)", details={"time_zone": name})
    return ZoneInfo(name)


def statistic_of(stats: StatSummary, statistic: str) -> Optional[float]:
    if statistic == "count":
        return float(stats.count)
    if not stats.count:
        return None
    if statistic == "total":
        return stats.sum
    if statistic == "avg":
        return stats.mean()
    if statistic == "std":
        return stats.std()
    if statistic == "min":
        return stats.min
    if statistic == "max":
        return stats.max
    if statistic == "median":
        return stats.quantile(0.5)
    return stats.quantile(int(statistic[1:]) / 100)          # p90 / p95 / p99


class Statistics:
    """The figures behind the VE pages and the admin dashboard (upgrade.md
    13.5):

    - **charts over time and percentiles** come from the time bins
      (``metric_minutes`` -> 15 min -> 1 day, see ``MetricTiers``), filtered
      or split by their dimensions (endpoint, function, version, outcome,
      layer, ...). The finest tier still holding the start of the window is
      used, stepping up to a coarser one when there would be too many
      points; values stay exact (counts, sums, min/max) or within 1 %
      (percentiles), whatever the window;
    - **top lists, per user / API key, the hour x weekday heatmap** come from
      the job attempt records, for windows of up to 90 days;
    - **one job's "where did the time go" bar** comes from its own records.

    The VE's plan retention hides older data (read-time only, nothing is
    deleted). Money (``cost``) is only included when asked (the caller
    decides who may see it)."""

    def __init__(self, db: Database, catalog: PlanCatalog, tiers: List[Tier], max_points: int = 500,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._db = db
        self._ves = db["virtual_environments"]
        self._attempts = db["job_attempts"]
        self._catalog = catalog
        self._tiers = tiers
        self._max_points = max_points
        self._now_fn = now_fn

    # ── charts (time bins) ───────────────────────────────────────────────────
    def chart(self, ve_id: VeIds, metric: str, start: float, end: float,
              statistics: Sequence[str] = DEFAULT_STATISTICS, dims: Optional[Dict[str, str]] = None,
              group_by: Optional[str] = None, step_s: Optional[int] = None,
              component_kind: Optional[str] = None) -> Dict[str, Any]:
        """One point per ``step_s`` (chosen when not given) and per value of
        ``group_by``: the asked statistics of ``metric`` in that step."""
        _check_statistics(statistics)
        starts, start, end = self._scope(ve_id, start, end)
        tier, step = self._resolution(start, end, step_s)
        merged: Dict[Tuple[float, Optional[str]], StatSummary] = {}
        for doc_start, stats, group in self._bins(starts, metric, end, tier, dims, group_by, component_kind):
            _merge_into(merged, (bin_start(doc_start, step), group), stats)
        points = [{"t": t, "group": group, "values": {s: statistic_of(stats, s) for s in statistics}}
                  for (t, group), stats in sorted(merged.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))]
        return {"ve_id": ve_id, "metric": metric, "start": start, "end": end, "step_s": step,
                "bin_width_s": tier.width_s, "group_by": group_by, "points": points}

    def summary(self, ve_id: VeIds, metric: str, start: float, end: float,
                statistics: Sequence[str] = DEFAULT_STATISTICS, dims: Optional[Dict[str, str]] = None,
                group_by: Optional[str] = None, component_kind: Optional[str] = None) -> Dict[str, Any]:
        """The whole window merged into one value set (per ``group_by``
        value): e.g. p95 response time per function over the last 30 days."""
        _check_statistics(statistics)
        starts, start, end = self._scope(ve_id, start, end)
        tier = self._covering_tier(start)
        merged: Dict[Optional[str], StatSummary] = {}
        for _, stats, group in self._bins(starts, metric, end, tier, dims, group_by, component_kind):
            _merge_into(merged, group, stats)
        groups = [{"group": group, "values": {s: statistic_of(stats, s) for s in statistics}}
                  for group, stats in sorted(merged.items(), key=lambda kv: str(kv[0]))]
        return {"ve_id": ve_id, "metric": metric, "start": start, "end": end, "bin_width_s": tier.width_s,
                "group_by": group_by, "groups": groups}

    def _bins(self, starts: Dict[str, float], metric: str, end: float, tier: Tier,
              dims: Optional[Dict[str, str]], group_by: Optional[str],
              component_kind: Optional[str]) -> Iterable[Tuple[float, StatSummary, Optional[str]]]:
        collection = tier_collection("metrics", tier.width_s, self._tiers)
        query = _per_ve(starts, "bin_start", end, floor_s=tier.width_s)
        if component_kind:
            query["component_kind"] = component_kind
        wanted = dict(dims or {})
        for doc in self._db[collection].find(query, {"bin_start": 1, "summaries": 1}):
            for s in doc.get("summaries") or []:
                if s.get("metric") != metric:
                    continue
                have = s.get("dims") or {}
                if any(have.get(k) != v for k, v in wanted.items()):
                    continue
                if group_by and group_by not in have:
                    continue
                yield float(doc["bin_start"]), StatSummary.from_dict(s["stats"]), \
                    (have.get(group_by) if group_by else None)

    def _covering_tier(self, start: float) -> Tier:
        """The finest tier whose keep time still holds ``start``."""
        now = self._now_fn()
        for tier in self._tiers:
            if tier.retention_s is None or start >= now - tier.retention_s:
                return tier
        return self._tiers[-1]

    def _resolution(self, start: float, end: float, step_s: Optional[int]) -> Tuple[Tier, int]:
        tier = self._covering_tier(start)
        candidates = self._tiers[self._tiers.index(tier):]
        if step_s is not None:
            if step_s <= 0:
                raise InvalidError("step_s must be positive")
            for t in candidates:
                if t.width_s <= step_s and step_s % t.width_s == 0:
                    return t, int(step_s)
            raise InvalidError(f"step_s must be a multiple of {tier.width_s} s for this window")
        span = end - start
        for t in candidates:
            # A point may merge a few bins (7 days = 30-min points from the
            # 15-min tier, not 7 daily ones); past that, a coarser tier.
            if span / t.width_s <= self._max_points * MAX_BINS_PER_POINT or t is candidates[-1]:
                step = t.width_s * max(1, math.ceil(span / t.width_s / self._max_points))
                return t, int(step)
        raise AssertionError("unreachable")

    # ── records (top lists, heatmap) ─────────────────────────────────────────
    def top(self, ve_id: VeIds, by: str, start: float, end: float, limit: int = 10, sort: str = "attempts",
            money: bool = False) -> Dict[str, Any]:
        """Top functions / versions / users / API keys / outcomes / buckets
        of the window, from the attempt records."""
        if by not in TOP_BY:
            raise InvalidError(f"by must be one of {', '.join(TOP_BY)}")
        if sort not in TOP_SORT or (sort == "cost" and not money):
            raise InvalidError(f"sort must be one of {', '.join(s for s in TOP_SORT if money or s != 'cost')}")
        starts, start, end = self._record_window(ve_id, start, end)
        match = _per_ve(starts, "finished_at_ts", end)
        if by == "bucket":
            rows = self._top_buckets(match, limit)
            return {"ve_id": ve_id, "by": by, "start": start, "end": end, "rows": rows}
        if by == "user":
            match["started_by_type"] = "user"
        elif by == "api_key":
            match["started_by_type"] = "api_key"
        key = {"function": {"function_id": "$function_id"},
               "version": {"function_id": "$function_id", "function_version": "$function_version"},
               "user": {"user_id": "$started_by_id"}, "api_key": {"api_key_id": "$started_by_id"},
               "outcome": {"outcome": "$outcome"}}[by]
        group: Dict[str, Any] = {
            "_id": key, "attempts": {"$sum": 1},
            "billable_attempts": {"$sum": {"$cond": ["$cost.billable", 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$outcome", "completed"]}, 0, 1]}},
            "execution_ms": {"$sum": "$execution_ms"},
        }
        if money:
            group["cost_ucents"] = {"$sum": "$cost.total_ucents"}
        sort_field = {"attempts": "attempts", "execution_ms": "execution_ms", "cost": "cost_ucents"}[sort]
        rows = []
        for r in self._attempts.aggregate([{"$match": match}, {"$group": group},
                                           {"$sort": {sort_field: -1}}, {"$limit": int(limit)}]):
            row = {**r.pop("_id"), **r}
            row["execution_ms"] = round(float(row["execution_ms"] or 0.0), 3)
            row["avg_execution_ms"] = round(row["execution_ms"] / row["attempts"], 3) if row["attempts"] else None
            if money:
                row["cost_ucents"] = int(row["cost_ucents"] or 0)
            rows.append(row)
        return {"ve_id": ve_id, "by": by, "start": start, "end": end, "sort": sort, "rows": rows}

    def _top_buckets(self, match: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Bytes each bucket gave to / took from this VE's jobs."""
        totals: Dict[str, Dict[str, int]] = {}
        for field in ("bytes_read", "bytes_written"):
            for r in self._attempts.aggregate([
                    {"$match": {**match, field: {"$exists": True, "$ne": {}}}},
                    {"$project": {"kv": {"$objectToArray": f"${field}"}}}, {"$unwind": "$kv"},
                    {"$group": {"_id": "$kv.k", "bytes": {"$sum": "$kv.v"}, "attempts": {"$sum": 1}}}]):
                row = totals.setdefault(r["_id"], {"bytes_read": 0, "bytes_written": 0, "attempts": 0})
                row[field] = int(r["bytes"])
                row["attempts"] = max(row["attempts"], int(r["attempts"]))
        rows = [{"bucket": name, **values} for name, values in totals.items()]
        rows.sort(key=lambda r: (-(r["bytes_read"] + r["bytes_written"]), r["bucket"]))
        return rows[:int(limit)]

    def heatmap(self, ve_id: VeIds, start: float, end: float, time_zone: str = "UTC") -> Dict[str, Any]:
        """Attempts per weekday (0 = Monday) x hour in the viewer's time zone
        (an IANA name: daylight-saving changes inside the window count right).
        Counted per UTC hour in Mongo, then placed on the local clock here."""
        zone = _zone(time_zone)
        starts, start, end = self._record_window(ve_id, start, end)
        cells = [[0] * 24 for _ in range(7)]
        for r in self._attempts.aggregate([
                {"$match": _per_ve(starts, "finished_at_ts", end)},
                {"$group": {"_id": {"$floor": {"$divide": ["$finished_at_ts", 3600]}}, "attempts": {"$sum": 1}}}]):
            local = datetime.fromtimestamp(float(r["_id"]) * 3600, tz=zone)
            cells[local.weekday()][local.hour] += int(r["attempts"])
        return {"ve_id": ve_id, "start": start, "end": end, "time_zone": time_zone,
                "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "attempts": cells}

    # ── one job ──────────────────────────────────────────────────────────────
    def job_times(self, ve_id: str, job_id: str, money: bool = False) -> Dict[str, Any]:
        """Where one job's time went, per attempt: the wait in each layer
        (Control Plane, VEM, endpoint, runner) then the execution."""
        docs = list(self._attempts.find({"ve_id": ve_id, "job_id": job_id}).sort("attempt", 1))
        if not docs:
            raise NotFoundError("no record of that job (yet)")
        attempts = []
        for doc in docs:
            waits = dict(doc.get("layer_waits_ms") or {})
            ordered = [layer for layer in LAYER_ORDER if layer in waits] + \
                sorted(layer for layer in waits if layer not in LAYER_ORDER)
            parts = [{"part": layer, "kind": "wait", "ms": round(float(waits[layer]), 3)} for layer in ordered]
            parts.append({"part": "execution", "kind": "run", "ms": round(float(doc.get("execution_ms") or 0.0), 3)})
            attempt = {
                "attempt": doc.get("attempt"), "outcome": doc.get("outcome"),
                "function_id": doc.get("function_id"), "function_version": doc.get("function_version"),
                "endpoint_id": doc.get("endpoint_id"), "received_at": doc.get("received_at"),
                "started_at": doc.get("started_at"), "finished_at": doc.get("finished_at"),
                "parts": parts, "total_ms": round(sum(p["ms"] for p in parts), 3),
                "bytes_read": doc.get("bytes_read") or {}, "bytes_written": doc.get("bytes_written") or {},
                "cache_hits": int(doc.get("cache_hits") or 0), "cache_misses": int(doc.get("cache_misses") or 0),
            }
            if money:
                attempt["cost_ucents"] = int((doc.get("cost") or {}).get("total_ucents") or 0)
            attempts.append(attempt)
        return {"ve_id": ve_id, "job_id": job_id, "trace_id": docs[0].get("trace_id"), "attempts": attempts}

    # ── windows ──────────────────────────────────────────────────────────────
    def _scope(self, ve_ids: VeIds, start: float, end: float) -> Tuple[Dict[str, float], float, float]:
        """``start``..``end`` checked, and each VE's start cut to its plan's
        retention (several VEs: the person's dashboard). Returns the start
        per VE, the earliest of them, and the end."""
        if end <= start:
            raise InvalidError("end must be after start")
        wanted = [ve_ids] if isinstance(ve_ids, str) else list(ve_ids)
        docs = {d["_id"]: d for d in self._ves.find({"_id": {"$in": wanted}}, {"plan_id": 1})}
        if isinstance(ve_ids, str) and ve_ids not in docs:
            raise NotFoundError("no such VE")
        starts: Dict[str, float] = {}
        for ve_id, doc in docs.items():
            days = self._catalog.get(doc["plan_id"]).max_retention_days if doc.get("plan_id") else None
            ve_start = max(start, self._now_fn() - days * DAY) if days else start
            if ve_start < end:
                starts[ve_id] = float(ve_start)
        if not starts:
            if not docs and not isinstance(ve_ids, str):
                return {}, float(start), float(end)        # no VEs at all: empty answers
            raise InvalidError("the whole window is older than this VE's retention")
        return starts, min(starts.values()), float(end)

    def _record_window(self, ve_ids: VeIds, start: float, end: float) -> Tuple[Dict[str, float], float, float]:
        if end - start > MAX_RECORD_WINDOW_S:
            raise InvalidError("top lists and the heatmap cover at most 90 days")
        return self._scope(ve_ids, start, end)


def _per_ve(starts: Dict[str, float], field: str, end: float, floor_s: Optional[int] = None) -> Dict[str, Any]:
    """Each VE from its own start (its retention) to ``end``."""
    def window(s: float) -> Dict[str, Any]:
        return {"$gte": bin_start(s, floor_s) if floor_s else s, "$lt": end}
    if not starts:
        return {"ve_id": {"$in": []}}
    if len(starts) == 1:
        (ve_id, s), = starts.items()
        return {"ve_id": ve_id, field: window(s)}
    return {"$or": [{"ve_id": ve_id, field: window(s)} for ve_id, s in starts.items()]}


def _check_statistics(statistics: Sequence[str]) -> None:
    unknown = [s for s in statistics if s not in STATISTICS]
    if unknown or not statistics:
        raise InvalidError(f"statistics must be some of {', '.join(STATISTICS)}")


def _merge_into(merged: Dict[Any, StatSummary], key: Any, stats: StatSummary) -> None:
    current = merged.get(key)
    if current is None:
        merged[key] = stats
    else:
        current.merge(stats)


class VeStatistics:
    """The statistics as each person may see them: any role in the VE sees
    the figures; money (costs) only the VE Owner and Admin (upgrade.md 13.6).

    The ``my_*`` methods cover several VEs at once -- every VE the person
    has a role in, or the ones asked for (the dashboard's "All my VEs") --
    merged on the server so percentiles stay exact. Money only when the
    person is Owner / Admin of every VE included."""

    def __init__(self, statistics: Statistics, authz: Any,
                 list_fn: Optional[Callable[[Any], List[Dict[str, Any]]]] = None) -> None:
        self._stats = statistics
        self._authz = authz
        self._list_fn = list_fn               # VeService.list_for

    def _role(self, actor: Any, ve_id: str) -> str:
        return self._authz.require_ve(actor, ve_id, R.VIEWER)

    def chart(self, actor: Any, ve_id: str, metric: str, start: float, end: float, **kw: Any) -> Dict[str, Any]:
        self._role(actor, ve_id)
        return self._stats.chart(ve_id, metric, start, end, **kw)

    def summary(self, actor: Any, ve_id: str, metric: str, start: float, end: float, **kw: Any) -> Dict[str, Any]:
        self._role(actor, ve_id)
        return self._stats.summary(ve_id, metric, start, end, **kw)

    def top(self, actor: Any, ve_id: str, by: str, start: float, end: float, **kw: Any) -> Dict[str, Any]:
        money = R.at_least(self._role(actor, ve_id), R.ADMIN)
        return self._stats.top(ve_id, by, start, end, money=money, **kw)

    def heatmap(self, actor: Any, ve_id: str, start: float, end: float, **kw: Any) -> Dict[str, Any]:
        self._role(actor, ve_id)
        return self._stats.heatmap(ve_id, start, end, **kw)

    def job_times(self, actor: Any, ve_id: str, job_id: str) -> Dict[str, Any]:
        money = R.at_least(self._role(actor, ve_id), R.ADMIN)
        return self._stats.job_times(ve_id, job_id, money=money)

    # ── several VEs (the person's dashboard) ─────────────────────────────────
    def _mine(self, actor: Any, ve_ids: Optional[Sequence[str]]) -> Tuple[List[str], bool]:
        if self._list_fn is None:
            raise InvalidError("statistics over several VEs are not available")
        roles = {v["ve_id"]: v.get("my_role") for v in self._list_fn(actor) if v.get("my_role")}
        chosen = list(roles) if not ve_ids else [v for v in ve_ids if v in roles]
        if ve_ids and len(chosen) != len(set(ve_ids)):
            raise NotFoundError("no such VE")          # never hint at VEs the person can't see
        money = bool(chosen) and all(R.at_least(roles[v], R.ADMIN) for v in chosen)
        return chosen, money

    def my_chart(self, actor: Any, ve_ids: Optional[Sequence[str]], metric: str, start: float, end: float,
                 **kw: Any) -> Dict[str, Any]:
        ids, _ = self._mine(actor, ve_ids)
        return {**self._stats.chart(ids, metric, start, end, **kw), "ve_ids": ids}

    def my_summary(self, actor: Any, ve_ids: Optional[Sequence[str]], metric: str, start: float, end: float,
                   **kw: Any) -> Dict[str, Any]:
        ids, _ = self._mine(actor, ve_ids)
        return {**self._stats.summary(ids, metric, start, end, **kw), "ve_ids": ids}

    def my_top(self, actor: Any, ve_ids: Optional[Sequence[str]], by: str, start: float, end: float,
               **kw: Any) -> Dict[str, Any]:
        ids, money = self._mine(actor, ve_ids)
        return {**self._stats.top(ids, by, start, end, money=money, **kw), "ve_ids": ids, "money": money}

    def my_heatmap(self, actor: Any, ve_ids: Optional[Sequence[str]], start: float, end: float,
                   **kw: Any) -> Dict[str, Any]:
        ids, _ = self._mine(actor, ve_ids)
        return {**self._stats.heatmap(ids, start, end, **kw), "ve_ids": ids}
