from __future__ import annotations

from typing import Any, Dict

from pymongo.database import Database

from axo_control_plane.domain.errors import ConflictError
from axo_control_plane.domain.plans import Resources

_DOC = "platform"


class CapacityLedger:
    """The platform's hardware that can be handed out to VEs, and how much of
    it is reserved (each VE's quota + plan base). No overbooking: a
    reservation that doesn't fit is refused. Reserving is one conditional
    update on a single document, so two Control Plane copies selling at the
    same moment can never both take the last cores."""

    def __init__(self, db: Database, total: Resources) -> None:
        self._col = db["capacity"]
        self._total = total
        self._col.update_one({"_id": _DOC}, {"$setOnInsert": {"cpu_cores": 0.0, "memory_mb": 0}}, upsert=True)

    @property
    def total(self) -> Resources:
        return self._total

    def reserved(self) -> Resources:
        doc = self._col.find_one({"_id": _DOC}) or {}
        return Resources(round(float(doc.get("cpu_cores", 0.0)), 3), int(doc.get("memory_mb", 0)))

    def available(self) -> Resources:
        return self._total - self.reserved()

    def summary(self) -> Dict[str, Any]:
        return {"total": self._total.to_dict(), "reserved": self.reserved().to_dict(),
                "available": self.available().to_dict()}

    def reserve(self, amount: Resources) -> None:
        result = self._col.update_one(
            {
                "_id": _DOC,
                "cpu_cores": {"$lte": self._total.cpu_cores - amount.cpu_cores + 1e-9},
                "memory_mb": {"$lte": self._total.memory_mb - amount.memory_mb},
            },
            {"$inc": {"cpu_cores": amount.cpu_cores, "memory_mb": amount.memory_mb}},
        )
        if result.modified_count != 1:
            available = self.available()
            raise ConflictError(
                f"not enough platform capacity: needs {amount.describe()}, {available.describe()} available",
                code="CAPACITY_FULL", details={"needed": amount.to_dict(), "available": available.to_dict()},
            )

    def release(self, amount: Resources) -> None:
        self._col.update_one({"_id": _DOC}, {"$inc": {"cpu_cores": -amount.cpu_cores, "memory_mb": -amount.memory_mb}})

    def change(self, old: Resources, new: Resources) -> None:
        """Quota/plan change: reserve the growth first, release the shrink after."""
        grow = Resources(max(0.0, new.cpu_cores - old.cpu_cores), max(0, new.memory_mb - old.memory_mb))
        shrink = Resources(max(0.0, old.cpu_cores - new.cpu_cores), max(0, old.memory_mb - new.memory_mb))
        if grow.cpu_cores or grow.memory_mb:
            self.reserve(grow)
        if shrink.cpu_cores or shrink.memory_mb:
            self.release(shrink)
