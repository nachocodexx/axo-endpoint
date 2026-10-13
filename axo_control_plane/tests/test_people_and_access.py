import pytest

from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import ConflictError, ForbiddenError, UnauthorizedError, UnavailableError
from axo_control_plane.infrastructure.mongo_leases import MongoLeases


def test_bootstrap_admin_must_change_password_and_is_not_recreated(platform):
    admin = platform.people.find("admin")
    assert admin["platform_role"] == R.PLATFORM_ADMIN and admin["password_change_required"] is True
    assert platform.people.bootstrap_admin("admin", "admin-password", "admin@axo.local") == "exists"
    session = platform.people.login("admin", "admin-password")
    actor = platform.people.actor_for(platform.people.verify(session["access_token"], session["temporal_secret"]))
    assert actor.is_platform_admin and actor.password_change_required
    platform.people.change_password(actor, session["access_token"], session["temporal_secret"], "a-new-secret")
    assert platform.people.find("admin")["password_change_required"] is False


def test_signup_takes_a_shared_name_and_gives_the_user_role(platform):
    alice = platform.user("alice")
    assert alice.platform_role == R.PLATFORM_USER
    with pytest.raises(ConflictError):
        platform.people.signup("alice", "other@example.com", "password123")
    platform.teams.create(alice, "data")
    with pytest.raises(ConflictError):          # users and teams share one list of names
        platform.people.signup("data", "d@example.com", "password123")


def test_logged_out_session_stops_working(platform):
    platform.user("bob")
    session = platform.people.login("bob", "password123")
    token, secret = session["access_token"], session["temporal_secret"]
    assert platform.people.verify(token, secret).username == "bob"
    platform.people.logout(token, secret)
    with pytest.raises(UnauthorizedError):
        platform.people.verify(token, secret)


def test_cached_roles_survive_xolo_outage_but_unknown_ones_are_refused(platform):
    alice = platform.user("alice")
    ve = platform.buy(alice)["ve"]
    assert platform.authz.ve_role(alice, ve["ve_id"]) == R.OWNER
    platform.identity.down = True
    platform.clock.advance(10_000)                # cache expired, Xolo down -> old answer
    assert platform.access.ve_role(alice.user_id, ve["ve_id"]) == R.OWNER
    assert platform.access._grants.stale_hits >= 1
    with pytest.raises(UnavailableError):         # nothing cached -> try again later, never allow
        platform.access.ve_role("someone-else", ve["ve_id"])


def test_team_owned_ve_gives_each_team_role_its_own_rights(platform):
    owner, admin, member, outsider = (platform.user(n) for n in ("olga", "adam", "mia", "otto"))
    team = platform.teams.create(owner, "ds")
    platform.teams.add_member(team["team_id"], admin.user_id, R.ADMIN, by=owner.user_id)
    platform.teams.add_member(team["team_id"], member.user_id, R.MEMBER, by=owner.user_id)
    ve = platform.buy(owner, name="shared", team_id=team["team_id"])["ve"]
    roles = {a.username: platform.authz.ve_role(a, ve["ve_id"]) for a in (owner, admin, member, outsider)}
    assert roles == {"olga": R.OWNER, "adam": R.ADMIN, "mia": R.VIEWER, "otto": None}
    # Leaving the team removes the access it gave.
    platform.teams.remove_member(member, team["team_id"], member.user_id)
    assert platform.authz.ve_role(member, ve["ve_id"]) is None


def test_team_admin_rules(platform):
    owner, admin, member = (platform.user(n) for n in ("olga", "adam", "mia"))
    team = platform.teams.create(owner, "ds")
    platform.teams.add_member(team["team_id"], admin.user_id, R.ADMIN, by=owner.user_id)
    platform.teams.add_member(team["team_id"], member.user_id, R.MEMBER, by=owner.user_id)
    with pytest.raises(ForbiddenError):
        platform.teams.remove_member(member, team["team_id"], admin.user_id)
    with pytest.raises(ForbiddenError):
        platform.teams.remove_member(admin, team["team_id"], owner.user_id)
    with pytest.raises(ForbiddenError):           # spending limits: Owner only
        platform.teams.update(admin, team["team_id"], per_admin_cents=1000)
    platform.teams.change_role(admin, team["team_id"], member.user_id, R.ADMIN)
    assert platform.teams.role_of(member.user_id, team["team_id"]) == R.ADMIN


def test_sdk_key_limits_never_exceed_the_persons_role(platform):
    alice = platform.user("alice")
    ve = platform.buy(alice)["ve"]
    keyed = alice.__class__(**{**alice.__dict__, "key_id": "k1", "key_ve_id": ve["ve_id"],
                               "key_max_role": R.DEVELOPER})
    assert platform.authz.ve_role(keyed, ve["ve_id"]) == R.DEVELOPER
    other = alice.__class__(**{**keyed.__dict__, "key_ve_id": "another-ve"})
    assert platform.authz.ve_role(other, ve["ve_id"]) is None


def test_mongo_lease_is_held_by_one_copy_until_it_expires(platform):
    leases = MongoLeases(platform.db, now_fn=platform.clock)
    assert leases.acquire("ve:x", "cp-1", 30)
    assert not leases.acquire("ve:x", "cp-2", 30)
    assert leases.acquire("ve:x", "cp-1", 30)      # renew
    platform.clock.advance(31)
    assert leases.acquire("ve:x", "cp-2", 30)      # cp-1 died: taken over
