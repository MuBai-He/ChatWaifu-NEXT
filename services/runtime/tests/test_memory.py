"""Structured memory projection, policy, retrieval, and extension-port tests."""

import time
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryRecord
from chatwaifu_runtime.memory.policy import MemoryPolicy
from chatwaifu_runtime.memory.ports import ScoredMemoryReference
from chatwaifu_runtime.memory.repository import MemorySearchHit
from chatwaifu_runtime.memory.retrieval import MemoryRetriever
from fastapi.testclient import TestClient
from httpx2 import Response


class RuntimeHttpClient(Protocol):
    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...

    def put(self, url: str, *, json: object) -> Response: ...

    def patch(self, url: str, *, json: object) -> Response: ...

    def delete(self, url: str) -> Response: ...


def test_implicit_memory_requires_review_and_preserves_sources(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session_id = _create_session(http)

    _submit_and_wait(http, session_id, "我喜欢蓝色")

    assert cast(dict[str, object], http.get("/v1/memory").json())["count"] == 0
    pending = _proposals(http, "pending")
    assert len(pending) == 1
    proposal = pending[0]
    candidate = cast(dict[str, object], proposal["candidate"])
    assert candidate["kind"] == "semantic.preference"
    assert candidate["predicate"] == "preference.like.蓝色"

    accepted = http.post(
        f"/v1/sessions/{session_id}/memory/proposals/{proposal['proposal_id']}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200
    active = _memory_items(http)
    assert len(active) == 1
    assert active[0]["text"] == "我喜欢蓝色"
    assert len(cast(list[str], active[0]["source_event_ids"])) == 1
    sources = cast(
        dict[str, object],
        http.get(f"/v1/memory/{active[0]['memory_id']}/sources").json(),
    )
    assert sources["count"] == 1
    assert cast(list[dict[str, object]], sources["items"])[0]["source_kind"] == "user_turn"

    _submit_and_wait(http, session_id, "我喜欢蓝色")
    assert len(_memory_items(http)) == 1
    assert _proposals(http, "ignored")[0]["rationale"] == "duplicate active memory"


def test_preference_correction_supersedes_instead_of_accumulating(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session_id = _create_session(http)
    _submit_and_wait(http, session_id, "请记住我喜欢蓝色")
    original = _memory_items(http)[0]

    _submit_and_wait(http, session_id, "我不喜欢蓝色")
    proposal = _proposals(http, "pending")[0]
    assert proposal["operation"] == "supersede"
    assert proposal["target_memory_id"] == original["memory_id"]
    response = http.post(
        f"/v1/sessions/{session_id}/memory/proposals/{proposal['proposal_id']}/decision",
        json={"decision": "accept"},
    )
    assert response.status_code == 200

    active = _memory_items(http)
    assert [item["text"] for item in active] == ["我不喜欢蓝色"]
    history = _memory_items(http, include_history=True)
    assert {str(item["state"]) for item in history} == {"active", "superseded"}
    replacement = next(item for item in history if item["state"] == "active")
    assert replacement["supersedes"] == original["memory_id"]


def test_sensitive_memory_always_requires_confirmation_and_is_not_recalled(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session_id = _create_session(http)
    _submit_and_wait(http, session_id, "请记住我的手机号是13800138000")

    assert _memory_items(http) == []
    proposal = _proposals(http, "pending")[0]
    assert cast(dict[str, object], proposal["candidate"])["sensitivity"] == "sensitive"
    accepted = http.post(
        f"/v1/sessions/{session_id}/memory/proposals/{proposal['proposal_id']}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200
    assert _memory_items(http)[0]["sensitivity"] == "sensitive"

    reply = _submit_and_wait(http, session_id, "我的手机号是什么")
    assert "经过策略允许" not in reply


def test_memory_management_correct_pin_and_forget(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session_id = _create_session(http)
    _submit_and_wait(http, session_id, "请记住我叫木白")
    original = _memory_items(http)[0]

    corrected = http.patch(
        f"/v1/sessions/{session_id}/memory/{original['memory_id']}",
        json={"text": "我叫小白"},
    )
    assert corrected.status_code == 200
    corrected_json = cast(dict[str, object], corrected.json())
    assert corrected_json["supersedes"] == original["memory_id"]

    pinned = http.put(
        f"/v1/sessions/{session_id}/memory/{corrected_json['memory_id']}/pinned",
        json={"pinned": True},
    )
    assert pinned.status_code == 200
    assert cast(dict[str, object], pinned.json())["pinned"] is True
    unrelated_reply = _submit_and_wait(http, session_id, "今天天气怎么样")
    assert "我叫小白" in unrelated_reply

    forgotten = http.delete(f"/v1/sessions/{session_id}/memory/{corrected_json['memory_id']}")
    assert forgotten.status_code == 200
    assert _memory_items(http) == []


@pytest.mark.asyncio
async def test_semantic_and_temporal_ports_contribute_without_owning_records() -> None:
    record = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="semantic.fact",
        subject_id="user",
        predicate="profile.pet",
        value="猫",
        text="用户养了一只猫",
        source_event_ids=[uuid4()],
        observed_at=datetime.now(UTC),
        valid_from=datetime.now(UTC),
        confidence=0.9,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository = _PortRepository(record)
    semantic = _SemanticPort(record.memory_id)
    temporal = _TemporalPort(record.memory_id)
    retriever = MemoryRetriever(repository, MemoryPolicy(), semantic, temporal)  # type: ignore[arg-type]

    packet = await retriever.retrieve_context("宠物", [record.namespace], token_budget=100, limit=5)

    excerpt = packet.relevant_memories[0]
    assert excerpt.memory_id == record.memory_id
    assert excerpt.semantic_relevance == 0.8
    assert excerpt.temporal_relevance == 0.6
    assert excerpt.retrieval_sources == ["semantic", "temporal"]


class _PortRepository:
    def __init__(self, record: MemoryRecord) -> None:
        self.record = record

    async def list_pinned(self, namespaces: list[str], limit: int) -> list[MemoryRecord]:
        del namespaces, limit
        return []

    async def search_fts(
        self, query: str, namespaces: list[str], limit: int
    ) -> list[MemorySearchHit]:
        del query, namespaces, limit
        return []

    async def get_many(self, memory_ids: list[UUID]) -> list[MemoryRecord]:
        return [self.record] if self.record.memory_id in memory_ids else []

    async def list_recent(self, namespaces: list[str], limit: int) -> list[MemoryRecord]:
        del namespaces, limit
        return []


class _SemanticPort:
    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id

    async def search(
        self, query: str, namespaces: list[str], limit: int
    ) -> list[ScoredMemoryReference]:
        del query, namespaces, limit
        return [ScoredMemoryReference(self.memory_id, 0.8)]


class _TemporalPort:
    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id

    async def search(
        self,
        query: str,
        namespaces: list[str],
        observed_at: datetime,
        limit: int,
    ) -> list[ScoredMemoryReference]:
        del query, namespaces, observed_at, limit
        return [ScoredMemoryReference(self.memory_id, 0.6)]


def _create_session(http: RuntimeHttpClient) -> str:
    response = http.post("/v1/sessions", json={})
    assert response.status_code == 201
    return str(cast(dict[str, object], response.json())["session_id"])


def _submit_and_wait(http: RuntimeHttpClient, session_id: str, text: str) -> str:
    response = http.post(f"/v1/sessions/{session_id}/turns", json={"text": text})
    assert response.status_code == 202
    generation_id = str(cast(dict[str, object], response.json())["generation_id"])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        events = cast(
            list[dict[str, object]],
            cast(
                dict[str, object],
                http.get(f"/v1/sessions/{session_id}/events?limit=500").json(),
            )["items"],
        )
        for event in events:
            if (
                event["event_type"] == "assistant.generation_completed"
                and str(event.get("generation_id")) == generation_id
            ):
                return str(cast(dict[str, object], event["payload"])["text"])
        time.sleep(0.01)
    raise AssertionError(f"generation {generation_id} did not complete")


def _memory_items(
    http: RuntimeHttpClient, *, include_history: bool = False
) -> list[dict[str, object]]:
    suffix = "?include_tombstoned=true" if include_history else ""
    return cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/memory{suffix}").json())["items"],
    )


def _proposals(http: RuntimeHttpClient, status: str) -> list[dict[str, object]]:
    return cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/memory/proposals?status={status}").json())["items"],
    )
