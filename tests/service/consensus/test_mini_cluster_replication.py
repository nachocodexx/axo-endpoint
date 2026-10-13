import time

import cloudpickle
import zmq

from axo_shared.protocol import Command
from axo_shared import wire
from tests.service.consensus._cluster import spin_up_node, wait_until


def _send_command(rpc_uri, command, timeout_ms=3000):
    dealer = zmq.Context.instance().socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, timeout_ms)
    dealer.connect(rpc_uri)
    try:
        dealer.send_multipart(wire.encode_command(command))
        frames = dealer.recv_multipart()
        return wire.decode_command_result(frames).unwrap()
    finally:
        dealer.close()


def _register_command(name, code=b"code"):
    return Command(
        operation=wire.FUNCTION_REGISTER,
        content_type="application/octet-stream",
        envelope={"function_id": name, "name": name},
        payload=code,
    )


def _register_function_command(name, fn):
    return Command(
        operation=wire.FUNCTION_REGISTER,
        content_type="application/octet-stream",
        envelope={"function_id": name, "name": name},
        payload=cloudpickle.dumps(fn),
    )


def _leader_and_follower(nodes):
    leader = next(n for n in nodes if n.elector.is_leader(n.config.AXO_ENDPOINT_ID))
    follower = next(n for n in nodes if n is not leader)
    return leader, follower


def _two_node_cluster(tmp_path, monkeypatch, **env_overrides):
    node1 = spin_up_node(tmp_path, monkeypatch, "node-a", **env_overrides)
    node2 = spin_up_node(
        tmp_path, monkeypatch, "node-b", sub_connect=[node1.config.AXO_ENDPOINT_PUB_BIND], **env_overrides
    )
    nodes = [node1, node2]
    assert wait_until(
        lambda: all(n.elector.current_view().leader_ids == frozenset({"node-b"}) for n in nodes),
        timeout=10.0,
    )
    return nodes


def test_function_registered_on_leader_appears_on_follower_after_idle_threshold(tmp_path, clean_env, monkeypatch):
    nodes = _two_node_cluster(tmp_path, monkeypatch)
    try:
        leader, follower = _leader_and_follower(nodes)

        result = _send_command(leader.config.AXO_ENDPOINT_ROUTER_BIND, _register_command("foo"))
        assert result.ok is True

        assert wait_until(
            lambda: follower.functions_store.get_by_id("foo").unwrap() is not None, timeout=5.0
        )
        record = follower.functions_store.get_by_id("foo").unwrap()
        assert record.code == b"code"
    finally:
        for n in nodes:
            n.stop()


def test_register_against_follower_succeeds_transparently(tmp_path, clean_env, monkeypatch):
    nodes = _two_node_cluster(tmp_path, monkeypatch)
    try:
        leader, follower = _leader_and_follower(nodes)

        result = _send_command(follower.config.AXO_ENDPOINT_ROUTER_BIND, _register_command("bar"))

        # The client never sees a "not leader" error -- it gets back exactly
        # what registering against the leader directly would have returned.
        assert result.ok is True
        assert result.metadata == {"function_id": "bar", "version": 1}
        assert leader.functions_store.get_by_id("bar").unwrap() is not None
    finally:
        for n in nodes:
            n.stop()


def test_max_dirty_count_flush_fires_before_idle_timeout_under_burst_load(tmp_path, clean_env, monkeypatch):
    nodes = _two_node_cluster(
        tmp_path,
        monkeypatch,
        AXO_ENDPOINT_CONSENSUS_REPLICATION_MAX_DIRTY="3",
        AXO_ENDPOINT_CONSENSUS_REPLICATION_IDLE_SECONDS="30.0",
    )
    try:
        leader, follower = _leader_and_follower(nodes)

        for i in range(3):
            result = _send_command(leader.config.AXO_ENDPOINT_ROUTER_BIND, _register_command(f"burst{i}"))
            assert result.ok is True

        # Bounded well under the 30s idle timeout -- only explainable by
        # MAX_DIRTY firing before the idle window would have.
        assert wait_until(
            lambda: all(
                follower.functions_store.get_by_id(f"burst{i}").unwrap() is not None for i in range(3)
            ),
            timeout=5.0,
        )
    finally:
        for n in nodes:
            n.stop()


def test_late_joining_node_catches_up_via_state_sync_pull(tmp_path, clean_env, monkeypatch):
    nodes = _two_node_cluster(tmp_path, monkeypatch)
    try:
        leader, follower = _leader_and_follower(nodes)
        result = _send_command(leader.config.AXO_ENDPOINT_ROUTER_BIND, _register_command("early"))
        assert result.ok is True
        assert wait_until(lambda: follower.functions_store.get_by_id("early").unwrap() is not None, timeout=5.0)

        # "node-0" sorts below both existing ids, so it never disturbs the
        # existing leader belief -- this isolates the assertion to the
        # one-shot STATE_SYNC_PULL catch-up path. It connects only to the
        # follower (never the leader), so the only way it can learn about
        # "early" is by pulling the follower's already-replicated state.
        late_node = spin_up_node(
            tmp_path, monkeypatch, "node-0", sub_connect=[follower.config.AXO_ENDPOINT_PUB_BIND]
        )
        nodes.append(late_node)

        assert wait_until(
            lambda: late_node.functions_store.get_by_id("early").unwrap() is not None, timeout=5.0
        )
    finally:
        for n in nodes:
            n.stop()
