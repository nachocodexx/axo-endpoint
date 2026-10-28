"""A VEM's choreography side on mongomock, with fake endpoints and storage."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import mongomock

from axo_shared.metrics import MetricAggregator

from axo_vem.application.choreography.engine import ChoreographyEngine
from axo_vem.application.choreography.service import ChoreographyService, StepCatalog
from axo_vem.domain.events.models import ChoreographyEdge, ChoreographyGraph, ChoreographyNode
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository

from tests.application.choreography.fakes import FakeBucketStorage, FakeStepJobs


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    def append_to_stream(self, stream: str, event_type: str, data: Dict[str, Any]) -> None:
        self.events.append((stream, event_type, data))

    def types(self) -> List[str]:
        return [e[1] for e in self.events]


class RecordingBroadcaster:
    def __init__(self) -> None:
        self.messages: List[tuple] = []

    def broadcast(self, topic: str, message: Dict[str, Any]) -> None:
        self.messages.append((topic, message))


def fn(node_id: str, function_id: Optional[str] = None, **kw) -> ChoreographyNode:
    return ChoreographyNode(node_id=node_id, kind="function", function_id=function_id or node_id,
                            retry_delay_seconds=kw.pop("retry_delay_seconds", 0.0), **kw)


def obj(node_id: str, object_name: str, method: Optional[str] = None, **kw) -> ChoreographyNode:
    return ChoreographyNode(node_id=node_id, kind="object", object_name=object_name, method=method,
                            retry_delay_seconds=kw.pop("retry_delay_seconds", 0.0), **kw)


def bucket(node_id: str, name: str, **kw) -> ChoreographyNode:
    return ChoreographyNode(node_id=node_id, kind="bucket", bucket_name=name,
                            retry_delay_seconds=kw.pop("retry_delay_seconds", 0.0), **kw)


def graph(nodes, *edges) -> ChoreographyGraph:
    """``edges``: (source, target) or (source, target, {condition / otherwise})."""
    return ChoreographyGraph(nodes=list(nodes), edges=[
        ChoreographyEdge(edge_id=f"e{i}", source_node_id=e[0], target_node_id=e[1], **(e[2] if len(e) > 2 else {}))
        for i, e in enumerate(edges)])


class Harness:
    def __init__(self, workers: int = 16) -> None:
        self.db = mongomock.MongoClient()["vem"]
        self.functions = MongoFunctionRepository(self.db["functions"])
        self.repository = MongoChoreographyRepository(self.db["choreographies"], self.db["choreography_versions"],
                                                      self.db["choreography_names"])
        self.runs = MongoChoreographyRunRepository(self.db["choreography_runs"])
        self.objects: Dict[str, tuple] = {}
        self.catalog = StepCatalog(self.functions, lambda name: self.objects.get(name))
        self.publisher = RecordingPublisher()
        self.broadcaster = RecordingBroadcaster()
        self.jobs = FakeStepJobs()
        self.buckets = FakeBucketStorage()
        self.metrics = MetricAggregator(width_s=10)
        self.service = ChoreographyService(self.repository, self.runs, self.catalog, self.publisher, "ve1")
        self.engine = self.new_engine(workers)

    def new_engine(self, workers: int = 16) -> ChoreographyEngine:
        return ChoreographyEngine(
            self.service, self.repository, self.runs, self.catalog, self.jobs, self.publisher,
            bucket_storage=self.buckets, results=self.jobs, broadcaster=self.broadcaster, metrics=self.metrics,
            virtual_environment_id="ve1", step_timeout_seconds=5.0, poll_interval_seconds=0.005, workers=workers,
        )

    def function(self, function_id: str, handler=None, params: Optional[List[Dict[str, Any]]] = None,
                 version: int = 1) -> None:
        self.db["functions"].update_one({"_id": f"{function_id}:{version}"}, {"$set": {
            "function_id": function_id, "version": version, "name": function_id, "deleted_at": None,
            "params_schema": params}}, upsert=True)
        if handler is not None:
            self.jobs.on(function_id, handler)

    def object(self, name: str, methods: Dict[str, Any]) -> None:
        """``methods``: method name -> (handler, params list, optional list)."""
        cls = {"class_name": "C", "version": 1, "methods": [
            {"name": m, "params": p, "optional": o} for m, (_h, p, o) in methods.items()]}
        self.objects[name] = ({"name": name, "object_id": f"obj-{name}", "class_name": "C", "class_version": 1,
                               "status": "ready"}, cls)
        for method, (handler, _p, _o) in methods.items():
            self.jobs.on(f"{name}.{method}", handler)

    def wait(self, run_id: str, timeout: float = 5.0, until=None) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.engine.run_info(run_id)
            if (until(run) if until else run["status"] not in ("queued", "running")):
                if run["status"] not in ("queued", "running"):
                    # the last steps' answers, read back from their jobs
                    run["result"] = self.engine.run_result(run_id)
                return run
            time.sleep(0.005)
        raise AssertionError(f"run {run_id} still {self.engine.run_info(run_id)['status']}")

    def close(self) -> None:
        self.engine.shutdown()
