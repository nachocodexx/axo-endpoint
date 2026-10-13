import pytest

from axo_control_plane.domain import roles as R
from axo_control_plane.domain import virtual_environment as V
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from axo_control_plane.domain.plans import Resources


def test_buying_reserves_quota_plus_base_and_makes_the_buyer_owner(platform):
    alice = platform.user("alice")
    result = platform.buy(alice, name="ml-prod", cpu=4.0, mem=8192, target_endpoints=2)
    ve = result["ve"]
    assert result["status"] == "created" and ve["lifecycle"] == V.CREATING
    assert ve["ve_id"].startswith("ml-prod-")
    # basic: base small (1 CPU / 2 GB) + quota 4 / 8 GB
    assert platform.capacity.reserved() == Resources(5.0, 10240)
    assert platform.authz.ve_role(alice, ve["ve_id"]) == R.OWNER
    assert [v["ve_id"] for v in platform.ves.list_for(alice)] == [ve["ve_id"]]
    bob = platform.user("bob")
    assert platform.ves.list_for(bob) == []
    with pytest.raises(NotFoundError):          # no hint that it exists
        platform.ves.get(bob, ve["ve_id"])


def test_capacity_is_never_overbooked(platform):
    alice = platform.user("alice")
    with pytest.raises(ConflictError) as full:
        platform.buy(alice, plan_id="pro", cpu=40.0, mem=8192, target_endpoints=1)
    assert full.value.code == "CAPACITY_FULL"
    assert platform.capacity.reserved() == Resources(0.0, 0)


def test_one_free_ve_per_person_and_free_is_personal(platform):
    alice = platform.user("alice")
    free = platform.buy(alice, name="play", plan_id="free", cpu=99, mem=99)["ve"]
    assert free["quota"] == {"cpu_cores": 1.0, "memory_mb": 1024}     # fixed by the plan
    with pytest.raises(ConflictError):
        platform.buy(alice, name="play2", plan_id="free")
    team = platform.teams.create(alice, "lab")
    with pytest.raises(InvalidError):
        platform.buy(alice, name="t", plan_id="free", team_id=team["team_id"])


def test_guests_and_members_cannot_buy(platform):
    alice, member = platform.user("alice"), platform.user("mia")
    team = platform.teams.create(alice, "lab")
    platform.teams.add_member(team["team_id"], member.user_id, R.MEMBER, by=alice.user_id)
    with pytest.raises(ForbiddenError):
        platform.buy(member, team_id=team["team_id"])
    guest = member.__class__(**{**member.__dict__, "platform_role": R.PLATFORM_GUEST})
    with pytest.raises(ForbiddenError):
        platform.buy(guest)


def test_team_admin_over_the_limit_creates_a_pending_purchase(platform):
    owner, admin = platform.user("olga"), platform.user("adam")
    team = platform.teams.create(owner, "lab")
    platform.teams.add_member(team["team_id"], admin.user_id, R.ADMIN, by=owner.user_id)
    platform.teams.update(owner, team["team_id"], per_admin_cents=600)
    first = platform.buy(admin, name="a", team_id=team["team_id"])          # $5 fits
    assert first["status"] == "created"
    assert platform.notifications.unread_count(owner.user_id) == 1         # every Team Admin purchase
    second = platform.buy(admin, name="b", team_id=team["team_id"])         # $10 > $6
    assert second["status"] == "pending"
    assert platform.capacity.reserved() == Resources(5.0, 10240)            # nothing reserved yet
    approved = platform.ves.answer_purchase(owner, second["purchase_id"], approve=True)
    assert approved["status"] == "created" and approved["ve"]["owner"]["name"] == "lab"
    assert platform.ves.spending(owner, team["team_id"])["team_cents"] == 1000


def test_plan_change_is_owner_only_and_moves_capacity(platform):
    alice, bob = platform.user("alice"), platform.user("bob")
    ve = platform.buy(alice, cpu=2.0, mem=4096, target_endpoints=2)["ve"]
    with pytest.raises(NotFoundError):
        platform.ves.change_plan(bob, ve["ve_id"], plan_id="standard")
    platform.ves.change_plan(alice, ve["ve_id"], plan_id="standard", quota_cpu_cores=6, quota_memory_mb=12288)
    # standard base = medium (2 / 4 GB) + quota 6 / 12 GB
    assert platform.capacity.reserved() == Resources(8.0, 16384)
    assert platform.db["virtual_environments"].find_one({"_id": ve["ve_id"]})["limits_dirty"] is True


def test_delete_needs_the_owner_and_the_name(platform):
    alice = platform.user("alice")
    ve = platform.buy(alice)["ve"]
    with pytest.raises(InvalidError):
        platform.ves.delete(alice, ve["ve_id"], confirm_name="nope")
    assert platform.ves.delete(alice, ve["ve_id"], confirm_name=ve["name"])["desired"] == V.DESIRED_DELETED


