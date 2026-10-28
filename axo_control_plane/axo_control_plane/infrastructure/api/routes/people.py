from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from axo_control_plane.application.people import Actor
from axo_control_plane.application.settings import UserSettings
from axo_control_plane.domain import settings as S
from axo_control_plane.domain.errors import ForbiddenError, NotFoundError
from axo_control_plane.domain import roles as R
from axo_control_plane.infrastructure.api.deps import Services, actor, actor_any, services, session_tokens


class SignupBody(BaseModel):
    username: str
    email: str
    password: str = Field(min_length=8)
    first_name: str = ""
    last_name: str = ""
    # From an email invitation: the account is a guest one and the
    # invitation is accepted at once.
    invitation_code: Optional[str] = None


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    new_password: str = Field(min_length=8)


class TeamBody(BaseModel):
    name: str
    description: str = ""


class TeamPatch(BaseModel):
    description: Optional[str] = None
    per_admin_cents: Optional[int] = -1
    team_cents: Optional[int] = -1


class InviteBody(BaseModel):
    who: str                         # username, team name, or email
    role: str


class RoleBody(BaseModel):
    role: str


class TransferBody(BaseModel):
    kind: str                        # ve | bucket | team
    target: str                      # VE id / bucket name / team id
    to: str                          # username or team name


class KeyBody(BaseModel):
    name: str
    ve_id: Optional[str] = None
    max_role: str = R.DEVELOPER
    expires_in_days: Optional[float] = None


class ReadBody(BaseModel):
    notification_ids: Optional[List[str]] = None
    groups: Optional[List[str]] = None       # every unread one of these groups (the page's filter)


