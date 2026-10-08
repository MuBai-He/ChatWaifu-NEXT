"""Independent decision routing, native acceptance, private credentials and upgrade safety."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx2
import pytest
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.providers import model_config
from chatwaifu_runtime.providers.model_config import (
    LocalModelSecretStore,
    ModelConfigurationService,
)
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from chatwaifu_runtime.providers.typesafe import TypeSafeBehaviorProvider
from fastapi.testclient import TestClient

from services.runtime.tests.test_api import RuntimeHttpClient


@pytest.mark.asyncio
async def test_schema_46_upgrade_preserves_all_model_rows_secrets_and_restart(
    tmp_path: Path, runtime_settings: Settings
) -> None:
    path = tmp_path / "upgrade.db"
    old = Database(path, runtime_settings.storage, migrations=MIGRATIONS[:-1])
    await old.open()
    try:
        async with old.transaction() as connection:
            for role in ("chat", "memory_extraction", "memory_summary", "embedding"):
                await connection.execute(
                    "INSERT INTO model_role_configs VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        role,
                        "disabled",
                        role + "-kept",
                        "",
                        60,
                        8192,
                        0,
                        "2026-10-06T00:00:00+00:00",
                        '{"output_reserve_tokens":123}',
                    ),
                )
        before = [
            tuple(row)
            for row in await old.fetchall("SELECT * FROM model_role_configs ORDER BY role")
        ]
        ledger = [
            tuple(row)
            for row in await old.fetchall("SELECT * FROM schema_migrations ORDER BY version")
        ]
    finally:
        await old.close()
    secrets = LocalModelSecretStore(runtime_settings.config_dir / "model-secrets.json")
    secrets.set("chat", "chat-private-key")
    secret_bytes = (runtime_settings.config_dir / "model-secrets.json").read_bytes()
    database = Database(path, runtime_settings.storage)
    await database.open()
    try:
        models = ModelConfigurationService(database, runtime_settings)
        await models.start()
        assert models.get("behavior_decision").provider == "inherit_chat"
        assert [
            tuple(row)
            for row in await database.fetchall(
                "SELECT * FROM model_role_configs WHERE role != 'behavior_decision' ORDER BY role"
            )
        ] == before
        assert [
            tuple(row)
            for row in await database.fetchall(
                "SELECT * FROM schema_migrations WHERE version < 47 ORDER BY version"
            )
        ] == ledger
        assert (runtime_settings.config_dir / "model-secrets.json").read_bytes() == secret_bytes
        config = models.get("behavior_decision").model_copy(
            update={
                "provider": "openai_compatible",
                "model": "decision-independent",
                "base_url": "https://decision.invalid/v1",
                "timeout_seconds": 12,
            }
        )
        await models.update(config, api_key="decision-private-key")
        saved = models.get("behavior_decision")
    finally:
        await database.close()
    await database.open()
    try:
        restarted = ModelConfigurationService(database, runtime_settings)
        await restarted.start()
        assert restarted.get("behavior_decision") == saved
        assert secrets.get("chat") == "chat-private-key"
        assert secrets.get("behavior_decision") == "decision-private-key"
        integrity = await database.fetchone("PRAGMA integrity_check")
        assert integrity is not None and integrity[0] == "ok"
        assert not await database.fetchall("PRAGMA foreign_key_check")
    finally:
        await database.close()


@pytest.mark.parametrize("prose_only", [False, True])
def test_live_save_routes_native_decision_and_chat_to_separate_models_and_keys(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: Settings,
    prose_only: bool,
) -> None:
    requests: list[httpx2.Request] = []
    bodies: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        body = cast(dict[str, object], json.loads(request.content))
        bodies.append(body)
        if body.get("tools") and not prose_only:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "decision-1",
                        "type": "function",
                        "function": {
                            "name": "decide_behavior",
                            "arguments": json.dumps(
                                {
                                    "action": "respond",
                                    "reason": "test invitation",
                                    "source_refs": ["model-probe"],
                                }
                            ),
                        },
                    }
                ]
            }
            finish = "tool_calls"
        else:
            delta = {"content": "OK"}
            finish = "stop"
        chunks: list[dict[str, object]] = [
            {"choices": [{"delta": delta, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": finish}]},
        ]
        stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=stream + "data: [DONE]\n\n"
        )

    def provider(
        *,
        base_url: str,
        model: str,
        api_key: str | Callable[[], str | None] | None,
        timeout_seconds: float,
    ) -> OpenAiCompatibleLlmProvider:
        return OpenAiCompatibleLlmProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=httpx2.MockTransport(handle),
        )

    monkeypatch.setattr(model_config, "OpenAiCompatibleLlmProvider", provider)
    http = cast(RuntimeHttpClient, client)
    chat = http.put(
        "/v1/model-configurations/chat",
        json={
            "provider": "openai_compatible",
            "model": "chat-main",
            "base_url": "https://chat.invalid/v1",
            "api_key": "chat-only-secret",
        },
    ).json()
    # Default inheritance uses the current chat route and key; a separate decision save
    # takes effect on the next decision, without restart or changes to the chat row.
    inherited = http.post("/v1/model-configurations/behavior_decision/test", json={})
    assert inherited.status_code == (502 if prose_only else 200)
    assert requests[-1].url.host == "chat.invalid"
    assert requests[-1].headers["Authorization"] == "Bearer chat-only-secret"
    assert bodies[-1]["model"] == "chat-main"
    response = http.put(
        "/v1/model-configurations/behavior_decision",
        json={
            "provider": "openai_compatible",
            "model": "decision-small",
            "base_url": "https://decision.invalid/v1",
            "timeout_seconds": 10,
            "api_key": "decision-only-secret",
        },
    )
    assert response.status_code == 200 and response.json()["api_key_configured"]
    assert "decision-only-secret" not in response.text
    result = http.post("/v1/model-configurations/behavior_decision/test", json={})
    assert result.status_code == (502 if prose_only else 200)
    if not prose_only:
        assert result.json()["action"] == "respond"
    assert requests[-1].url.host == "decision.invalid"
    assert requests[-1].headers["Authorization"] == "Bearer decision-only-secret"
    assert bodies[-1]["model"] == "decision-small" and bodies[-1]["tool_choice"] == "required"
    listed = http.get("/v1/model-configurations?include_behavior_decision=true").json()
    assert next(item for item in listed["items"] if item["role"] == "chat") == chat
    assert "decision-only-secret" not in str(listed)
    assert http.post("/v1/model-configurations/chat/test", json={}).status_code == 200
    assert requests[-1].url.host == "chat.invalid"
    assert requests[-1].headers["Authorization"] == "Bearer chat-only-secret"
    # Return to explicit inheritance; clearing the independent credential cannot
    # clear or replace the chat credential.
    assert (
        http.put(
            "/v1/model-configurations/behavior_decision",
            json={
                "provider": "inherit_chat",
                "model": "inherit-chat",
                "timeout_seconds": 30,
                "clear_api_key": True,
            },
        ).status_code
        == 200
    )
    store = LocalModelSecretStore(runtime_settings.config_dir / "model-secrets.json")
    assert store.get("behavior_decision") is None and store.get("chat") == "chat-only-secret"
    http.post("/v1/model-configurations/behavior_decision/test", json={})
    assert requests[-1].url.host == "chat.invalid"


def test_api_jev_uses_native_adapter_and_only_the_decision_key(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: Settings,
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.path == "/v1/systemone"
        assert request.headers["Authorization"] == "Bearer jev-only-private-key"
        body = json.loads(request.content)
        assert body["model"] == "jev-latest" and "tools" not in body
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "respond",
                        "confidence": 1.0,
                        "probabilities": {"wait": 0.0, "respond": 1.0, "clarify": 0.0},
                    }
                },
            },
        )

    def native(
        *, base_url: str, model: str, api_key: Callable[[], str | None], timeout_seconds: float
    ) -> TypeSafeBehaviorProvider:
        return TypeSafeBehaviorProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=httpx2.MockTransport(handle),
        )

    monkeypatch.setattr(model_config, "TypeSafeBehaviorProvider", native)
    http = cast(RuntimeHttpClient, client)
    chat = next(
        item
        for item in http.get("/v1/model-configurations?include_behavior_decision=true").json()[
            "items"
        ]
        if item["role"] == "chat"
    )
    updated = http.put(
        "/v1/model-configurations/behavior_decision",
        json={
            "provider": "typesafe",
            "model": "jev-latest",
            "base_url": "https://api.typesafe.ai/v1",
            "timeout_seconds": 10,
            "api_key": "jev-only-private-key",
        },
    )
    assert updated.status_code == 200 and updated.json()["api_key_configured"]
    assert "jev-only-private-key" not in updated.text
    probe = http.post("/v1/model-configurations/behavior_decision/test", json={})
    assert probe.status_code == 200 and probe.json()["action"] == "respond" and len(requests) == 1
    assert (
        next(
            item
            for item in http.get("/v1/model-configurations?include_behavior_decision=true").json()[
                "items"
            ]
            if item["role"] == "chat"
        )
        == chat
    )
    store = LocalModelSecretStore(runtime_settings.config_dir / "model-secrets.json")
    assert store.get("behavior_decision") == "jev-only-private-key" and store.get("chat") is None


@pytest.mark.parametrize(
    "role,body",
    [
        ("chat", {"provider": "inherit_chat", "model": "inherit-chat"}),
        (
            "chat",
            {
                "provider": "typesafe",
                "model": "jev-latest",
                "base_url": "https://api.typesafe.ai/v1",
            },
        ),
        ("behavior_decision", {"provider": "demo", "model": "demo", "timeout_seconds": 30}),
        (
            "behavior_decision",
            {"provider": "inherit_chat", "model": "inherit-chat", "timeout_seconds": 31},
        ),
        (
            "behavior_decision",
            {
                "provider": "inherit_chat",
                "model": "inherit-chat",
                "timeout_seconds": 30,
                "enabled": False,
            },
        ),
    ],
)
def test_invalid_decision_routes_do_not_modify_persisted_configuration(
    client: TestClient, role: str, body: dict[str, object]
) -> None:
    http = cast(RuntimeHttpClient, client)
    before = http.get("/v1/model-configurations?include_behavior_decision=true").json()
    assert http.put("/v1/model-configurations/" + role, json=body).status_code == 409
    assert http.get("/v1/model-configurations?include_behavior_decision=true").json() == before


def test_older_clients_keep_the_original_four_model_roles(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    legacy = http.get("/v1/model-configurations").json()
    assert legacy["count"] == 4
    assert {item["role"] for item in legacy["items"]} == {
        "chat",
        "memory_extraction",
        "memory_summary",
        "embedding",
    }
    current = http.get("/v1/model-configurations?include_behavior_decision=true").json()
    assert current["count"] == 5
    assert (
        next(item for item in current["items"] if item["role"] == "behavior_decision")["provider"]
        == "inherit_chat"
    )
