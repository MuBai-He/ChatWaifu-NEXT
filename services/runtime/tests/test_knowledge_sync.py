"""Owner admission and durable sync semantics through actual Runtime HTTP/SQLite."""

from typing import Any, Protocol
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response


class Push(Protocol):
    def __call__(
        self,
        *mutations: dict[str, Any],
        policies: list[dict[str, Any]] | None = None,
        epoch: int = 0,
    ) -> Response: ...


type SyncFixture = tuple[TestClient, str, str, Push]


@pytest.fixture
def sync(client: TestClient) -> SyncFixture:
    session = client.post("/v1/sessions", json={}).json()["session_id"]
    device = str(uuid4())

    def push(
        *mutations: dict[str, Any], policies: list[dict[str, Any]] | None = None, epoch: int = 0
    ) -> Response:
        return client.post(
            "/v1/personal-information/sync/push",
            json={
                "session_id": session,
                "device_id": device,
                "policy_epoch": epoch,
                "mutations": list(mutations),
                "policies": policies or [],
            },
        )

    return client, session, device, push


def mutation(key: str = "source:one", revision: int = 0, **updates: Any) -> dict[str, Any]:
    return {
        "operation_id": str(uuid4()),
        "expected_revision": revision,
        "record": {
            "record_id": key,
            "kind": "source",
            "room_id": "room:one",
            "text": "周四开会",
            "occurred_at": 1791800000000,
            **updates,
        },
    }


def test_viewer_credential_is_session_bound_and_cannot_escape_knowledge(sync: SyncFixture) -> None:
    c, s, d, _push = sync
    other = c.post("/v1/sessions", json={}).json()["session_id"]
    viewer = c.post("/v1/knowledge/viewer-session", json={"session_id": s, "device_id": d})
    assert viewer.status_code == 200, viewer.text
    assert "HttpOnly" in viewer.headers["set-cookie"]
    cookie = viewer.json()["cookie"].split(";", 1)[0]
    scoped = {"Authorization": "", "Cookie": cookie}
    assert (
        c.get("/v1/knowledge/status", params={"session_id": s}, headers=scoped).status_code == 200
    )
    assert (
        c.get("/v1/knowledge/status", params={"session_id": other}, headers=scoped).status_code
        == 403
    )
    for path in ("/v1/sessions", "/v1/personal-information/status", "/v1/model-configs"):
        assert c.get(path, headers=scoped).status_code == 401
    assert (
        c.post(
            "/v1/knowledge/viewer-session", json={"session_id": s, "device_id": d}, headers=scoped
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/v1/knowledge/query", json={"session_id": other, "question": "test"}, headers=scoped
        ).status_code
        == 403
    )
    # A newly opened native viewer revokes that device's previous credential.
    assert (
        c.post("/v1/knowledge/viewer-session", json={"session_id": s, "device_id": d}).status_code
        == 200
    )
    assert (
        c.get("/v1/knowledge/status", params={"session_id": s}, headers=scoped).status_code == 401
    )


def test_derived_record_is_not_retrievable_after_source_exclusion(sync: SyncFixture) -> None:
    c, s, _d, push = sync
    assert push(mutation()).status_code == 200
    assert (
        push(
            mutation(
                "memory:one",
                kind="memory",
                room_id="manual",
                title="项目事实",
                confirmed=True,
                source_refs=[{"record_id": "source:one", "revision": 1}],
            )
        ).status_code
        == 200
    )
    path = "/v1/knowledge/records/memory:one"
    assert c.get(path, params={"session_id": s}).status_code == 200
    assert (
        push(
            policies=[
                {
                    "operation_id": str(uuid4()),
                    "room_id": "room:one",
                    "expected_revision": 0,
                    "excluded": True,
                }
            ]
        ).status_code
        == 200
    )
    assert c.get(path, params={"session_id": s}).status_code == 403
    assert c.get("/v1/knowledge/records", params={"session_id": s}).json()["items"] == []


