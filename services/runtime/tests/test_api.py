"""Runtime HTTP and WebSocket acceptance tests."""

import time
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import uuid4

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
    assert event["event_type"] == "system.runtime_started"


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
    assert secret_file.stat().st_mode & 0o777 == 0o600


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
        if any(item["event_type"] == "assistant.generation_completed" for item in events):
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
    assert result["turns_deleted"] == 6
    assert int(str(result["events_deleted"])) > 0
    assert result["memories_deleted"] == 2
    assert int(str(result["audio_assets_deleted"])) > 0
    assert (
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/messages").json())["count"]
        == 0
    )
    assert (
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["count"] == 0
    )
    assert (
        cast(dict[str, object], http.get("/v1/memory?include_tombstoned=true").json())["count"] == 0
    )
    assert list((runtime_settings.data_dir / "audio").glob("*.wav")) == []

    _submit_and_wait(http, session_id, "重新开始")
    fresh_events = cast(
        list[dict[str, object]],
        cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["items"],
    )
    assert fresh_events[0]["sequence"] == 1


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
        assert (
            cast(dict[str, object], http.get(f"/v1/sessions/{session_id}/events").json())["count"]
            == 0
        )


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
