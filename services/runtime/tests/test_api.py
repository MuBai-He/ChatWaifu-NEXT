"""Runtime HTTP and WebSocket acceptance tests."""

import time
from typing import Protocol, cast

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient
from httpx2 import Response


class RuntimeHttpClient(Protocol):
    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...

    def delete(self, url: str) -> Response: ...


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
    assert profile["display_name"] == "小雾"
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
