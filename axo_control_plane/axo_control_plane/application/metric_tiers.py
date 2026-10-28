from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from axo_shared.metrics import MetricRecord, StatSummary, Tier, bin_start, freeze_dims, parse_tiers, rollup
from pymongo.database import Database

from axo_control_plane.domain.errors import InvalidError

DEFAULT_CP_TIERS = "1m:7d,15m:90d,1d:forever"

Merge = Callable[[List[Dict[str, Any]]], Dict[str, Any]]


def cp_tiers(spec: str = DEFAULT_CP_TIERS) -> List[Tier]:
    """The CP's tiers (finest first; the 10 s live tier stays in the VEMs)."""
    tiers = parse_tiers(spec)
    if tiers.is_err:
        raise InvalidError(str(tiers.unwrap_err()))
    return tiers.unwrap()


@dataclass(frozen=True)
class Series:
    """One kind of binned data: where each tier lives, which fields say what
    a bin belongs to (besides its time), and how bins merge."""

    name: str
    collections: Sequence[str]        # one per tier, finest first
    key_fields: Tuple[str, ...]
    merge: Merge                      # finer bins -> the fields of one coarser bin
    # Finer bins may only be removed once this holds (e.g. on a closed bill).
    removable: Dict[str, Any] = None  # type: ignore[assignment]


def merge_metric_summaries(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """MetricSummaryReported bins -> one coarser bin: every (metric, dims)
    merged exactly (sums + sketches), the same as axo_shared.metrics.rollup."""
    records = [MetricRecord(metric=s["metric"], dims=freeze_dims(s.get("dims")), bin_start=0.0,
                            width_s=1, stats=StatSummary.from_dict(s["stats"]))
               for doc in docs for s in doc.get("summaries") or []]
    merged = rollup(records, 1)
    if merged.is_err:
        raise InvalidError(str(merged.unwrap_err()))
    return {"summaries": [{"metric": r.metric, "dims": dict(r.dims), "stats": r.stats.to_dict()}
                          for r in merged.unwrap()]}


def sum_fields(*fields: str) -> Merge:
    def merge(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {f: sum(int(d.get(f) or 0) for d in docs) for f in fields}
    return merge


class MetricTiers:
    """Time tiers for the statistics the CP keeps (upgrade.md 13.4): finer
    bins are merged into coarser ones (1 min -> 15 min -> 1 day by default,
    ``AXO_CP_METRIC_TIERS``), with no accuracy lost.

    - A coarser bin is always **rebuilt from all its finer bins** -- never
      added to -- so a repeat, a late finer bin, or a change passing upward
      (15 min -> day) never counts anything twice. A changed bin is marked
      not-rolled again, so the next tier up is rebuilt too.
    - A finer bin is removed only when it is older than its tier's keep time
      **and** already merged (and, for billing rows, on a closed bill).
    - A finer bin that arrives after its tier's keep time (its siblings may
      be gone, so a rebuild would lose them) is dropped, not merged.
    - Run by one CP copy at a time (lease)."""

    def __init__(self, db: Database, tiers: List[Tier], series: Iterable[Series],
                 now_fn: Callable[[], float] = time.time) -> None:
        self._db = db
        self._tiers = tiers
        self._series = list(series)
        for s in self._series:
            if len(s.collections) != len(tiers):
                raise InvalidError(f"series {s.name} needs one collection per tier")
        self._now_fn = now_fn

    def tick(self) -> Dict[str, int]:
        """Rebuild what changed, then remove what's expired. Returns counts."""
        rebuilt = sum(self._rollup(s, i) for s in self._series for i in range(len(self._tiers) - 1))
        removed = sum(self._expire(s, i) for s in self._series for i in range(len(self._tiers)))
        return {"rebuilt": rebuilt, "removed": removed}

    # ── merging ──────────────────────────────────────────────────────────────
    def _rollup(self, series: Series, index: int) -> int:
        finer, coarser = self._tiers[index], self._tiers[index + 1]
        source = self._db[series.collections[index]]
        target = self._db[series.collections[index + 1]]
        cutoff = None if finer.retention_s is None else self._now_fn() - finer.retention_s
        windows: Dict[Tuple[Any, ...], float] = {}
        for doc in source.find({"rolled": {"$ne": True}}):
            if cutoff is not None and doc["bin_start"] < cutoff:
                source.update_one({"_id": doc["_id"]}, {"$set": {"rolled": True, "dropped_late": True}})
                continue
            key = tuple(doc.get(f) for f in series.key_fields)
            windows[key + (bin_start(doc["bin_start"], coarser.width_s),)] = bin_start(doc["bin_start"],
                                                                                    coarser.width_s)
        for window, start in windows.items():
            match = {f: v for f, v in zip(series.key_fields, window)}
            match["bin_start"] = {"$gte": start, "$lt": start + coarser.width_s}
            docs = [d for d in source.find(match) if not d.get("dropped_late")]
            target_id = ":".join(str(v) for v in window[:-1]) + f":{int(start)}"
            fields = {f: v for f, v in zip(series.key_fields, window)}
            target.replace_one({"_id": target_id},
                               {"_id": target_id, **fields, "bin_start": start, "bin_width_s": coarser.width_s,
                                **series.merge(docs), "sources": len(docs), "rolled": False,
                                "rebuilt_at": self._now_fn()},
                               upsert=True)
            source.update_many({"_id": {"$in": [d["_id"] for d in docs]}}, {"$set": {"rolled": True}})
        return len(windows)

    # ── keep times ───────────────────────────────────────────────────────────
    def _expire(self, series: Series, index: int) -> int:
        tier = self._tiers[index]
        if tier.retention_s is None:
            return 0
        query: Dict[str, Any] = {"bin_start": {"$lt": self._now_fn() - tier.retention_s}}
        if index < len(self._tiers) - 1:
            query["rolled"] = True               # never lose what isn't merged up yet
        if index == 0 and series.removable:
            query.update(series.removable)
        return self._db[series.collections[index]].delete_many(query).deleted_count


def default_series() -> List[Series]:
    """What the CP keeps in tiers: the VEMs' minute summaries, and
    axo_storage's per-minute request totals (bin_start = their ``ts``)."""
    return [
        Series("metrics", ("metric_minutes", "metric_bins_900", "metric_bins_86400"),
               ("ve_id", "component_kind", "component_id"), merge_metric_summaries),
        Series("storage_usage", ("storage_usage", "storage_usage_bins_900", "storage_usage_bins_86400"),
               ("bucket_id", "payer_key", "ve_id"),
               sum_fields("requests", "bytes_read", "bytes_written"),
               removable={"bill_id": {"$ne": None}}),
    ]


def series_for(tiers: List[Tier]) -> List[Series]:
    """The default series, with one collection per configured tier."""
    names = {"metrics": ("metric_minutes", "metric_bins_{w}"),
             "storage_usage": ("storage_usage", "storage_usage_bins_{w}")}
    out = []
    for s in default_series():
        first, pattern = names[s.name]
        collections = [first] + [pattern.format(w=t.width_s) for t in tiers[1:]]
        out.append(Series(s.name, collections, s.key_fields, s.merge, s.removable))
    return out


def tier_collection(series: str, width_s: int, tiers: List[Tier]) -> Optional[str]:
    """The collection holding ``series`` bins of ``width_s`` (None if no such tier)."""
    for s in series_for(tiers):
        if s.name == series:
            for tier, name in zip(tiers, s.collections):
                if tier.width_s == width_s:
                    return name
    return None
