"""Desktop companion policy, persistence, and idle resource tests."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.ambient import decide_proactive, is_quiet_time
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


def test_proactive_policy_respects_quiet_hours_busy_state_and_budget() -> None:
    china = timezone(timedelta(hours=8))
    quiet_now = datetime(2026, 8, 28, 23, 30, tzinfo=china)
    daytime = datetime(2026, 8, 28, 15, 0, tzinfo=china)
    settings = CompanionSettings(
        proactive_enabled=True,
        proactive_idle_minutes=10,
        proactive_cooldown_minutes=60,
        proactive_daily_budget=2,
    )

    assert is_quiet_time(quiet_now, "23:00", "08:00") is True
    assert (
        decide_proactive(
            settings,
            now=quiet_now,
            idle_seconds=900,
            generation_active=False,
            proactive_today=0,
            last_proactive_at=None,
        ).reason
        == "quiet_hours"
    )
    assert (
        decide_proactive(
            settings,
            now=daytime,
            idle_seconds=900,
            generation_active=True,
            proactive_today=0,
            last_proactive_at=None,
        ).reason
        == "conversation_busy"
    )
    assert (
        decide_proactive(
            settings,
            now=daytime,
            idle_seconds=900,
            generation_active=False,
            proactive_today=2,
            last_proactive_at=None,
        ).reason
        == "daily_budget_exhausted"
    )
    assert (
        decide_proactive(
            settings,
            now=daytime,
            idle_seconds=900,
            generation_active=False,
            proactive_today=0,
            last_proactive_at=daytime - timedelta(minutes=10),
        ).reason
        == "cooldown_active"
    )


def test_manual_proactive_turn_is_audited_without_fabricating_user_text(
    client: TestClient,
) -> None:
    created = cast(dict[str, object], client.post("/v1/sessions", json={}).json())
    session_id = str(created["session_id"])

    accepted = client.post(f"/v1/sessions/{session_id}/companion/proactive")
    assert accepted.status_code == 200
    generation_id = str(cast(dict[str, object], accepted.json())["generation_id"])

    events: list[dict[str, object]] = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        payload = cast(dict[str, object], client.get(f"/v1/sessions/{session_id}/events").json())
        events = cast(list[dict[str, object]], payload["items"])
        if any(
            item["event_type"] == "assistant.generation_completed"
            and str(item.get("generation_id")) == generation_id
            for item in events
        ):
            break
        time.sleep(0.01)

    generation_events = [item for item in events if str(item.get("generation_id")) == generation_id]
    assert any(item["event_type"] == "companion.proactive_triggered" for item in events)
    assert not any(item["event_type"] == "user.turn_committed" for item in generation_events)
    completed = next(
        item for item in generation_events if item["event_type"] == "assistant.generation_completed"
    )
    assert "我就在这里" in str(cast(dict[str, object], completed["payload"])["text"])

    messages_payload = cast(
        dict[str, object], client.get(f"/v1/sessions/{session_id}/messages").json()
    )
    messages = cast(list[dict[str, object]], messages_payload["items"])
    assert [item["role"] for item in messages] == ["assistant"]
    assert "Runtime ambient event" not in str(messages[0]["committed_text"])

    status = cast(dict[str, object], client.get("/v1/companion/status").json())
    assert status["proactive_today"] == 1
    assert status["last_proactive_at"] is not None


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


@pytest.mark.asyncio
async def test_resource_sleep_aborts_if_voice_activity_arrives_during_unload(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "runtime.db", StorageConfig())
    await database.open()
    settings = CompanionSettingsService(database)
    await settings.start()
    resources: ResourceLifecycleService

    class _TouchingTts(_FakeTts):
        async def deactivate_idle(self) -> bool:
            resources.touch()
            return await super().deactivate_idle()

    resources = ResourceLifecycleService(
        settings,
        ActivityTracker(),
        _TouchingTts(),
        _FakeStt(),
    )
    try:
        with pytest.raises(RuntimeError, match="新的活动"):
            await resources.sleep_now()
        assert resources.status().state == "active"
    finally:
        await database.close()