# ── provisioner ──────────────────────────────────────────────────────────────
def _heartbeat(platform, ve_id, **fields):
    hb = {"lifecycle": "Running", "health": "Healthy", "target_endpoints": 1, "min_endpoints": 1,
          "running": 1, "outbox_pending": 0, **fields}
    platform.db["virtual_environments"].update_one({"_id": ve_id}, {"$set": {"heartbeat": hb,
                                                                              "heartbeat_at": platform.clock()}})


def test_provisioner_brings_a_new_ve_up_step_by_step(platform):
    alice = platform.user("alice")
    ve = platform.buy(alice, target_endpoints=2)["ve"]
    ve_id = ve["ve_id"]
    platform.provisioner.tick()
    d = platform.driver
    assert f"ve-{ve_id}-net" in d.networks and "axo-storage" in d.networks[f"ve-{ve_id}-net"]
    for role in ("kurrent", "mongo", "vem"):
        assert d.state(f"ve-{ve_id}-{role}") == "running"
    vem = d.containers[f"ve-{ve_id}-vem"]["spec"]
    assert vem.env["AXO_VEM_SERVICE_TOKEN"] == f"vem-token-{ve_id}"
    assert vem.env["AXO_VEM_VE_STORAGE_TOKEN"] == f"st-{ve_id}"
    assert '"target_endpoints":2' in vem.env["AXO_VEM_INITIAL_SETTINGS"]
    assert set(vem.networks) == {f"ve-{ve_id}-net", "axo-control-net"}
    assert vem.labels["axo.ve"] == ve_id and vem.labels["axo.role"] == "vem"
    # The VEM reports Running -> the VE is Running and its owner hears about it.
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    record = platform.ves.record(ve_id)
    assert record.lifecycle == V.RUNNING
    assert any("is ready" in n["text"] for n in platform.notifications.list(alice.user_id))
    # Repeating does nothing new.
    before = dict(d.containers)
    platform.provisioner.tick()
    assert d.containers.keys() == before.keys()


def test_stop_then_start_keeps_containers(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    platform.provisioner.tick()
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    platform.ves.stop(alice, ve_id)
    platform.provisioner.tick()                       # asks the VEM to stop (drain)
    assert ("stop", ve_id) in platform.vem.calls
    platform.provisioner.tick()                       # VEM stopped -> containers stopped, kept
    assert platform.ves.record(ve_id).lifecycle == V.STOPPED
    assert platform.driver.state(f"ve-{ve_id}-vem") == "exited"
    platform.ves.start(alice, ve_id)
    platform.provisioner.tick()                       # containers started again
    assert platform.driver.state(f"ve-{ve_id}-vem") == "running"
    _heartbeat(platform, ve_id, lifecycle="Stopped", health=None)
    platform.clock.advance(1)
    platform.provisioner.tick()                       # VEM still Stopped -> asked to start
    assert ("start", ve_id) in platform.vem.calls


def test_delete_waits_for_unsent_messages_then_removes_everything(platform):
    alice = platform.user("alice")
    ve = platform.buy(alice)["ve"]
    ve_id = ve["ve_id"]
    platform.provisioner.tick()
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    platform.driver.add_labelled(f"ep-{ve_id}-1", {"axo.ve": ve_id, "axo.role": "endpoint"})
    platform.ves.delete(alice, ve_id, confirm_name=ve["name"])
    _heartbeat(platform, ve_id, outbox_pending=3)
    platform.provisioner.tick()                       # prepare-delete requested
    platform.provisioner.tick()                       # still 3 unsent -> waits
    assert platform.ves.record(ve_id).lifecycle == V.DELETING
    _heartbeat(platform, ve_id, outbox_pending=0)
    platform.provisioner.tick()
    record = platform.ves.record(ve_id)
    assert record.lifecycle == V.DELETED
    assert not [c for c in platform.driver.containers if ve_id in c]
    assert f"ve-{ve_id}-net" not in platform.driver.networks
    assert platform.capacity.reserved() == Resources(0.0, 0)
    assert f"ve-{ve_id}-mongo-data" in platform.driver.volumes        # kept for the grace period
    assert record.purge_after == pytest.approx(platform.clock() + 30 * 86400)


def test_vem_without_heartbeats_is_unavailable_then_recreated(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    platform.provisioner.tick()
    _heartbeat(platform, ve_id)
    platform.provisioner.tick()
    platform.clock.advance(100)
    platform.provisioner.tick()
    record = platform.ves.record(ve_id)
    assert record.health == "Unavailable" and record.vem_down_since is not None
    platform.clock.advance(400)
    platform.provisioner.tick()
    assert f"ve-{ve_id}-vem" in platform.driver.removed
    assert platform.driver.state(f"ve-{ve_id}-vem") == "running"


def test_only_the_lease_holder_works_on_a_ve(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    platform.leases.acquire(f"ve:{ve_id}", "cp-2", 30)
    assert platform.provisioner.tick() == 0
    assert platform.driver.containers == {}
