from option import Err, Ok

from axo_shared.container.errors import ContainerSpawnError
from axo_shared.container.handle import SpawnedContainerHandle

from axo_vem.application.nodes.launch_endpoint_node import LaunchEndpointNodeUseCase
from axo_vem.domain.compute.endpoint import Endpoint


class _FakeSpawner:
    def __init__(self, result=None):
        self.spawn_calls = []
        self._result = result

    def spawn(self, **kwargs):
        self.spawn_calls.append(kwargs)
        if self._result is not None:
            return self._result
        return Ok(SpawnedContainerHandle(name=kwargs["name"], mode=kwargs["mode"], container_id="c1"))


class _FakeEndpointRepository:
    """Every endpoint of this VEM's one VE."""

    def __init__(self, endpoints=None):
        self._endpoints = endpoints or []

    def list_all(self):
        return list(self._endpoints)

    def save(self, endpoint):
        raise NotImplementedError

    def get(self, endpoint_id):
        raise NotImplementedError


def test_execute_generates_a_unique_endpoint_id_and_spawns():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    result = use_case.execute(api_uri="tcp://axo-vem:6000")

    assert result.is_ok
    launched = result.unwrap()
    assert launched.endpoint_id.startswith("axo-endpoint-")
    assert launched.router_bind == "tcp://0.0.0.0:5555"
    assert len(spawner.spawn_calls) == 1


def test_execute_sets_mesh_identity_env_vars():
    spawner = _FakeSpawner()
    repository = _FakeEndpointRepository([Endpoint(endpoint_id="axo-endpoint-0", status="running", pub_bind="tcp://0.0.0.0:5556")])
    use_case = LaunchEndpointNodeUseCase(
        spawner=spawner, image="axo-endpoint:local", network="axo-net", endpoint_repository=repository,
        virtual_environment_id="ve1",
    )

    result = use_case.execute(
        api_uri="tcp://axo-vem:6000",
        router_port=5565, pub_port=5566, results_port=5567,
    )

    env = spawner.spawn_calls[0]["env"]
    assert env["AXO_ENDPOINT_ID"] == result.unwrap().endpoint_id
    assert env["AXO_ENDPOINT_API_URI"] == "tcp://axo-vem:6000"
    assert env["AXO_ENDPOINT_ROUTER_BIND"] == "tcp://0.0.0.0:5565"
    assert env["AXO_ENDPOINT_PUB_BIND"] == "tcp://0.0.0.0:5566"
    assert env["AXO_ENDPOINT_SUB_CONNECT"] == "tcp://axo-endpoint-0:5556"
    assert env["AXO_ENDPOINT_VIRTUAL_ENV_ID"] == "ve1"


def test_execute_derives_sub_connect_only_from_running_peers_with_pub_bind():
    spawner = _FakeSpawner()
    repository = _FakeEndpointRepository([
            Endpoint(endpoint_id="axo-endpoint-0", status="running", pub_bind="tcp://0.0.0.0:5556"),
            Endpoint(endpoint_id="axo-endpoint-1", status="stopped", pub_bind="tcp://0.0.0.0:5556"),
            Endpoint(endpoint_id="axo-endpoint-2", status="unreachable", pub_bind="tcp://0.0.0.0:5556"),
            Endpoint(endpoint_id="axo-endpoint-3", status="running", pub_bind=None),
            Endpoint(endpoint_id="axo-endpoint-4", status=None, pub_bind="tcp://0.0.0.0:5576"),
        ])
    use_case = LaunchEndpointNodeUseCase(
        spawner=spawner, image="axo-endpoint:local", network="axo-net", endpoint_repository=repository,
        virtual_environment_id="ve1",
    )

    use_case.execute(api_uri="tcp://axo-vem:6000")

    env = spawner.spawn_calls[0]["env"]
    sub_connect = set(env["AXO_ENDPOINT_SUB_CONNECT"].split(","))
    assert sub_connect == {"tcp://axo-endpoint-0:5556", "tcp://axo-endpoint-4:5576"}


