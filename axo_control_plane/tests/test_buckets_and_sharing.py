import pytest

from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError, UnauthorizedError


def test_bucket_names_are_scoped_by_owner(platform):
    alice, bob = platform.user("alice"), platform.user("bob")
    a = platform.buckets.create(alice, "reports")
    b = platform.buckets.create(bob, "reports")
    assert a["name"] == "alice/reports" and b["name"] == "bob/reports"
    assert a["bucket_id"] != b["bucket_id"] and a["bucket_id"] in platform.storage.buckets
    with pytest.raises(ConflictError):
        platform.buckets.create(alice, "reports")
    with pytest.raises(ForbiddenError):                # not a name alice controls
        platform.buckets.create(alice, "x", owner_name="bob")


def test_storage_access_rule(platform):
    alice, bob = platform.user("alice"), platform.user("bob")
    platform.buckets.create(alice, "reports")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    check = platform.storage_access.check
    assert check(alice.user_id, "alice/reports", "write", ve_id)["allowed"]
    assert not check(bob.user_id, "alice/reports", "read", None)["allowed"]
    invite = platform.sharing.invite_to_bucket(alice, "alice/reports", "bob", R.READER)
    platform.sharing.answer(bob, invite["id"], accept=True)
    assert check(bob.user_id, "alice/reports", "read", None)["allowed"]
    assert not check(bob.user_id, "alice/reports", "write", None)["allowed"]
    # A job in alice's VE: bob also needs Developer+ there.
    assert not check(bob.user_id, "alice/reports", "read", ve_id)["allowed"]
    answer = check(alice.user_id, "alice/reports", "read", ve_id)
    assert answer["bucket_key"].startswith("bkt-") and answer["quota_bytes"] == 10 * 1024 ** 3
    assert not check(alice.user_id, "nobody/nothing", "read", None)["allowed"]
    assert not check(alice.user_id, "reports", "read", None)["allowed"]    # short names don't exist


def test_platform_code_buckets_belong_to_their_ve(platform):
    check = platform.storage_access.check
    assert check("vem:ml-a1b2", "axo/ve-ml-a1b2-functions", "write", "ml-a1b2")["allowed"]
    assert not check("vem:ml-c3d4", "axo/ve-ml-a1b2-functions", "read", "ml-c3d4")["allowed"]
    assert check("svc", "axo/cleanup-logs", "write", None)["allowed"]
    assert not check("u", "axo/cleanup-logs", "read", "ve-a")["allowed"]


def test_aliases_and_transfer_keep_old_names_working(platform):
    alice = platform.user("alice")
    platform.teams.create(alice, "ds")
    bucket = platform.buckets.create(alice, "reports-2026")
    platform.buckets.add_alias(alice, "alice/reports-2026", "ds/reports")
    assert platform.buckets.resolve("ds/reports")["_id"] == bucket["bucket_id"]
    other = platform.buckets.create(alice, "reports-2027")
    platform.buckets.move_alias(alice, "ds/reports", "alice/reports-2027")
    assert platform.buckets.resolve("ds/reports")["_id"] == other["bucket_id"]
    with pytest.raises(ConflictError):                # an alias takes the name like a bucket
        platform.buckets.create(alice, "reports", owner_name="ds")
    # Transfer to the team: new name, old one stays as an alias.
    transfer = platform.sharing.request_transfer(alice, "bucket", "alice/reports-2026", "ds")
    platform.sharing.answer_transfer(alice, transfer["id"], accept=True)   # alice is the Team Owner
    moved = platform.buckets.resolve("ds/reports-2026")
    assert moved["_id"] == bucket["bucket_id"]
    assert platform.buckets.resolve("alice/reports-2026")["_id"] == bucket["bucket_id"]
    assert platform.storage_access.check(alice.user_id, "alice/reports-2026", "write", None)["allowed"]


def test_deleted_bucket_is_hidden_then_purged_after_grace(platform):
    alice = platform.user("alice")
    bucket = platform.buckets.create(alice, "tmp")
    platform.buckets.delete(alice, "alice/tmp")
    with pytest.raises(NotFoundError):
        platform.buckets.resolve("alice/tmp")
    with pytest.raises(ConflictError):                # name stays reserved during the grace period
        platform.buckets.create(alice, "tmp")
    assert platform.buckets.purge_due() == []
    platform.clock.advance(31 * 86400)
    assert platform.buckets.purge_due() == [bucket["bucket_id"]]
    platform.buckets.create(alice, "tmp")             # free again


