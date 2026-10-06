"""Operator display-name changes preserve conversational identity and scope."""

from typing import cast

import pytest
from fastapi.testclient import TestClient


def test_rename_preserves_participant_scene_and_existing_sessions(client: TestClient) -> None:
    first = cast(
        dict[str, str], client.post("/v1/participants", json={"display_name": "占位名"}).json()
    )
    second = cast(
        dict[str, str], client.post("/v1/participants", json={"display_name": "小林"}).json()
    )
    ids = [first["participant_id"], second["participant_id"]]
    scene = cast(
        dict[str, object],
        client.post("/v1/scenes", json={"display_name": "讨论", "participant_ids": ids}).json(),
    )
    private = cast(
        dict[str, object],
        client.post("/v1/sessions", json={"participant_id": ids[0]}).json(),
    )
    shared = cast(
        dict[str, object],
        client.post(
            "/v1/sessions", json={"participant_id": ids[0], "scene_id": scene["scene_id"]}
        ).json(),
    )
    # Matching somebody else's name must never merge their identity or memories.
    renamed = client.patch(
        f"/v1/participants/{ids[0]}",
        json={"display_name": "  小林  ", "expected_display_name": "占位名"},
    )
    assert renamed.status_code == 200
    assert renamed.json() == {**first, "display_name": "小林"}
    people = client.get("/v1/participants").json()["items"]
    assert len([p for p in people if p["display_name"] == "小林"]) == 2
    assert client.get("/v1/scenes").json()["items"] == [scene]
    for session in (private, shared):
        assert client.get(f"/v1/sessions/{session['session_id']}").json() == session
    assert private["user_scope"] != shared["user_scope"]


def test_rename_rejects_stale_name_and_unknown_identity(client: TestClient) -> None:
    first = client.patch(
        "/v1/participants/local",
        json={"display_name": "新的名字", "expected_display_name": "主人"},
    )
    assert first.status_code == 200
    stale = client.patch(
        "/v1/participants/local",
        json={"display_name": "覆盖", "expected_display_name": "主人"},
    )
    assert stale.status_code == 409
    unknown = client.patch(
        "/v1/participants/not-registered",
        json={"display_name": "未知", "expected_display_name": "旧名称"},
    )
    assert unknown.status_code == 404
    assert client.get("/v1/participants").json()["items"] == [first.json()]


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "   ", "expected_display_name": "主人"},
        {"display_name": "字" * 81, "expected_display_name": "主人"},
        {"display_name": "名字"},
        {"display_name": "名字", "expected_display_name": "主人", "participant_id": "forged"},
    ],
)
def test_rename_validates_name_and_precondition(client: TestClient, body: dict[str, str]) -> None:
    response = client.patch("/v1/participants/local", json=body)
    assert response.status_code == 422
    assert client.get("/v1/participants").json()["items"][0]["display_name"] == "主人"


def test_rename_requires_runtime_operator_token(client: TestClient) -> None:
    response = client.patch(
        "/v1/participants/local",
        headers={"Authorization": "Bearer invalid-operator-token"},
        json={"display_name": "覆盖", "expected_display_name": "主人"},
    )
    assert response.status_code == 401
    assert client.get("/v1/participants").json()["items"][0]["display_name"] == "主人"