def test_execute_leaves_sub_connect_empty_without_endpoint_repository():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    use_case.execute(api_uri="tcp://axo-vem:6000")

    assert spawner.spawn_calls[0]["env"]["AXO_ENDPOINT_SUB_CONNECT"] == ""


def test_execute_leaves_sub_connect_empty_for_a_ve_with_no_existing_peers():
    spawner = _FakeSpawner()
    repository = _FakeEndpointRepository([])
    use_case = LaunchEndpointNodeUseCase(
        spawner=spawner, image="axo-endpoint:local", network="axo-net", endpoint_repository=repository,
        virtual_environment_id="ve1",
    )

    use_case.execute(api_uri="tcp://axo-vem:6000")

    assert spawner.spawn_calls[0]["env"]["AXO_ENDPOINT_SUB_CONNECT"] == ""


def test_execute_ignores_mesh_identity_keys_in_env_overrides():
    """A caller-supplied override for a mesh-identity key must never win --
    it would silently break the node's own generated identity/addressing."""
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    result = use_case.execute(
        api_uri="tcp://axo-vem:6000",
        env_overrides={"AXO_ENDPOINT_ID": "hijacked", "AXO_ENDPOINT_LOG_LEVEL": "INFO"},
    )

    env = spawner.spawn_calls[0]["env"]
    assert env["AXO_ENDPOINT_ID"] == result.unwrap().endpoint_id
    assert env["AXO_ENDPOINT_ID"] != "hijacked"
    assert env["AXO_ENDPOINT_LOG_LEVEL"] == "INFO"  # non-identity override still applies


def test_execute_includes_docker_socket_mount():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    use_case.execute(api_uri="tcp://axo-vem:6000")

    mounts = spawner.spawn_calls[0]["mounts"]
    assert any(m.source == "/var/run/docker.sock" and m.mode == "ro" for m in mounts)


def test_execute_propagates_spawn_error():
    spawner = _FakeSpawner(result=Err(ContainerSpawnError("docker unreachable")))
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    result = use_case.execute(api_uri="tcp://axo-vem:6000")

    assert result.is_err
    assert isinstance(result.unwrap_err(), ContainerSpawnError)


def test_execute_hands_the_endpoint_storage_address_and_this_ves_token():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(
        spawner=spawner, image="axo-endpoint:local", network="axo-net", virtual_environment_id="ml-prod",
        storage_uri="tcp://axo-storage:7000", storage_token="ml-prod-token",
    )

    use_case.execute(api_uri="tcp://axo-vem:6000")

    env = spawner.spawn_calls[0]["env"]
    assert env["AXO_ENDPOINT_STORAGE_URI"] == "tcp://axo-storage:7000"
    assert env["AXO_ENDPOINT_STORAGE_TOKEN"] == "ml-prod-token"
    assert env["AXO_ENDPOINT_VIRTUAL_ENV_ID"] == "ml-prod"


def test_execute_labels_the_endpoint_container():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(
        spawner=spawner, image="axo-endpoint:local", network="axo-net", virtual_environment_id="ml-prod",
    )

    result = use_case.execute(api_uri="tcp://axo-vem:6000")

    labels = spawner.spawn_calls[0]["labels"]
    assert labels == {"axo.role": "endpoint", "axo.ve": "ml-prod", "axo.endpoint": result.unwrap().endpoint_id}


def test_execute_sizes_the_container_and_the_endpoints_runner_budget():
    spawner = _FakeSpawner()
    use_case = LaunchEndpointNodeUseCase(spawner=spawner, image="axo-endpoint:local", network="axo-net")

    use_case.execute(api_uri="tcp://axo-vem:6000", cpu_cores=2.0, memory_mb=4096)

    call = spawner.spawn_calls[0]
    assert call["nano_cpus"] == 2_000_000_000
    assert call["mem_limit_bytes"] == 4096 * 1024 * 1024
    assert call["env"]["AXO_ENDPOINT_CPU_CORES"] == "2.0"
    assert call["env"]["AXO_ENDPOINT_MEMORY_MB"] == "4096"
    assert call["env"]["AXO_ENDPOINT_RUNNER_BUDGET_ENFORCED"] == "true"
