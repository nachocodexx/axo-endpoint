from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest
from option import Err, Ok
from xolo.client import errors as XE
from xolo.client import models as XM

from axo_control_plane.domain.errors import (
    ConflictError,
    InvalidError,
    NotFoundError,
    UnauthorizedError,
    UnavailableError,
)
from axo_control_plane.infrastructure.xolo_identity import XoloIdentity


def _page(items: List[Any], page: int, total_pages: int) -> Dict[str, Any]:
    return {"items": items, "total_count": len(items), "page": page, "page_size": 200,
            "total_pages": total_pages}


class FakeClient:
    """Answers like XoloClient: every call returns Ok(dto) / Err(XoloError).
    ``answers[name]`` overrides one call; every call is recorded."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.calls: List[Tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            answer = self.answers.get(name, Ok(None))
            return answer(*args, **kwargs) if callable(answer) else answer
        return call

    def called(self, name: str) -> List[Tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]


def _identity(**answers: Any) -> Tuple[XoloIdentity, FakeClient]:
    client = FakeClient(**answers)
    return XoloIdentity("http://xolo/api/v4", "acc", "key", scope="AXO", client=client), client


def _auth(user_id: str = "u1") -> XM.AuthenticatedDTO:
    return XM.AuthenticatedDTO(username="ana", first_name="Ana", last_name="L", email="ana@x.io",
                               profile_photo="", access_token="tok", metadata={}, temporal_secret="sec",
                               user_id=user_id)


def test_needs_an_api_key():
    with pytest.raises(ValueError):
        XoloIdentity("http://xolo/api/v4", "acc", "", client=FakeClient())


def test_signup_uses_the_scope_and_returns_the_xolo_key():
    identity, client = _identity(signup=Ok(XM.CreatedUserResponseDTO(key="u1")))
    user = identity.signup("ana", "ana@x.io", "secret-123")
    assert user.user_id == "u1" and user.username == "ana"
    (_, kwargs), = client.called("signup")
    assert kwargs["scope"] == "AXO" and kwargs["first_name"] == "ana" and kwargs["last_name"] == "-"


def test_login_returns_a_session():
    identity, _ = _identity(auth=Ok(_auth()))
    session = identity.login("ana", "secret-123")
    assert (session.user.user_id, session.access_token, session.temporal_secret) == ("u1", "tok", "sec")


def test_login_without_user_id_asks_the_session_who_it_is():
    user = XM.UserDTO(key="u9", username="ana", first_name="", last_name="", email="", profile_photo="")
    identity, client = _identity(auth=Ok(_auth(user_id="")), introspect_session=Ok(user))
    assert identity.login("ana", "pw").user.user_id == "u9"
    assert client.called("introspect_session") == [(("tok", "sec"), {})]


def test_wrong_password_is_unauthorized():
    identity, _ = _identity(auth=Err(XE.UnauthorizedError("bad credentials")))
    with pytest.raises(UnauthorizedError):
        identity.login("ana", "nope")


def test_refused_api_key_is_unavailable_not_unauthorized():
    identity, _ = _identity(service_claim_resource=Err(XE.AccessDeniedError("missing service scope")))
    with pytest.raises(UnavailableError) as info:
        identity.claim("ve:1", "u1")
    assert info.value.code == "XOLO_KEY_REFUSED"


def test_xolo_down_is_unavailable():
    identity, _ = _identity(introspect_session=Err(XE.InternalError("Connection refused")))
    with pytest.raises(UnavailableError):
        identity.introspect("tok", "sec")


@pytest.mark.parametrize("err, expected", [
    (XE.NotFoundError("no"), NotFoundError),
    (XE.AlreadyExistsError("dup"), ConflictError),
    (XE.ConflictError("dup"), ConflictError),
    (XE.ValidationError("bad"), InvalidError),
])
def test_error_mapping(err, expected):
    identity, _ = _identity(service_create_group=Err(err))
    with pytest.raises(expected):
        identity.create_group("team:1", "u1")


def test_change_password_uses_the_users_session():
    identity, client = _identity()
    identity.change_password("ana", "tok", "sec", "new-secret-1")
    assert client.called("change_password") == [(("new-secret-1", "tok", "sec"), {})]


def test_grants_of_an_unknown_resource_are_empty():
    identity, _ = _identity(service_list_resource_grants=Err(XE.NotFoundError("ResourcePolicy not found")))
    assert identity.grants("ve:none") == []


def test_grants():
    dto = XM.ResourceGrantsDTO(resource_id="ve:1", grants=[
        XM.ResourceGrantDTO(grant_id="g", principal_id="u1", principal_type="USER", permissions={"read"},
                            is_owner=False)])
    identity, _ = _identity(service_list_resource_grants=Ok(dto))
    (grant,) = identity.grants("ve:1")
    assert (grant.principal_id, grant.permissions, grant.is_owner) == ("u1", frozenset({"read"}), False)


def test_removals_of_missing_things_are_fine():
    missing = Err(XE.NotFoundError("gone"))
    identity, _ = _identity(service_revoke_permission=missing, service_delete_resource_policy=missing,
                            service_delete_group=missing)
    identity.revoke("ve:1", "u1", "USER", ["read"])
    identity.delete_resource("ve:1")
    identity.delete_group("g1")


def test_deleting_a_missing_resource_answered_as_a_database_error_is_fine():
    identity, _ = _identity(service_delete_resource_policy=Err(
        XE.DatabaseError("Resource policy 've:1' not found")))
    identity.delete_resource("ve:1")


def test_other_database_errors_on_delete_are_unavailable():
    identity, _ = _identity(service_delete_resource_policy=Err(XE.DatabaseError("write failed")))
    with pytest.raises(UnavailableError):
        identity.delete_resource("ve:1")


def test_resources_of_reads_every_page():
    def resources(user_id, owned_page, owned_page_size, shared_page, shared_page_size):
        owned = [{"resource_id": f"ve:{owned_page}", "permissions": set(), "is_owner": True}]
        shared = [{"resource_id": "bucket:s", "permissions": {"read"}, "is_owner": False}] if shared_page == 1 else []
        return Ok(XM.ServiceUserResourcesDTO.model_validate({
            "user_id": user_id, "groups": [],
            "owned_resources": _page(owned, owned_page, 2), "shared_resources": _page(shared, shared_page, 1)}))
    identity, _ = _identity(service_get_user_resources=resources)
    assert identity.resources_of("u1") == ["ve:1", "bucket:s", "ve:2"]


def test_groups():
    dto = XM.ServiceUserResourcesDTO.model_validate({
        "user_id": "u1", "groups": [{"id": "g1", "name": "team:1", "my_role": "member"}],
        "owned_resources": _page([], 1, 0), "shared_resources": _page([], 1, 0)})
    identity, client = _identity(service_create_group=Ok(XM.CreatedGroupResponseDTO(ok=True, group_id="g1")),
                                 service_get_user_resources=Ok(dto))
    assert identity.create_group("team:1", "u1") == "g1"
    identity.add_members("g1", ("u2", "u3"))
    identity.remove_members("g1", ["u3"])
    identity.transfer_group("g1", "u2")
    assert identity.groups_of("u1") == ["g1"]
    assert client.called("service_add_group_members") == [(("g1", ["u2", "u3"]), {})]
    assert client.called("service_transfer_group_owner") == [(("g1", "u2"), {"keep_previous_as_member": True})]

