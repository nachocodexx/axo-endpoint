import pytest

from axo_endpoint.core.consensus.dirty_tracker import DirtyTracker
from axo_endpoint.core.consensus.registry_sync import RegistrySyncBridge, serialize_function_record
from axo_endpoint.core.consensus.state_machine import ClusterState, InMemoryReplicatedStateMachine, encode_state_changes
from axo_endpoint.core.events import InMemoryEventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_shared.functions.lifecycle import FunctionState
from axo_shared.functions.models import FunctionRecord
from axo_shared.protocol import Command
from axo_endpoint.core.storage import InMemoryStorageBackend, StorageKey
from axo_endpoint.service.handlers.state_sync_push import StateSyncPushHandler


@pytest.fixture
def backend():
    return InMemoryStorageBackend()


@pytest.fixture
def registry_sync(backend):
    registry = FunctionRegistry(backend=backend, event_bus=InMemoryEventBus())
    return RegistrySyncBridge(registry=registry, backend=backend, dirty_tracker=DirtyTracker(max_dirty_count=50))


@pytest.fixture
def state_machine():
    return InMemoryReplicatedStateMachine()


@pytest.fixture
def handler(state_machine, registry_sync):
    return StateSyncPushHandler(state_machine=state_machine, registry_sync=registry_sync)


def _record(name="foo", version=1):
    return FunctionRecord(
        code=b"print(1)", function_id=name, name=name, version=version, created_at=1.0,
        state=FunctionState.REGISTERED, runtime_spec=None,
    )


def _push_command(term, function_changes=None):
    return Command(
        operation="STATE_SYNC_PUSH",
        content_type="application/json",
        envelope={"term": term, "leader_ids": ["leader"], "members": ["leader", "self"]},
        payload=encode_state_changes(function_changes or {}),
    )


def test_higher_term_push_applies(handler, state_machine, backend):
    record = _record()
    changes = {"foo:1": serialize_function_record(record)}

    result = handler.handle(_push_command(term=1, function_changes=changes))

    assert result.ok is True
    assert state_machine.snapshot().term == 1
    assert state_machine.snapshot().leader_ids == frozenset({"leader"})


def test_pushed_function_data_lands_in_the_backend(handler, backend):
    record = _record(name="bar", version=2)
    changes = {"bar:2": serialize_function_record(record)}

    handler.handle(_push_command(term=1, function_changes=changes))

    stored = backend.get(StorageKey(id="bar", version=2, alias="bar")).unwrap()
    assert stored == record


def test_stale_term_push_is_rejected_and_state_unchanged(state_machine, registry_sync):
    # Bump local state to term=5 first.
    state_machine.apply_remote(ClusterState(term=5, leader_ids=frozenset({"leader"}), version=1))
    handler = StateSyncPushHandler(state_machine=state_machine, registry_sync=registry_sync)

    changes = {"foo:1": serialize_function_record(_record())}
    result = handler.handle(_push_command(term=1, function_changes=changes))

    assert result.ok is False
    assert result.error_code == 6002
    assert result.error_name == "STALE_TERM"
    assert state_machine.snapshot().term == 5
    assert state_machine.snapshot().functions == {}
