"""Runtime HTTP and WebSocket acceptance tests."""

from typing import Protocol, cast

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
