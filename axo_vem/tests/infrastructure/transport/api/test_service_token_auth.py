"""Only the Control Plane may call a VEM: its service token plus the user it acts for."""
from fastapi.testclient import TestClient

from axo_vem.infrastructure.auth.service_token import ACTING_USER_HEADER

from .conftest import CP_HEADERS


def test_cp_token_and_acting_user_are_accepted(client):
    assert client.get("/endpoints").status_code == 200


def test_missing_token_is_401(anonymous_client):
    assert anonymous_client.get("/endpoints", headers={ACTING_USER_HEADER: "user-1"}).status_code == 401


def test_wrong_token_is_401(anonymous_client):
    headers = {"Authorization": "Bearer not-the-cp", ACTING_USER_HEADER: "user-1"}
    assert anonymous_client.get("/endpoints", headers=headers).status_code == 401


def test_missing_acting_user_is_400(anonymous_client):
    headers = {"Authorization": CP_HEADERS["Authorization"]}
    assert anonymous_client.get("/endpoints", headers=headers).status_code == 400


def test_no_configured_token_refuses_everything(collections, activity_repository):
    from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
    from axo_vem.infrastructure.transport.api.app import create_app

    app = create_app(
        collections=collections, activity_repository=activity_repository,
        current_user_dependency=build_service_token_dependency(""),
    )
    assert TestClient(app, headers=CP_HEADERS).get("/endpoints").status_code == 401


def test_health_needs_no_token(anonymous_client):
    assert anonymous_client.get("/health").status_code == 200


def test_websockets_need_the_cp_token(collections, activity_repository):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from axo_vem.infrastructure.transport.api.app import create_app
    from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster

    from .conftest import SERVICE_TOKEN

    app = create_app(
        collections=collections, activity_repository=activity_repository,
        broadcaster=Broadcaster(), service_token=SERVICE_TOKEN,
    )
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/functions?token={SERVICE_TOKEN}"):
            pass  # accepted
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/functions?token=wrong") as ws:
                ws.receive_text()
