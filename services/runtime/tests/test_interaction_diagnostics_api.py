"""Owner interaction trace API reads persisted metadata without prompt or text."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from services.runtime.tests.runtime_wait import wait_for_generation_terminal


def _session(client: TestClient) -> str:
    response = client.post("/v1/sessions", json={"character_id": "default"})
    assert response.status_code == 201
    return str(response.json()["session_id"])


def test_generation_trace_is_read_only_and_bounded_to_its_session(client: TestClient) -> None:
    session_id = _session(client)
    other_session = _session(client)
    accepted = client.post(f"/v1/sessions/{session_id}/turns", json={"text": "秘密测试正文"})
    assert accepted.status_code == 202
    generation_id = str(accepted.json()["generation_id"])
    wait_for_generation_terminal(client, UUID(generation_id))

    page_response = client.get(f"/v1/sessions/{session_id}/interactions")
    assert page_response.status_code == 200
    page = page_response.json()
    assert page["schema_version"] == "1.0"
    assert page["items"][0]["interaction_id"] == generation_id
    assert page["items"][0]["generation_state"] == "completed"
    assert page["has_more"] is False

    detail_response = client.get(f"/v1/sessions/{session_id}/interactions/{generation_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["summary"]["generation_id"] == generation_id
    assert detail["prompt_identity"]["schema_version"] == "1.0"
    assert detail["prompt_budget"]["budget"] > 0
    assert detail["selected_memory_ids"] == []
    assert "秘密测试正文" not in detail_response.text
    assert "system_prompt" not in detail_response.text
    assert "api_key" not in detail_response.text
    assert (
        client.get(f"/v1/sessions/{other_session}/interactions/{generation_id}").status_code == 404
    )
    assert client.get(f"/v1/sessions/{session_id}/interactions/{uuid4()}").status_code == 404
    assert client.get(f"/v1/sessions/{uuid4()}/interactions").status_code == 404


def test_invalid_interaction_cursors_and_bounds_are_rejected(client: TestClient) -> None:
    session_id = _session(client)
    assert client.get(f"/v1/sessions/{session_id}/interactions?cursor=bad").status_code == 422
    assert client.get(f"/v1/sessions/{session_id}/interactions?limit=51").status_code == 422
    assert (
        client.get(
            f"/v1/sessions/{session_id}/interactions/{uuid4()}?after_sequence=-1"
        ).status_code
        == 422
    )