def test_conflict_resolution_is_version_checked_and_has_device_change(sync: SyncFixture) -> None:
    c, s, d, push = sync
    push(mutation())
    push(mutation(revision=1, text="网页周五"))
    receipt = push(mutation(revision=1, text="手机周六")).json()["receipts"][0]
    path = "/v1/knowledge/conflicts/" + receipt["conflict_id"] + "/resolve"
    body = {
        "session_id": s,
        "keep_local": False,
        "expected_revision": 1,
        "operation_id": str(uuid4()),
    }
    assert c.post(path, json=body).status_code == 409
    body["expected_revision"] = 2
    assert c.post(path, json=body).status_code == 200
    changes = c.get(
        "/v1/personal-information/sync/pull", params={"session_id": s, "device_id": d}
    ).json()["changes"]
    assert changes[-1]["kind"] == "conflict_resolved"
    assert changes[-1]["value"]["record"]["text"] == "网页周五"


def test_push_pull_retry_and_device_ack(sync: SyncFixture) -> None:
    c, s, d, push = sync
    change = mutation()
    first = push(change)
    assert first.status_code == 200, first.text
    again = push(change)
    assert again.json() == first.json()
    batch = c.get(
        "/v1/personal-information/sync/pull", params={"session_id": s, "device_id": d}
    ).json()
    assert len(batch["changes"]) == 1
    assert batch["changes"][0]["value"]["revision"] == 1
    assert (
        c.post(
            "/v1/personal-information/sync/ack",
            json={"session_id": s, "device_id": d, "cursor": batch["cursor"]},
        ).status_code
        == 200
    )
    status = c.get("/v1/knowledge/status", params={"session_id": s}).json()
    assert status["records"] == 1 and status["devices"][0]["acknowledged"] == batch["cursor"]


def test_cursor_receipt_does_not_claim_phone_applied_a_record(sync: SyncFixture) -> None:
    c, s, d, push = sync
    push(mutation())
    ack: dict[str, Any] = {"session_id": s, "device_id": d, "cursor": 1}
    assert c.post("/v1/personal-information/sync/ack", json=ack).status_code == 200
    path = "/v1/knowledge/records/source:one"
    assert c.get(path, params={"session_id": s}).json()["sync"]["applied_devices"] == 0
    ack["applied"] = [{"record_id": "source:one", "revision": 1}]
    assert c.post("/v1/personal-information/sync/ack", json=ack).status_code == 200
    assert c.get(path, params={"session_id": s}).json()["sync"]["applied_devices"] == 1
    push(mutation(revision=1, text="网页又改期"))
    assert c.get(path, params={"session_id": s}).json()["sync"]["applied_devices"] == 0


def test_forgetting_scrubs_old_sync_versions_and_retry_receipts(sync: SyncFixture) -> None:
    c, s, d, push = sync
    first = mutation(text="这段合成私密文字应被遗忘")
    push(first)
    forget = mutation(revision=1)
    forget["action"] = "forget"
    push(forget)
    versions = c.get("/v1/personal-information/sync/pull", params={"session_id": s, "device_id": d})
    assert "合成私密文字" not in versions.text
    assert "合成私密文字" not in push(first).text
    assert (
        c.get(
            "/v1/knowledge/records/source:one", params={"session_id": s, "revision": 1}
        ).status_code
        == 404
    )


def test_concurrent_edit_retains_both_values(sync: SyncFixture) -> None:
    c, s, d, push = sync
    assert push(mutation()).status_code == 200
    assert push(mutation(revision=1, text="改周五")).json()["receipts"][0]["status"] == "accepted"
    result = push(mutation(revision=1, text="手机离线改周六")).json()["receipts"][0]
    assert result["status"] == "conflict" and result["current"]["text"] == "改周五"
    conflicts = c.get("/v1/knowledge/conflicts", params={"session_id": s}).json()["items"]
    assert conflicts[0]["proposed"]["record"]["text"] == "手机离线改周六"
    batch = c.get(
        "/v1/personal-information/sync/pull", params={"session_id": s, "device_id": d}
    ).json()
    assert [x["value"]["text"] for x in batch["changes"]] == ["周四开会", "改周五"]


