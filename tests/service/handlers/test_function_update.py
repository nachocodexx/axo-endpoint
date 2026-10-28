import pytest

from axo_endpoint.core.events import InMemoryEventBus
from axo_endpoint.core.functions import FunctionRegistry
from axo_shared.protocol import Command
from axo_endpoint.core.storage import InMemoryStorageBackend, StorageKey
from axo_endpoint.service.handlers import FunctionUpdateHandler


@pytest.fixture
def registry():
    return FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=InMemoryEventBus())


def test_update_merges_env_vars_and_params_schema(registry):
    registry.register(function_id="add", name="add", code=b"code", now=100.0)
    handler = FunctionUpdateHandler(registry=registry, now_fn=lambda: 200.0)

    result = handler.handle(Command(
        operation="FUNCTION_UPDATE",
        content_type="application/json",
        envelope={
            "function_id": "add", "version": 1,
            "env_vars": {"A": "1"},
            "params_schema": [{"name": "x", "type": "number", "required": False}],
        },
    ))

    assert result.ok is True
    assert result.metadata == {"function_id": "add", "version": 1}

    record = registry.get(StorageKey(id="add", version=1, alias="add")).unwrap()
    assert record.runtime_spec.env_vars == {"A": "1"}
    assert [p.name for p in record.params_schema] == ["x"]


def test_missing_function_id_or_version_returns_error(registry):
    handler = FunctionUpdateHandler(registry=registry)
    result = handler.handle(
        Command(operation="FUNCTION_UPDATE", content_type="application/json", envelope={})
    )
    assert result.ok is False
    assert result.error_name == "MISSING_FIELD"
    assert result.error_code == 1001


def test_update_on_unregistered_function_returns_error(registry):
    handler = FunctionUpdateHandler(registry=registry)
    result = handler.handle(Command(
        operation="FUNCTION_UPDATE", content_type="application/json",
        envelope={"function_id": "missing", "version": 1, "env_vars": {"A": "1"}},
    ))
    assert result.ok is False
    assert result.error_name == "STORAGE_FAILURE"


def test_the_settings_page_replaces_env_vars_keeps_secrets_hidden_and_changes_attempts():
    from axo_endpoint.core.events import InMemoryEventBus
    from axo_endpoint.core.functions import FunctionRegistry
    from axo_endpoint.core.storage import InMemoryStorageBackend
    from axo_endpoint.service.handlers.function_update import FunctionUpdateHandler
    from axo_shared.protocol import Command
    from axo_shared.runtime.spec import RuntimeSpec

    registry = FunctionRegistry(backend=InMemoryStorageBackend(), event_bus=InMemoryEventBus())
    key = registry.register("f", "f", b"def f(a, ctx=None): ...", 0.0,
                            runtime_spec=RuntimeSpec(env_vars={"A": "1", "TOKEN": "s3cret"},
                                                     secret_env=["TOKEN"])).unwrap()
    handler = FunctionUpdateHandler(registry)

    def update(**envelope):
        return handler.handle(Command(operation="FUNCTION_UPDATE", content_type="application/json",
                                      envelope={"function_id": key.id, "version": key.version, **envelope}))

    # the secret isn't sent back (the page never has it): its value stays
    assert update(env_vars={"B": "2", "TOKEN": None}, env_vars_replace=True, secret_env=["TOKEN"],
                  runtime={"max_attempts": 3, "retry_backoff": "exponential", "keep_result": "30d"}).ok
    spec = registry.get(key).unwrap().runtime_spec
    assert spec.env_vars == {"B": "2", "TOKEN": "s3cret"} and spec.secret_env == ["TOKEN"]
    assert (spec.max_attempts, spec.retry_backoff, spec.keep_result) == (3, "exponential", "30d")
    assert spec.public_dict()["env_vars"] == {"B": "2", "TOKEN": None}
    assert not update(runtime={"max_attempts": 0}).ok
    assert not update(runtime={"python_version": "3.12"}).ok
