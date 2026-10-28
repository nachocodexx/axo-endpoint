from axo_endpoint.core.result_store import ResultStore
from axo_endpoint.core.results import FunctionResult
from axo_endpoint.core.storage import StorageKey


def test_finished_results_are_forgotten_after_their_keep_time_pending_ones_never():
    now = [100.0]
    store = ResultStore(now_fn=lambda: now[0])
    store.put(StorageKey(id="done"), FunctionResult(job_id="done", ok=True, output={"x": 1}))
    store.put(StorageKey(id="running"), FunctionResult(job_id="running", ok=False, error="PENDING"))
    now[0] = 100.0 + 3599
    assert store.prune(3600) == 0
    now[0] = 100.0 + 3601
    assert store.prune(3600) == 1
    assert store.get(StorageKey(id="done")).unwrap() is None
    assert store.get(StorageKey(id="running")).unwrap() is not None