def test_policy_retry_and_exclusion_fence(sync: SyncFixture) -> None:
    c, s, _d, push = sync
    policy = {
        "operation_id": str(uuid4()),
        "room_id": "room:one",
        "expected_revision": 0,
        "excluded": True,
    }
    first = push(policies=[policy])
    assert first.status_code == 200
    assert push(policies=[policy]).status_code == 200
    assert c.get("/v1/knowledge/status", params={"session_id": s}).json()["policy_epoch"] == 1
    assert push(mutation()).status_code == 409
    response = push(mutation(), epoch=1).json()["receipts"][0]
    assert response["status"] == "blocked" and response["reason"] == "conversation_excluded"
    restore = {**policy, "operation_id": str(uuid4()), "expected_revision": 1, "excluded": False}
    assert push(policies=[restore], epoch=1).status_code == 200
    assert push(mutation(), epoch=2).json()["receipts"][0]["status"] == "accepted"


def test_forgetting_cannot_be_undone_by_delayed_upload(sync: SyncFixture) -> None:
    c, s, _d, push = sync
    push(mutation())
    delete = mutation(revision=1)
    delete["action"] = "forget"
    assert push(delete).json()["receipts"][0]["record"]["deleted"]
    assert push(mutation(revision=2), epoch=1).json()["receipts"][0]["status"] == "conflict"
    assert c.get("/v1/knowledge/status", params={"session_id": s}).json()["records"] == 0


def test_changed_operation_payload_is_rejected(sync: SyncFixture) -> None:
    _c, _s, _d, push = sync
    op = mutation()
    push(op)
    op["record"]["text"] = "changed under reused id"
    assert push(op).status_code == 409


def test_private_owner_only_and_redacted_validation(sync: SyncFixture) -> None:
    c, s, d, _push = sync
    body = {
        "session_id": str(uuid4()),
        "device_id": d,
        "policy_epoch": 0,
        "mutations": [mutation()],
    }
    assert c.post("/v1/personal-information/sync/push", json=body).status_code == 403
    body["session_id"] = s
    body["secret_marker"] = "private-secret-canary"
    result = c.post("/v1/personal-information/sync/push", json=body)
    assert result.status_code == 422 and "private-secret-canary" not in result.text
    assert (
        c.get(
            "/v1/knowledge/status",
            params={"session_id": s},
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )


def test_source_references_must_exist_at_exact_revision(sync: SyncFixture) -> None:
    _c, _s, _d, push = sync
    push(mutation())
    wrong = mutation(
        "memory:one",
        kind="memory",
        title="开会约定",
        confirmed=True,
        source_refs=[{"record_id": "source:one", "revision": 99}],
    )
    assert push(wrong).status_code == 409
    wrong["record"]["source_refs"][0]["revision"] = 1
    assert push(wrong).json()["receipts"][0]["status"] == "accepted"


def test_pull_cursor_paginates_without_skipping(sync: SyncFixture) -> None:
    c, s, d, push = sync
    push(*(mutation(f"source:{i}") for i in range(4)))
    params = {"session_id": s, "device_id": d, "limit": 2}
    first = c.get("/v1/personal-information/sync/pull", params=params).json()
    second = c.get(
        "/v1/personal-information/sync/pull", params={**params, "after": first["cursor"]}
    ).json()
    assert first["more"] and not second["more"]
    assert len({x["id"] for x in first["changes"] + second["changes"]}) == 4


def test_clients_cannot_forge_formal_memory_origin(sync: SyncFixture) -> None:
    c, session, _device, push = sync
    candidate = mutation(
        "formal:" + str(uuid4()),
        kind="memory",
        title="伪造命名空间",
        confirmed=True,
        origin={
            "source_type": "formal_memory",
            "memory_id": str(uuid4()),
            "namespace": "user/local/global",
        },
    )
    assert push(candidate).status_code == 403
    assert c.get("/v1/knowledge/status", params={"session_id": session}).json()["records"] == 0