def test_ve_invitations_follow_the_role_rules(platform):
    owner, admin, dev, guest_inviter = (platform.user(n) for n in ("olga", "adam", "dina", "gil"))
    ve_id = platform.buy(owner)["ve"]["ve_id"]
    inv = platform.sharing.invite_to_ve(owner, ve_id, "adam", R.ADMIN)
    assert platform.authz.ve_role(admin, ve_id) is None          # not before accepting
    with pytest.raises(ForbiddenError):
        platform.sharing.answer(dev, inv["id"], accept=True)      # not for dina
    platform.sharing.answer(admin, inv["id"], accept=True)
    assert platform.authz.ve_role(admin, ve_id) == R.ADMIN
    with pytest.raises(ForbiddenError):                           # only the Owner gives Admin
        platform.sharing.invite_to_ve(admin, ve_id, "dina", R.ADMIN)
    inv = platform.sharing.invite_to_ve(admin, ve_id, "dina", R.DEVELOPER)
    platform.sharing.answer(dev, inv["id"], accept=True)
    assert platform.authz.ve_role(dev, ve_id) == R.DEVELOPER
    access = {a["principal_id"]: a["role"] for a in platform.sharing.ve_access(owner, ve_id)}
    assert access == {owner.user_id: R.OWNER, admin.user_id: R.ADMIN, dev.user_id: R.DEVELOPER}
    with pytest.raises(ForbiddenError):
        platform.sharing.set_ve_role(admin, ve_id, admin.user_id, "USER", R.DEVELOPER)  # admin role: Owner only
    platform.sharing.set_ve_role(dev, ve_id, dev.user_id, "USER", None)                # leaving is always ok
    assert platform.authz.ve_role(dev, ve_id) is None


def test_email_invite_makes_a_viewer_guest(platform):
    owner = platform.user("olga")
    ve_id = platform.buy(owner)["ve"]["ve_id"]
    with pytest.raises(InvalidError):
        platform.sharing.invite_to_ve(owner, ve_id, "new@example.com", R.DEVELOPER)
    inv = platform.sharing.invite_to_ve(owner, ve_id, "new@example.com", R.VIEWER)
    code = inv["signup_code"]
    platform.people.signup("newbie", "new@example.com", "password123", platform_role=R.PLATFORM_GUEST)
    session = platform.people.login("newbie", "password123")
    guest = platform.people.actor_for(platform.people.verify(session["access_token"], session["temporal_secret"]))
    assert guest.is_guest
    platform.sharing.accept_with_code(guest, code)
    assert platform.authz.ve_role(guest, ve_id) == R.VIEWER
    with pytest.raises(ForbiddenError):
        platform.buy(guest)


def test_ve_shared_with_a_team_and_transferred_to_it(platform):
    owner, member = platform.user("olga"), platform.user("mia")
    team = platform.teams.create(owner, "lab")
    inv = platform.sharing.invite_to_team(owner, team["team_id"], "mia")
    platform.sharing.answer(member, inv["id"], accept=True)
    ve = platform.buy(owner)["ve"]
    inv = platform.sharing.invite_to_ve(owner, ve["ve_id"], "lab", R.DEVELOPER)
    platform.sharing.answer(owner, inv["id"], accept=True)       # Team Owner accepts for the team
    assert platform.authz.ve_role(member, ve["ve_id"]) == R.DEVELOPER
    transfer = platform.sharing.request_transfer(owner, "ve", ve["ve_id"], "lab")
    platform.sharing.answer_transfer(owner, transfer["id"], accept=True)
    assert platform.ves.record(ve["ve_id"]).owner.kind == "team"
    assert platform.authz.ve_role(owner, ve["ve_id"]) == R.OWNER  # as Team Owner


def test_sdk_keys(platform):
    alice = platform.user("alice")
    ve_id = platform.buy(alice)["ve"]["ve_id"]
    created = platform.keys.create(alice, "ci", ve_id, R.DEVELOPER, expires_in_days=1)
    keyed = platform.keys.authenticate(created["key"])
    assert keyed.user_id == alice.user_id and platform.authz.ve_role(keyed, ve_id) == R.DEVELOPER
    assert "hash" not in created and created["key"].startswith("axo_")
    with pytest.raises(UnauthorizedError):
        platform.keys.authenticate(created["key"][:-2] + "xx")
    with pytest.raises(ForbiddenError):
        platform.keys.create(keyed, "nested", ve_id, R.VIEWER)
    platform.clock.advance(86401)
    with pytest.raises(UnauthorizedError):                         # expired
        platform.keys.authenticate(created["key"])
    other = platform.keys.create(alice, "ops", None, R.ADMIN)
    platform.keys.revoke(alice, other["key_id"])
    with pytest.raises(UnauthorizedError):
        platform.keys.authenticate(other["key"])


def test_storage_keys_are_accepted_for_the_cp_and_checked_for_people(platform):
    alice, bob = platform.user("alice"), platform.user("bob")
    key = platform.buckets.create(alice, "reports")["bucket_id"]
    check = platform.storage_access.check
    assert check(alice.user_id, key, "write", None)["allowed"]
    assert not check(bob.user_id, key, "read", None)["allowed"]
    assert check("control-plane", key, "read", None)["allowed"]
    assert not check("control-plane", key, "read", "some-ve")["allowed"]
    assert not check(alice.user_id, "bkt-unknown", "read", None)["allowed"]
