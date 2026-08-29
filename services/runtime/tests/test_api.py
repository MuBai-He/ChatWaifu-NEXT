"""Runtime HTTP and WebSocket acceptance tests."""

import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient
from httpx2 import Response


class RuntimeHttpClient(Protocol):
    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...

    def put(self, url: str, *, json: object) -> Response: ...

    def delete(self, url: str) -> Response: ...

    def options(self, url: str, *, headers: dict[str, str]) -> Response: ...


def test_browser_cors_allows_memory_mutation_methods(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    response = http.options(
        "/v1/sessions/session-id/memory/memory-id/pinned",
        headers={
            "Origin": runtime_settings.runtime.web_origin,
            "Access-Control-Request-Method": "PUT",
        },
    )

    assert response.status_code == 200
    allowed = {
        method.strip() for method in response.headers["access-control-allow-methods"].split(",")
    }
    assert {"PUT", "PATCH", "DELETE"}.issubset(allowed)


def test_packaged_tauri_origin_can_call_runtime(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    response = http.options(
        "/v1/runtime/health",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_health_session_persistence_and_event_stream(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    health = http.get("/v1/runtime/health")
    assert health.status_code == 200
    health_json = cast(dict[str, object], health.json())
    assert health_json["status"] == "ok"
    assert health_json["providers"] == {
        "llm": "demo",
        "tts": "fake",
        "stt": "disabled",
    }

    created = http.post("/v1/sessions", json={"character_id": "default"})
    assert created.status_code == 201
    session = cast(dict[str, object], created.json())
    assert session["state"] == "ready"
    session_id = str(session["session_id"])

    tts_providers = cast(
        dict[str, object], http.get(f"/v1/tts/providers?session_id={session_id}").json()
    )
    assert tts_providers["schema_version"] == "1.0"
    tts_items = cast(list[dict[str, object]], tts_providers["items"])
    assert tts_items[0]["provider_id"] == "fake"
    assert tts_items[0]["selected"] is True
    selected_tts = http.put(
        f"/v1/sessions/{session_id}/tts/provider",
        json={"provider_id": "fake"},
    )
    assert selected_tts.status_code == 200
    assert cast(dict[str, object], selected_tts.json())["provider_id"] == "fake"

    fetched = http.get(f"/v1/sessions/{session_id}")
    assert fetched.status_code == 200
    fetched_json = cast(dict[str, object], fetched.json())
    assert fetched_json["session_id"] == session_id

    events_response = http.get(f"/v1/sessions/{session_id}/events")
    events_json = cast(dict[str, object], events_response.json())
    events = cast(list[dict[str, object]], events_json["items"])
    assert [event["event_type"] for event in events] == ["session.created"]
    assert events[0]["sequence"] == 1

    closed = http.delete(f"/v1/sessions/{session_id}")
    assert closed.status_code == 200
    closed_json = cast(dict[str, object], closed.json())
    assert closed_json["state"] == "closed"


def test_websocket_announces_runtime(client: TestClient) -> None:
    with client.websocket_connect("/v1/events") as websocket:
        event = cast(dict[str, object], websocket.receive_json())
    validated = GenericCoreEvent.model_validate(event)
    assert event["event_type"] == "system.runtime_started"
    assert validated.source == "runtime.api"


def test_websocket_replays_durable_events_after_sequence_without_duplicates(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "测试断线恢复")
    events = cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["items"],
    )
    assert len(events) > 2
    cursor = int(str(events[-3]["sequence"]))

    with client.websocket_connect(
        f"/v1/events?session_id={session_id}&after_sequence={cursor}"
    ) as websocket:
        started = GenericCoreEvent.model_validate(websocket.receive_json())
        replayed = [
            cast(dict[str, object], websocket.receive_json()),
            cast(dict[str, object], websocket.receive_json()),
        ]

    assert started.event_type == "system.runtime_started"
    assert [int(str(event["sequence"])) for event in replayed] == [cursor + 1, cursor + 2]


def test_recovery_snapshot_replays_from_an_active_generation(
    runtime_settings: Settings,
) -> None:
    slow_llm = runtime_settings.llm.model_copy(update={"demo_chunk_delay_ms": 1_000})
    settings = runtime_settings.model_copy(update={"llm": slow_llm})
    with TestClient(create_app(settings)) as client:
        http = cast(RuntimeHttpClient, client)
        session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
        session_id = str(session["session_id"])
        accepted = cast(
            dict[str, object],
            http.post(f"/v1/sessions/{session_id}/turns", json={"text": "生成中断线"}).json(),
        )
        recovery = cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/recovery").json())
        assert recovery["active_generation_id"] == accepted["generation_id"]
        assert int(str(recovery["after_sequence"])) < int(str(recovery["last_sequence"]))
        messages = cast(list[dict[str, object]], recovery["messages"])
        assert [message["role"] for message in messages] == ["user"]

        with client.websocket_connect(
            f"/v1/events?session_id={session_id}&after_sequence={recovery['after_sequence']}"
        ) as websocket:
            websocket.receive_json()
            generation_started = cast(dict[str, object], websocket.receive_json())
        assert generation_started["event_type"] == "assistant.generation_started"
        assert generation_started["generation_id"] == accepted["generation_id"]


def test_model_roles_are_independent_and_api_keys_never_echo(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    listed = cast(dict[str, object], http.get("/v1/model-configurations").json())
    items = cast(list[dict[str, object]], listed["items"])
    assert {str(item["role"]) for item in items} == {
        "chat",
        "memory_extraction",
        "memory_summary",
        "embedding",
    }
    original_chat = next(item for item in items if item["role"] == "chat")

    updated_response = http.put(
        "/v1/model-configurations/memory_summary",
        json={
            "provider": "openai_compatible",
            "model": "summary-test-model",
            "base_url": "http://127.0.0.1:9999/v1",
            "timeout_seconds": 10,
            "context_window": 16_384,
            "enabled": True,
            "api_key": "write-only-test-secret",
        },
    )
    assert updated_response.status_code == 200
    updated = cast(dict[str, object], updated_response.json())
    assert updated["role"] == "memory_summary"
    assert updated["model"] == "summary-test-model"
    assert updated["api_key_configured"] is True
    assert "api_key" not in updated
    assert "write-only-test-secret" not in updated_response.text

    reloaded = cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get("/v1/model-configurations").json())["items"],
    )
    assert next(item for item in reloaded if item["role"] == "chat") == original_chat
    assert "write-only-test-secret" not in str(reloaded)
    secret_file = runtime_settings.config_dir / "model-secrets.json"
    if os.name == "posix":
        assert secret_file.stat().st_mode & 0o777 == 0o600


def test_aliyun_tts_configuration_is_persisted_without_echoing_api_key(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    catalog = cast(dict[str, object], http.get("/v1/tts/configurations").json())
    entries = cast(list[dict[str, object]], catalog["items"])
    assert catalog["count"] == 2
    assert {entry["provider_id"] for entry in entries} == {
        "aliyun_qwen_realtime",
        "aliyun_cosyvoice_realtime",
    }
    assert all("configuration_schema" in entry for entry in entries)
    assert all("ui_schema" in entry for entry in entries)
    assert all("configuration" in entry for entry in entries)

    current = cast(
        dict[str, object],
        http.get("/v1/tts/configurations/aliyun_qwen_realtime").json(),
    )
    assert current["voice_id"] == "qwen-tts-vc-bailian-voice-20260828030329088-e738"
    assert current["api_key_configured"] is False

    response = http.put(
        "/v1/tts/configurations/aliyun_qwen_realtime",
        json={
            "enabled": True,
            "model": "qwen3-tts-vc-realtime-2026-01-15",
            "voice_id": "qwen-tts-vc-bailian-voice-20260828030329088-e738",
            "region": "beijing",
            "workspace_id": "",
            "language_type": "Auto",
            "sample_rate": 24000,
            "speech_rate": 1.0,
            "volume": 50,
            "pitch_rate": 1.0,
            "timeout_seconds": 45,
            "max_audio_bytes": 32000000,
            "api_key": "write-only-aliyun-secret",
        },
    )
    assert response.status_code == 200
    updated = cast(dict[str, object], response.json())
    assert updated["enabled"] is True
    assert updated["api_key_configured"] is True
    assert "api_key" not in updated
    assert "write-only-aliyun-secret" not in response.text
    secret_file = runtime_settings.config_dir / "tts-secrets.json"
    if os.name == "posix":
        assert secret_file.stat().st_mode & 0o777 == 0o600

    partial = http.put(
        "/v1/tts/configurations/aliyun_qwen_realtime",
        json={"speech_rate": 1.1},
    )
    assert partial.status_code == 200
    assert cast(dict[str, object], partial.json())["speech_rate"] == 1.1
    assert cast(dict[str, object], partial.json())["api_key_configured"] is True

    assert http.get("/v1/tts/configurations/not-installed").status_code == 404
    assert http.put("/v1/tts/configurations/not-installed", json={}).status_code == 404
    assert http.post("/v1/tts/configurations/not-installed/test", json={}).status_code == 404

    cosy_current = cast(
        dict[str, object],
        http.get("/v1/tts/configurations/aliyun_cosyvoice_realtime").json(),
    )
    assert cosy_current["model"] == "cosyvoice-v3.5-plus"
    assert cosy_current["api_key_configured"] is True

    cosy_response = http.put(
        "/v1/tts/configurations/aliyun_cosyvoice_realtime",
        json={
            "enabled": True,
            "model": "cosyvoice-v3.5-plus",
            "voice_id": "cosyvoice-v3.5-plus-test-voice",
            "region": "beijing",
            "workspace_id": "",
            "language_type": "auto",
            "sample_rate": 24000,
            "speech_rate": 1.0,
            "volume": 50,
            "pitch_rate": 1.0,
            "instruction": "温柔自然。",
            "timeout_seconds": 45,
            "max_audio_bytes": 32000000,
        },
    )
    assert cosy_response.status_code == 200
    cosy_updated = cast(dict[str, object], cosy_response.json())
    assert cosy_updated["provider_id"] == "aliyun_cosyvoice_realtime"
    assert cosy_updated["instruction"] == "温柔自然。"
    assert cosy_updated["api_key_configured"] is True
    assert "write-only-aliyun-secret" not in cosy_response.text

    unsupported_instruction = http.put(
        "/v1/tts/configurations/aliyun_cosyvoice_realtime",
        json={
            "enabled": False,
            "model": "cosyvoice-v2",
            "voice_id": "",
            "region": "beijing",
            "workspace_id": "",
            "language_type": "auto",
            "sample_rate": 24000,
            "speech_rate": 1.0,
            "volume": 50,
            "pitch_rate": 1.0,
            "instruction": "温柔自然。",
            "timeout_seconds": 45,
            "max_audio_bytes": 32000000,
        },
    )
    assert unsupported_instruction.status_code == 409
    assert "不支持情绪指令" in unsupported_instruction.text


def test_character_kernel_persists_and_reset_restores_initial_state(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    first_session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    first_session_id = str(first_session["session_id"])
    before = cast(
        dict[str, object],
        http.get(f"/v1/sessions/{first_session_id}/character-state").json(),
    )
    touched_response = http.post(
        f"/v1/sessions/{first_session_id}/character-interactions",
        json={"kind": "avatar_touch", "region": "body"},
    )
    assert touched_response.status_code == 200
    touched = cast(dict[str, object], touched_response.json())
    assert int(str(touched["revision"])) == int(str(before["revision"])) + 1
    _submit_and_wait(http, first_session_id, "谢谢你，我很喜欢和你聊天")
    after = cast(
        dict[str, object],
        http.get(f"/v1/sessions/{first_session_id}/character-state").json(),
    )
    after_relationship = cast(dict[str, object], after["relationship"])
    assert int(str(after["revision"])) == int(str(before["revision"])) + 2
    assert after_relationship["interaction_count"] == 1
    assert float(str(after_relationship["affinity"])) > float(
        str(cast(dict[str, object], before["relationship"])["affinity"])
    )

    second_session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    persisted = cast(
        dict[str, object],
        http.get(f"/v1/sessions/{second_session['session_id']}/character-state").json(),
    )
    assert persisted["revision"] == after["revision"]

    reset = http.post(
        f"/v1/sessions/{first_session_id}/reset",
        json={"confirm": True},
    )
    assert reset.status_code == 200
    restored = cast(
        dict[str, object],
        http.get(f"/v1/sessions/{first_session_id}/character-state").json(),
    )
    assert restored["revision"] == 0
    assert cast(dict[str, object], restored["relationship"])["interaction_count"] == 0


def test_committed_memory_builds_a_rebuildable_embedding_projection(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    _submit_and_wait(http, str(session["session_id"]), "请记住我喜欢蓝色")

    with sqlite3.connect(runtime_settings.database_path) as connection:
        row = connection.execute(
            "SELECT model_fingerprint, vector_json FROM memory_embeddings"
        ).fetchone()

    assert row is not None
    assert row[0] == "local_hash:local-hash-64-v1"
    assert len(cast(list[float], json.loads(str(row[1])))) == 64


def test_text_turn_streams_persists_and_serves_audio(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    created = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(created["session_id"])

    accepted = http.post(
        f"/v1/sessions/{session_id}/turns", json={"text": "你好，介绍一下你自己。"}
    )
    assert accepted.status_code == 202
    generation_id = str(cast(dict[str, object], accepted.json())["generation_id"])

    events: list[dict[str, object]] = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = http.get(f"/v1/sessions/{session_id}/events")
        events = cast(list[dict[str, object]], cast(dict[str, object], response.json())["items"])
        generation_events = [
            item for item in events if str(item.get("generation_id")) == generation_id
        ]
        generation_completed = any(
            item["event_type"] == "assistant.generation_completed" for item in generation_events
        )
        avatar_idle = any(
            item["event_type"] == "avatar.cue_emitted"
            and cast(dict[str, object], cast(dict[str, object], item["payload"])["cue"]).get("name")
            == "idle"
            for item in generation_events
        )
        if generation_completed and avatar_idle:
            break
        time.sleep(0.01)

    generation_events = [item for item in events if str(item.get("generation_id")) == generation_id]
    assert any(item["event_type"] == "assistant.text_delta" for item in generation_events)
    completed = next(
        item for item in generation_events if item["event_type"] == "assistant.generation_completed"
    )
    completed_text = str(cast(dict[str, object], completed["payload"])["text"])
    deltas = "".join(
        str(cast(dict[str, object], item["payload"])["text"])
        for item in generation_events
        if item["event_type"] == "assistant.text_delta"
    )
    assert deltas == completed_text
    assert "ChatWaifu NEXT" in completed_text
    assert "你好，介绍一下你自己。" in completed_text
    avatar_events = [
        item for item in generation_events if item["event_type"] == "avatar.cue_emitted"
    ]
    final_cue = cast(
        dict[str, object], cast(dict[str, object], avatar_events[-1]["payload"])["cue"]
    )
    assert final_cue["name"] == "idle"
    assert final_cue["priority"] == 90
    audio_event = next(
        item for item in generation_events if item["event_type"] == "assistant.audio_chunk_queued"
    )
    payload = cast(dict[str, object], audio_event["payload"])
    audio = http.get(str(payload["url"]))
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content.startswith(b"RIFF")

    messages = cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())
    roles = [item["role"] for item in cast(list[dict[str, object]], messages["items"])]
    assert roles == ["user", "assistant"]


def test_playback_ack_commits_only_a_fully_played_segment(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "请说一句用于播放确认的话。")
    events = cast(
        list[dict[str, object]],
        cast(
            dict[str, object],
            http.get(f"/v1/sessions/{session_id}/events?limit=500").json(),
        )["items"],
    )
    audio_event = next(
        event for event in events if event["event_type"] == "assistant.audio_chunk_queued"
    )
    generation_id = str(audio_event["generation_id"])
    payload = cast(dict[str, object], audio_event["payload"])
    stream_id = str(payload["stream_id"])
    segment_id = str(payload["segment_id"])
    duration_ms = int(str(payload["duration_ms"]))

    initial = cast(
        dict[str, object],
        http.get(f"/v1/sessions/{session_id}/generations/{generation_id}/playback").json(),
    )
    assert initial["spoken_text"] == ""
    assert cast(list[dict[str, object]], initial["segments"])[0]["state"] == "queued"

    started_id = str(uuid4())
    started = http.post(
        f"/v1/sessions/{session_id}/playback/ack",
        json=_playback_ack(
            command_id=started_id,
            session_id=session_id,
            generation_id=generation_id,
            stream_id=stream_id,
            segment_id=segment_id,
            phase="started",
            played_pts_ms=0,
        ),
    )
    assert started.status_code == 200
    assert cast(dict[str, object], started.json())["state"] == "playing"

    partial = http.post(
        f"/v1/sessions/{session_id}/playback/ack",
        json=_playback_ack(
            command_id=str(uuid4()),
            session_id=session_id,
            generation_id=generation_id,
            stream_id=stream_id,
            segment_id=segment_id,
            phase="progress",
            played_pts_ms=duration_ms // 2,
        ),
    )
    assert partial.status_code == 200
    assert cast(dict[str, object], partial.json())["spoken_text"] == ""

    stopped_id = str(uuid4())
    stopped_body = _playback_ack(
        command_id=stopped_id,
        session_id=session_id,
        generation_id=generation_id,
        stream_id=stream_id,
        segment_id=segment_id,
        phase="stopped",
        played_pts_ms=duration_ms,
        reason="ended",
    )
    stopped = http.post(
        f"/v1/sessions/{session_id}/playback/ack",
        json=stopped_body,
    )
    stopped_json = cast(dict[str, object], stopped.json())
    assert stopped.status_code == 200
    assert stopped_json["completed"] is True
    assert stopped_json["spoken_text"] == payload["text"]

    duplicate = http.post(
        f"/v1/sessions/{session_id}/playback/ack",
        json=stopped_body,
    )
    assert duplicate.status_code == 200
    assert cast(dict[str, object], duplicate.json())["duplicate"] is True

    final_events = cast(
        list[dict[str, object]],
        cast(
            dict[str, object],
            http.get(f"/v1/sessions/{session_id}/events?limit=500").json(),
        )["items"],
    )
    event_types = [str(event["event_type"]) for event in final_events]
    assert "assistant.playback_started" in event_types
    assert "assistant.playback_progress" in event_types
    assert event_types.count("assistant.playback_stopped") == 1
    assert event_types.count("assistant.spoken_text_committed") == 1


def test_interrupted_playback_does_not_commit_spoken_text(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "这句话会在播放中途停止。")
    events = cast(
        list[dict[str, object]],
        cast(
            dict[str, object],
            http.get(f"/v1/sessions/{session_id}/events?limit=500").json(),
        )["items"],
    )
    audio_event = next(
        event for event in events if event["event_type"] == "assistant.audio_chunk_queued"
    )
    payload = cast(dict[str, object], audio_event["payload"])
    response = http.post(
        f"/v1/sessions/{session_id}/playback/ack",
        json=_playback_ack(
            command_id=str(uuid4()),
            session_id=session_id,
            generation_id=str(audio_event["generation_id"]),
            stream_id=str(payload["stream_id"]),
            segment_id=str(payload["segment_id"]),
            phase="stopped",
            played_pts_ms=max(0, int(str(payload["duration_ms"])) // 2),
            reason="interrupted",
        ),
    )
    result = cast(dict[str, object], response.json())
    assert response.status_code == 200
    assert result["state"] == "stopped"
    assert result["completed"] is False
    assert result["spoken_text"] == ""


def test_interrupt_cancels_generation_and_rejects_late_output(
    runtime_settings: Settings,
) -> None:
    slow_llm = runtime_settings.llm.model_copy(update={"demo_chunk_delay_ms": 1000})
    settings = runtime_settings.model_copy(update={"llm": slow_llm})
    with TestClient(create_app(settings)) as client:
        http = cast(RuntimeHttpClient, client)
        session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
        session_id = str(session["session_id"])
        accepted = http.post(
            f"/v1/sessions/{session_id}/turns", json={"text": "这条回复要被打断。"}
        )
        assert accepted.status_code == 202
        interrupted = http.post(
            f"/v1/sessions/{session_id}/interrupt",
            json={"reason": "acceptance_test"},
        )
        assert interrupted.json()["interrupted"] is True

        events = cast(
            list[dict[str, object]],
            cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["items"],
        )
        event_types = [str(event["event_type"]) for event in events]
        assert "assistant.generation_cancelled" in event_types
        assert "conversation.interrupted" in event_types
        assert "assistant.generation_completed" not in event_types
        cancelled = next(
            event for event in events if event["event_type"] == "assistant.generation_cancelled"
        )
        assert cast(dict[str, object], cancelled["payload"])["reason"] == "acceptance_test"


def test_explicit_memory_survives_sessions_and_forget_tombstones(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    first_session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    _submit_and_wait(http, str(first_session["session_id"]), "请记住我喜欢蓝色")

    active = cast(dict[str, object], http.get("/v1/memory").json())
    active_items = cast(list[dict[str, object]], active["items"])
    assert [item["content"] for item in active_items] == ["我喜欢蓝色"]

    second_session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    second_session_id = str(second_session["session_id"])
    recalled_reply = _submit_and_wait(http, second_session_id, "你还记得我的喜好吗?")
    assert "记忆:" in recalled_reply
    assert "我喜欢蓝色" in recalled_reply

    _submit_and_wait(http, second_session_id, "请忘记我喜欢蓝色")
    assert cast(dict[str, object], http.get("/v1/memory").json())["count"] == 0
    history = cast(dict[str, object], http.get("/v1/memory?include_tombstoned=true").json())
    history_items = cast(list[dict[str, object]], history["items"])
    assert history_items[0]["state"] == "tombstoned"


def test_reset_clears_conversation_memory_events_and_audio(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "请记住我喜欢蓝色")
    _submit_and_wait(http, session_id, "请忘记我喜欢蓝色")
    _submit_and_wait(http, session_id, "请记住我喜欢紫色")

    assert (
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
        == 6
    )
    assert cast(dict[str, object], http.get("/v1/memory").json())["count"] == 1
    assert len(list((runtime_settings.data_dir / "audio").glob("*.wav"))) > 0
    rejected = http.post(f"/v1/sessions/{session_id}/reset", json={"confirm": False})
    assert rejected.status_code == 422

    reset = http.post(f"/v1/sessions/{session_id}/reset", json={"confirm": True})
    assert reset.status_code == 200
    result = cast(dict[str, object], reset.json())
    assert result["scope"] == {
        "character_id": "default",
        "user_scope": "local",
        "conversation": "current_session",
        "audio": "current_session",
        "memory": "current_character_user",
        "character_state": "current_character_user",
    }
    assert result["turns_deleted"] == 6
    assert int(str(result["events_deleted"])) > 0
    assert result["memories_deleted"] == 2
    assert int(str(result["audio_assets_deleted"])) > 0
    assert result["audio_assets_pending_cleanup"] == 0
    assert result["audio_cleanup_complete"] is True
    assert (
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
        == 0
    )
    remaining_events = cast(
        list[dict[str, object]],
        cast(
            dict[str, object],
            http.get(f"/v1/sessions/{session_id}/events").json(),
        )["items"],
    )
    assert [event["event_type"] for event in remaining_events] == ["session.data_reset"]
    reset_sequence = int(str(remaining_events[0]["sequence"]))
    assert (
        cast(dict[str, object], http.get("/v1/memory?include_tombstoned=true").json())["count"] == 0
    )
    assert list((runtime_settings.data_dir / "audio").glob("*.wav")) == []

    _submit_and_wait(http, session_id, "重新开始")
    fresh_events = cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["items"],
    )
    assert fresh_events[0]["event_type"] == "session.data_reset"
    assert int(str(fresh_events[0]["sequence"])) == reset_sequence
    assert int(str(fresh_events[-1]["sequence"])) > reset_sequence


def test_reset_is_scoped_across_sessions_characters_users_and_audio(
    runtime_settings: Settings, tmp_path: Path
) -> None:
    source_character = runtime_settings.characters_dir / "default"
    characters_dir = tmp_path / "characters"
    shutil.copytree(source_character, characters_dir / "default")
    shutil.copytree(source_character, characters_dir / "alternate")
    alternate_manifest = characters_dir / "alternate" / "character.yaml"
    alternate_manifest.write_text(
        alternate_manifest.read_text(encoding="utf-8").replace(
            "character_id: default", "character_id: alternate", 1
        ),
        encoding="utf-8",
    )
    settings = runtime_settings.model_copy(update={"characters_dir": characters_dir})

    with TestClient(create_app(settings)) as client:
        http = cast(RuntimeHttpClient, client)
        first = cast(
            dict[str, object],
            http.post("/v1/sessions", json={"character_id": "default"}).json(),
        )
        sibling = cast(
            dict[str, object],
            http.post("/v1/sessions", json={"character_id": "default"}).json(),
        )
        other_character = cast(
            dict[str, object],
            http.post("/v1/sessions", json={"character_id": "alternate"}).json(),
        )
        first_id = str(first["session_id"])
        sibling_id = str(sibling["session_id"])
        other_id = str(other_character["session_id"])

        _submit_and_wait(http, first_id, "请记住我喜欢紫色")
        _submit_and_wait(http, sibling_id, "同一个角色的另一个会话仍需保留")
        _submit_and_wait(http, other_id, "请记住我喜欢绿色")
        http.post(
            f"/v1/sessions/{first_id}/character-interactions",
            json={"kind": "avatar_touch", "region": "body"},
        )
        http.post(
            f"/v1/sessions/{other_id}/character-interactions",
            json={"kind": "avatar_touch", "region": "body"},
        )
        other_state_before = cast(
            dict[str, object],
            http.get(f"/v1/sessions/{other_id}/character-state").json(),
        )

        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            audio_by_session = {
                session_id: {
                    str(row["segment_id"])
                    for row in connection.execute(
                        "SELECT segment_id FROM playback_segments WHERE session_id = ?",
                        (session_id,),
                    )
                }
                for session_id in (first_id, sibling_id, other_id)
            }
            connection.execute(
                """
                INSERT INTO character_states
                SELECT character_id, 'other-user', valence, arousal, energy, attention,
                       embarrassment, tension, revision, updated_at
                FROM character_states
                WHERE character_id = 'default' AND user_scope = 'local'
                """
            )
            connection.execute(
                """
                INSERT INTO relationship_states
                SELECT character_id, 'other-user', familiarity, trust, affinity, comfort,
                       recent_tension, interaction_count, stage, preferred_address,
                       revision, updated_at
                FROM relationship_states
                WHERE character_id = 'default' AND user_scope = 'local'
                """
            )
            local_memory = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE namespace = 'character/default/user/local'
                LIMIT 1
                """
            ).fetchone()
            assert local_memory is not None
            other_user_memory_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, namespace, kind, subject_id, predicate, value_json,
                    text, normalized_text, search_terms, observed_at, valid_from,
                    valid_to, confidence, importance, sensitivity, state, supersedes,
                    pinned, created_at, updated_at, tombstoned_at
                ) VALUES (?, 'character/default/user/other-user', ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    other_user_memory_id,
                    local_memory["kind"],
                    local_memory["subject_id"],
                    local_memory["predicate"],
                    local_memory["value_json"],
                    local_memory["text"],
                    local_memory["normalized_text"],
                    local_memory["search_terms"],
                    local_memory["observed_at"],
                    local_memory["valid_from"],
                    local_memory["valid_to"],
                    local_memory["confidence"],
                    local_memory["importance"],
                    local_memory["sensitivity"],
                    local_memory["state"],
                    local_memory["pinned"],
                    local_memory["created_at"],
                    local_memory["updated_at"],
                    local_memory["tombstoned_at"],
                ),
            )
            other_user_source_event = connection.execute(
                """
                SELECT event_id,
                       json_extract(envelope_json, '$.turn_id') AS turn_id
                FROM events
                WHERE session_id = ? AND event_type = 'user.turn_committed'
                ORDER BY sequence LIMIT 1
                """,
                (first_id,),
            ).fetchone()
            assert other_user_source_event is not None
            connection.execute(
                """
                INSERT INTO memory_sources(
                    source_id, memory_id, source_event_id, session_id, turn_id,
                    source_kind, created_at
                ) VALUES (?, ?, ?, ?, ?, 'user_turn', ?)
                """,
                (
                    str(uuid4()),
                    other_user_memory_id,
                    other_user_source_event["event_id"],
                    first_id,
                    other_user_source_event["turn_id"],
                    datetime.now(UTC).isoformat(),
                ),
            )

        audio_root = settings.data_dir / "audio"
        assert all(audio_by_session.values())
        reset = http.post(f"/v1/sessions/{first_id}/reset", json={"confirm": True})
        assert reset.status_code == 200
        result = cast(dict[str, object], reset.json())
        assert cast(dict[str, object], result["scope"])["character_id"] == "default"
        assert result["audio_assets_deleted"] == len(audio_by_session[first_id])

        assert (
            cast(dict[str, object], http.get(f"/v1/sessions/{first_id}/messages").json())["count"]
            == 0
        )
        assert (
            cast(dict[str, object], http.get(f"/v1/sessions/{sibling_id}/messages").json())["count"]
            == 2
        )
        assert (
            cast(dict[str, object], http.get(f"/v1/sessions/{other_id}/messages").json())["count"]
            == 2
        )
        assert all(
            not (audio_root / f"{asset_id}.wav").exists() for asset_id in audio_by_session[first_id]
        )
        assert all(
            (audio_root / f"{asset_id}.wav").is_file()
            for asset_id in audio_by_session[sibling_id] | audio_by_session[other_id]
        )

        memories = cast(
            list[dict[str, object]],
            cast(dict[str, object], http.get("/v1/memory?include_tombstoned=true").json())["items"],
        )
        namespaces = {str(memory["namespace"]) for memory in memories}
        assert "character/default/user/local" not in namespaces
        assert "character/default/user/other-user" in namespaces
        assert "character/alternate/user/local" in namespaces
        other_state_after = cast(
            dict[str, object],
            http.get(f"/v1/sessions/{other_id}/character-state").json(),
        )
        assert other_state_after["revision"] == other_state_before["revision"]
        assert (
            cast(dict[str, object], other_state_after["relationship"])["interaction_count"]
            == cast(dict[str, object], other_state_before["relationship"])["interaction_count"]
        )
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*) FROM character_states
                WHERE character_id = 'default' AND user_scope = 'other-user'
                """
            ).fetchone() == (1,)
            assert connection.execute(
                """
                SELECT COUNT(*) FROM relationship_states
                WHERE character_id = 'default' AND user_scope = 'other-user'
                """
            ).fetchone() == (1,)

        repeated = cast(
            dict[str, object],
            http.post(f"/v1/sessions/{first_id}/reset", json={"confirm": True}).json(),
        )
        assert repeated["turns_deleted"] == 0
        assert repeated["events_deleted"] == 1
        assert repeated["memories_deleted"] == 0
        assert repeated["audio_assets_deleted"] == 0

        retained_events = cast(
            list[dict[str, object]],
            cast(dict[str, object], http.get(f"/v1/sessions/{first_id}/events").json())["items"],
        )
        assert [event["event_type"] for event in retained_events] == [
            "user.turn_committed",
            "session.data_reset",
        ]
        retained_sequence = max(int(str(event["sequence"])) for event in retained_events)
        _submit_and_wait(http, first_id, "重置之后继续聊天")
        continued_events = cast(
            list[dict[str, object]],
            cast(dict[str, object], http.get(f"/v1/sessions/{first_id}/events").json())["items"],
        )
        continued_sequences = [int(str(event["sequence"])) for event in continued_events]
        assert len(continued_sequences) == len(set(continued_sequences))
        assert max(continued_sequences) > retained_sequence


def test_reset_rolls_back_all_truth_and_audio_when_one_delete_fails(
    client: TestClient, runtime_settings: Settings
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "请记住我喜欢蓝色")
    http.post(
        f"/v1/sessions/{session_id}/character-interactions",
        json={"kind": "avatar_touch", "region": "body"},
    )
    messages_before = int(
        str(
            cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
        )
    )
    memories_before = int(str(cast(dict[str, object], http.get("/v1/memory").json())["count"]))
    audio_before = tuple((runtime_settings.data_dir / "audio").glob("*.wav"))
    assert messages_before > 0
    assert memories_before > 0
    assert audio_before

    with sqlite3.connect(runtime_settings.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER inject_experience_reset_failure
            BEFORE DELETE ON character_states
            WHEN old.character_id = 'default' AND old.user_scope = 'local'
            BEGIN
                SELECT RAISE(ABORT, 'injected_experience_reset_failure');
            END
            """
        )

    with pytest.raises(Exception, match="injected_experience_reset_failure"):
        http.post(f"/v1/sessions/{session_id}/reset", json={"confirm": True})

    assert (
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
        == messages_before
    )
    assert cast(dict[str, object], http.get("/v1/memory").json())["count"] == memories_before
    assert all(path.is_file() for path in audio_before)
    assert not list((runtime_settings.data_dir / "audio" / "reset-quarantine").glob("*"))

    with sqlite3.connect(runtime_settings.database_path) as connection:
        connection.execute("DROP TRIGGER inject_experience_reset_failure")


def test_reset_event_is_live_and_keeps_the_session_cursor_monotonic(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    _submit_and_wait(http, session_id, "跨窗口重置前的消息")
    before = cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["items"],
    )
    cursor = max(int(str(event["sequence"])) for event in before)

    with client.websocket_connect(
        f"/v1/events?session_id={session_id}&after_sequence={cursor}"
    ) as websocket:
        assert websocket.receive_json()["event_type"] == "system.runtime_started"
        response = http.post(f"/v1/sessions/{session_id}/reset", json={"confirm": True})
        assert response.status_code == 200
        reset_event = cast(dict[str, object], websocket.receive_json())

    assert reset_event["event_type"] == "session.data_reset"
    assert int(str(reset_event["sequence"])) == cursor + 1
    recovery = cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/recovery").json())
    assert recovery["messages"] == []
    assert recovery["after_sequence"] == cursor + 1
    assert recovery["last_sequence"] == cursor + 1


def test_startup_reconciles_crash_left_audio_quarantine_from_database_truth(
    runtime_settings: Settings,
) -> None:
    with TestClient(create_app(runtime_settings)) as first_client:
        http = cast(RuntimeHttpClient, first_client)
        session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
        session_id = str(session["session_id"])
        _submit_and_wait(http, session_id, "生成一段用于崩溃恢复的语音")
        with sqlite3.connect(runtime_settings.database_path) as connection:
            row = connection.execute(
                "SELECT segment_id FROM playback_segments WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        assert row is not None
        referenced_asset_id = str(row[0])

    audio_root = runtime_settings.data_dir / "audio"
    quarantine_batch = audio_root / "reset-quarantine" / str(uuid4())
    quarantine_batch.mkdir(parents=True)
    referenced_path = audio_root / f"{referenced_asset_id}.wav"
    referenced_staged = quarantine_batch / referenced_path.name
    referenced_path.replace(referenced_staged)
    orphan_asset_id = str(uuid4())
    orphan_staged = quarantine_batch / f"{orphan_asset_id}.wav"
    orphan_staged.write_bytes(b"orphaned reset audio")

    with TestClient(create_app(runtime_settings)) as restarted:
        assert restarted.get("/v1/runtime/health").status_code == 200

    assert referenced_path.is_file()
    assert not orphan_staged.exists()
    assert not (audio_root / "reset-quarantine").exists()


def test_reset_cancels_an_active_generation(runtime_settings: Settings) -> None:
    slow_llm = runtime_settings.llm.model_copy(update={"demo_chunk_delay_ms": 1000})
    settings = runtime_settings.model_copy(update={"llm": slow_llm})
    with TestClient(create_app(settings)) as client:
        http = cast(RuntimeHttpClient, client)
        session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
        session_id = str(session["session_id"])
        accepted = http.post(
            f"/v1/sessions/{session_id}/turns", json={"text": "重置这条进行中的回复"}
        )
        assert accepted.status_code == 202

        reset = http.post(f"/v1/sessions/{session_id}/reset", json={"confirm": True})
        assert reset.status_code == 200
        assert cast(dict[str, object], reset.json())["turns_deleted"] == 1
        assert (
            cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
            == 0
        )
        reset_events = cast(
            list[dict[str, object]],
            cast(
                dict[str, object],
                http.get(f"/v1/sessions/{session_id}/events").json(),
            )["items"],
        )
        assert [event["event_type"] for event in reset_events] == ["session.data_reset"]


def test_character_and_manifest_driven_runtime_status_skill(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    characters = cast(dict[str, object], http.get("/v1/characters").json())
    profile = cast(list[dict[str, object]], characters["items"])[0]
    assert profile["display_name"] == "绫地宁宁"
    assert cast(dict[str, object], profile["voice_profile"])["voice_id"] == ("ayachi_nene_local")
    assert "非官方" in str(profile["content_notice"])
    assert "system_prompt" not in profile

    skills = cast(dict[str, object], http.get("/v1/skills").json())
    skill = cast(list[dict[str, object]], skills["items"])[0]
    assert skill["skill_id"] == "runtime.status"
    capability = cast(list[dict[str, object]], skill["capabilities"])[0]
    assert capability["side_effect"] == "read"
    assert capability["confirmation_required"] is False

    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    result = http.post(f"/v1/sessions/{session['session_id']}/skills/runtime.status", json={})
    assert result.status_code == 200
    result_json = cast(dict[str, object], result.json())
    assert result_json["status"] == "succeeded"
    data = cast(dict[str, object], result_json["data"])
    assert data["llm_provider"] == "demo"
    assert data["tts_provider"] == "fake"
    assert data["stt_provider"] == "disabled"
    assert data["transport"] == "pipecat_smallwebrtc"


def _submit_and_wait(http: RuntimeHttpClient, session_id: str, text: str) -> str:
    accepted = http.post(f"/v1/sessions/{session_id}/turns", json={"text": text})
    assert accepted.status_code == 202
    generation_id = str(cast(dict[str, object], accepted.json())["generation_id"])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        events_response = http.get(f"/v1/sessions/{session_id}/events?limit=500")
        events = cast(
            list[dict[str, object]], cast(dict[str, object], events_response.json())["items"]
        )
        for event in events:
            if (
                event["event_type"] == "assistant.generation_completed"
                and str(event.get("generation_id")) == generation_id
            ):
                return str(cast(dict[str, object], event["payload"])["text"])
        time.sleep(0.01)
    raise AssertionError(f"generation {generation_id} did not complete")


def _playback_ack(
    *,
    command_id: str,
    session_id: str,
    generation_id: str,
    stream_id: str,
    segment_id: str,
    phase: str,
    played_pts_ms: int,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "command_id": command_id,
        "schema_version": "1.0",
        "command_type": "cmd.playback.ack",
        "issued_at": datetime.now(UTC).isoformat(),
        "issuer": "test.browser",
        "session_id": session_id,
        "generation_id": generation_id,
        "payload": {
            "phase": phase,
            "stream_id": stream_id,
            "segment_id": segment_id,
            "played_pts_ms": played_pts_ms,
            "buffered_ms": 0,
            "client_clock_ms": 1000 + played_pts_ms,
            "transport": "audio_element",
            "reason": reason,
        },
    }
