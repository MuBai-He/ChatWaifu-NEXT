"""Desktop companion policy, persistence, and idle resource tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.attention import evaluate_attention
from chatwaifu_runtime.companion.models import CompanionSettings
from chatwaifu_runtime.companion.resources import ResourceLifecycleService
from chatwaifu_runtime.companion.settings import CompanionSettingsService
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from fastapi.testclient import TestClient


def test_push_to_talk_is_always_intentional() -> None:
    decision = evaluate_attention("我们聊 Python 吧", "push_to_talk", CompanionSettings())

    assert decision.accepted is True
    assert decision.text == "我们聊 Python 吧"
    assert decision.reason == "push_to_talk"


def test_open_mic_requires_an_early_wake_phrase_and_strips_it() -> None:
    settings = CompanionSettings(wake_phrases=("宁宁", "绫地宁宁"))

    accepted = evaluate_attention("宁宁，我们聊 Python 吧", "open_mic", settings)
    ignored = evaluate_attention("我刚才和宁宁聊过这件事", "open_mic", settings)

    assert accepted.accepted is True
    assert accepted.text == "我们聊 Python 吧"
    assert accepted.wake_phrase == "宁宁"
    assert ignored.accepted is False
    assert ignored.reason == "not_addressed"


def test_companion_settings_round_trip_through_runtime(client: TestClient) -> None:
    initial = client.get("/v1/companion/settings")
    assert initial.status_code == 200
    payload = initial.json()
    payload.update(
        {
            "wake_phrases": ["宁宁", "小宁宁"],
            "proactive_enabled": True,
            "proactive_idle_minutes": 30,
            "resource_idle_minutes": 15,
        }
    )
    payload.pop("schema_version")
    payload.pop("updated_at")

    updated = client.put("/v1/companion/settings", json=payload)

    assert updated.status_code == 200
    assert updated.json()["wake_phrases"] == ["宁宁", "小宁宁"]
    assert client.get("/v1/companion/settings").json()["resource_idle_minutes"] == 15


class _FakeTts:
    def __init__(self) -> None:
        self.unloads = 0

    async def deactivate_idle(self) -> bool:
        self.unloads += 1
        return True


class _FakeStt:
    kind = "fake"

    def __init__(self) -> None:
        self.unloads = 0

    async def deactivate(self) -> bool:
        self.unloads += 1
        return True


@pytest.mark.asyncio
async def test_resource_sleep_unloads_idle_models_and_wakes_lazily(tmp_path: Path) -> None:
    database = Database(tmp_path / "runtime.db", StorageConfig())
    await database.open()
    settings = CompanionSettingsService(database)
    await settings.start()
    tts = _FakeTts()
    stt = _FakeStt()
    resources = ResourceLifecycleService(
        settings,
        ActivityTracker(),
        tts,
        stt,
    )
    try:
        sleeping = await resources.sleep_now()
        awake = resources.wake()
    finally:
        await database.close()

    assert sleeping.state == "sleeping"
    assert awake.state == "active"
    assert tts.unloads == 1
    assert stt.unloads == 1


@pytest.mark.asyncio
async def test_resource_sleep_refuses_to_cancel_active_work(tmp_path: Path) -> None:
    database = Database(tmp_path / "runtime.db", StorageConfig())
    await database.open()
    settings = CompanionSettingsService(database)
    await settings.start()
    resources = ResourceLifecycleService(
        settings,
        ActivityTracker(),
        _FakeTts(),
        _FakeStt(),
        busy=lambda: True,
    )
    try:
        with pytest.raises(RuntimeError, match="当前回合"):
            await resources.sleep_now()
    finally:
        await database.close()