def build_router() -> APIRouter:
    router = APIRouter()

    # ── accounts and sessions ────────────────────────────────────────────────
    @router.post("/auth/signup", status_code=201, tags=["auth"])
    def signup(body: SignupBody, svc: Services = Depends(services)):
        invitation = svc.sharing.invitation_for_code(body.invitation_code) if body.invitation_code else None
        role = R.PLATFORM_GUEST if invitation is not None else R.PLATFORM_USER
        user = svc.people.signup(body.username, body.email, body.password, body.first_name, body.last_name,
                                 platform_role=role)
        if invitation is not None:
            session = svc.people.login(body.username, body.password)
            guest = svc.people.actor_for(svc.people.verify(session["access_token"], session["temporal_secret"]))
            svc.sharing.accept_with_code(guest, body.invitation_code)
            return {**session, "user": user}
        return {"user": user}

    @router.post("/auth/login", tags=["auth"])
    def login(body: LoginBody, svc: Services = Depends(services)):
        return svc.people.login(body.username, body.password)

    @router.post("/auth/refresh", tags=["auth"])
    def refresh(tokens=Depends(session_tokens), svc: Services = Depends(services)):
        return svc.people.refresh(*tokens)

    @router.post("/auth/logout", status_code=204, tags=["auth"])
    def logout(tokens=Depends(session_tokens), svc: Services = Depends(services)):
        svc.people.logout(*tokens)

    @router.post("/auth/password", status_code=204, tags=["auth"])
    def change_password(body: PasswordBody, tokens=Depends(session_tokens), who: Actor = Depends(actor_any),
                        svc: Services = Depends(services)):
        svc.people.change_password(who, tokens[0], tokens[1], body.new_password)

    @router.get("/me", tags=["auth"])
    def me(who: Actor = Depends(actor_any), svc: Services = Depends(services)):
        return {**svc.people.get(who.user_id), "platform_role": who.platform_role,
                "teams": [{**t, "role": next((m["role"] for m in t.get("members") or []
                                              if m.get("user_id") == who.user_id), None)}
                          for t in svc.teams.of_user(who.user_id)],      # "role" = the person's own
                "unread_notifications": svc.notifications.unread_count(who.user_id),
                "via_key": who.key_id}

    # ── settings (how the platform shows things to this person) ─────────────
    @router.get("/me/settings", tags=["settings"])
    def my_settings(who: Actor = Depends(actor_any), svc: Services = Depends(services)):
        """What applies (``settings``), what the person chose (``own``), the
        platform defaults, and every setting with its choices."""
        return _settings(svc).describe(_person(who))

    @router.patch("/me/settings", tags=["settings"])
    def change_settings(body: Dict[str, Any], who: Actor = Depends(actor_any), svc: Services = Depends(services)):
        """Only the fields sent change; ``null`` = follow the platform default again."""
        return _settings(svc).update(_person(who), body)

    @router.get("/settings/time-zones", tags=["settings"])
    def time_zones():
        return {"time_zones": S.time_zones()}

    # ── notifications ────────────────────────────────────────────────────────
    @router.get("/notifications", tags=["notifications"])
    def notifications(unread_only: bool = False, limit: int = 50, before: Optional[float] = None,
                      before_id: Optional[str] = None, groups: Optional[str] = None,
                      who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Newest first; ``before`` + ``before_id`` = the last one shown
        (next page); ``groups`` = people,ves,jobs,billing."""
        picked = [g for g in (groups or "").split(",") if g] or None
        return {"unread": svc.notifications.unread_count(who.user_id),
                "items": svc.notifications.list(who.user_id, unread_only, max(1, min(limit, 200)),
                                                before=before, before_id=before_id, groups=picked)}

    @router.post("/notifications/read", tags=["notifications"])
    def mark_read(body: ReadBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return {"marked": svc.notifications.mark_read(who.user_id, body.notification_ids, body.groups)}

    # ── teams ────────────────────────────────────────────────────────────────
    @router.get("/teams", tags=["teams"])
    def my_teams(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.teams.summaries(who.user_id)

    @router.get("/names/check", tags=["teams"])
    def name_check(name: str, _who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Whether a team name is free (names are shared with usernames)."""
        return svc.teams.name_free(name)

    @router.post("/teams", status_code=201, tags=["teams"])
    def create_team(body: TeamBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.teams.create(who, body.name, body.description)

    @router.get("/teams/{team_id}", tags=["teams"])
    def get_team(team_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        role = svc.teams.require(who, team_id, R.MEMBER)
        team = svc.teams.get(team_id)
        team["members"] = [{**m, "username": _username(svc, m["user_id"])} for m in team["members"]]
        out = {**team, "my_role": role, "invitations": svc.sharing.outgoing("team", team_id)
               if R.at_least(role, R.ADMIN, R.TEAM_ROLES) else []}
        if role != R.OWNER:
            out.pop("limits", None)
        return out

    @router.get("/teams/{team_id}/resources", tags=["teams"])
    def team_resources(team_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The VEs and buckets the team owns, and those shared with it."""
        return svc.teams.resources(who, team_id)

    @router.get("/teams/{team_id}/history", tags=["teams"])
    def team_history(team_id: str, limit: int = 100, who: Actor = Depends(actor), svc: Services = Depends(services)):
        rows = svc.teams.history(who, team_id, min(max(limit, 1), 500))
        for row in rows:
            for key in ("by", "user_id", "from", "to"):
                if isinstance(row.get(key), str):
                    row[f"{key}_name"] = _username(svc, row[key])
        return rows

    @router.patch("/teams/{team_id}", tags=["teams"])
    def update_team(team_id: str, body: TeamPatch, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.teams.update(who, team_id, body.description, body.per_admin_cents, body.team_cents)

    @router.delete("/teams/{team_id}", status_code=204, tags=["teams"])
    def delete_team(team_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.teams.delete(who, team_id)

    @router.post("/teams/{team_id}/invitations", status_code=201, tags=["teams"])
    def invite_to_team(team_id: str, body: InviteBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.invite_to_team(who, team_id, body.who, body.role)

    @router.put("/teams/{team_id}/members/{user_id}", status_code=204, tags=["teams"])
    def change_member_role(team_id: str, user_id: str, body: RoleBody, who: Actor = Depends(actor),
                           svc: Services = Depends(services)):
        svc.teams.change_role(who, team_id, user_id, body.role)

    @router.delete("/teams/{team_id}/members/{user_id}", status_code=204, tags=["teams"])
    def remove_member(team_id: str, user_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.teams.remove_member(who, team_id, user_id)

    @router.get("/teams/{team_id}/purchases", tags=["teams"])
    def team_purchases(team_id: str, status: Optional[str] = None, who: Actor = Depends(actor),
                       svc: Services = Depends(services)):
        return svc.ves.purchases(who, team_id, status)

    @router.get("/teams/{team_id}/spending", tags=["teams"])
    def team_spending(team_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.spending(who, team_id)

    @router.post("/purchases/{purchase_id}/approve", tags=["teams"])
    def approve(purchase_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.answer_purchase(who, purchase_id, approve=True)

    @router.post("/purchases/{purchase_id}/reject", tags=["teams"])
    def reject(purchase_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.ves.answer_purchase(who, purchase_id, approve=False)

    # ── invitations and transfers ────────────────────────────────────────────
    @router.get("/invitations", tags=["sharing"])
    def incoming(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.incoming(who)

    @router.get("/invitations/sent", tags=["sharing"])
    def sent(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.sent(who)

    @router.get("/invitations/answered", tags=["sharing"])
    def answered(days: int = 30, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.answered(who, min(max(days, 1), 90))

    @router.post("/invitations/{invitation_id}/accept", tags=["sharing"])
    def accept(invitation_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.answer(who, invitation_id, accept=True)

    @router.post("/invitations/{invitation_id}/reject", tags=["sharing"])
    def decline(invitation_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.answer(who, invitation_id, accept=False)

    @router.delete("/invitations/{invitation_id}", status_code=204, tags=["sharing"])
    def cancel(invitation_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.sharing.cancel(who, invitation_id)

    @router.get("/transfers", tags=["sharing"])
    def transfers(answered_days: int = 0, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """Waiting ones; ``answered_days`` adds those answered since."""
        return svc.sharing.pending_transfers(who, min(max(answered_days, 0), 90))

    @router.delete("/transfers/{transfer_id}", status_code=204, tags=["sharing"])
    def cancel_transfer(transfer_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.sharing.cancel_transfer(who, transfer_id)

    @router.post("/transfers", status_code=201, tags=["sharing"])
    def request_transfer(body: TransferBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.request_transfer(who, body.kind, body.target, body.to)

    @router.post("/transfers/{transfer_id}/accept", tags=["sharing"])
    def accept_transfer(transfer_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.answer_transfer(who, transfer_id, accept=True)

    @router.post("/transfers/{transfer_id}/reject", tags=["sharing"])
    def reject_transfer(transfer_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.sharing.answer_transfer(who, transfer_id, accept=False)

    # ── SDK keys ─────────────────────────────────────────────────────────────
    @router.get("/sdk-keys", tags=["sdk keys"])
    def list_keys(who: Actor = Depends(actor), svc: Services = Depends(services)):
        return svc.keys.list(who)

    @router.post("/sdk-keys", status_code=201, tags=["sdk keys"])
    def create_key(body: KeyBody, who: Actor = Depends(actor), svc: Services = Depends(services)):
        """The key is in the answer once; it can't be shown again."""
        return svc.keys.create(who, body.name, body.ve_id, body.max_role, body.expires_in_days)

    @router.delete("/sdk-keys/{key_id}", status_code=204, tags=["sdk keys"])
    def revoke_key(key_id: str, who: Actor = Depends(actor), svc: Services = Depends(services)):
        svc.keys.revoke(who, key_id)

    return router


def _settings(svc: Services) -> UserSettings:
    return svc.settings or UserSettings(svc.db)


def _person(who: Actor) -> str:
    """Settings are a person's own: an SDK key (a program) has none."""
    if who.key_id is not None:
        raise ForbiddenError("settings belong to a person: sign in, an API key can't read or change them")
    return who.user_id


def _username(svc: Services, user_id: str) -> Optional[str]:
    try:
        return svc.people.get(user_id)["username"]
    except NotFoundError:
        return None
