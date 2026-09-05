"""Authenticated library settings and binary preview/delete contract."""
# pyright: reportPrivateUsage=false

from functools import partial
from typing import cast
from uuid import UUID

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate
from fastapi.testclient import TestClient
from test_sticker_repository import PNG_1X1, _seed_source_chain


def test_sticker_api_auth_settings_preview_delete(client: TestClient) -> None:
    route = "/v1/sticker-library"
    assert client.get(route, headers={"Authorization": "Bearer invalid"}).status_code == 401
    snapshot = client.get(route).json()
    assert snapshot["settings"]["learning_enabled"] is False
    assert snapshot["items"] == []
    assert client.get(route + "?character_id=another").status_code == 400
    updated = client.put(
        route + "/settings", json={"learning_enabled": True, "expected_revision": 0}
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert (
        client.put(
            route + "/settings", json={"learning_enabled": False, "expected_revision": 0}
        ).status_code
        == 409
    )
    container = cast(RuntimeContainer, client.app.state.container)  # type: ignore[union-attr]
    assert client.portal is not None
    conn_id, gen_id = client.portal.call(
        partial(_seed_source_chain, container.database, scope="local", character_id="default")
    )
    candidate = StickerSaveCandidate(
        data=PNG_1X1,
        label="猫",
        description="开心小猫",
        expression="happy",
        source_connection_id=UUID(conn_id),
        generation_id=UUID(gen_id),
    )
    saved = client.portal.call(
        partial(
            container.sticker_repository.save, "local", "default", candidate, expected_revision=1
        )
    )
    assert saved is not None
    image_route = route + "/" + saved.sticker_id + "/image"
    preview = client.get(image_route)
    assert preview.status_code == 200 and preview.content == PNG_1X1
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["content-type"] == "image/png"
    assert client.get(image_route, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.delete(route + "/" + saved.sticker_id).json()["deleted"] is True
    assert client.get(image_route).status_code == 404
    assert client.get(route).json()["items"] == []
