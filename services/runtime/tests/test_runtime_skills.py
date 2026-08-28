"""Runtime Skill permission, MCP plugin, timeout, cancellation, and lifecycle tests."""

import sys
import time
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import McpConnectionSecretStore
from fastapi.testclient import TestClient
from httpx2 import Response


class RuntimeHttpClient(Protocol):
    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...

    def put(self, url: str, *, json: object) -> Response: ...

    def delete(self, url: str) -> Response: ...


def test_example_mcp_plugin_install_execute_confirm_disable_and_uninstall(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])

    installed = http.post("/v1/plugins/install-example", json={"example_id": "local-echo"})
    assert installed.status_code == 201
    assert cast(dict[str, object], installed.json())["plugin_id"] == "local.echo"

    skills = cast(dict[str, object], http.get("/v1/skills").json())
    plugin_skill = next(
        item
        for item in cast(list[dict[str, object]], skills["items"])
        if item["skill_id"] == "local.echo"
    )
    assert plugin_skill["source"] == "plugin"
    assert plugin_skill["enabled"] is True

    echo = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={
            "skill_id": "local.echo",
            "capability": "echo",
            "arguments": {"text": "hello MCP"},
        },
    )
    assert echo.status_code == 202
    echoed = _wait_for_run(http, str(cast(dict[str, object], echo.json())["skill_run_id"]))
    assert echoed["state"] == "succeeded", echoed
    assert (
        cast(dict[str, object], cast(dict[str, object], echoed["result"])["data"])["echo"]
        == "hello MCP"
    )

    write = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={
            "skill_id": "local.echo",
            "capability": "append_note",
            "arguments": {"text": "requires permission"},
        },
    )
    waiting = cast(dict[str, object], write.json())
    assert waiting["state"] == "waiting_for_confirmation"
    request_id = str(waiting["confirmation_request_id"])
    forbidden_persistent = http.post(
        f"/v1/skill-confirmations/{request_id}", json={"decision": "allow_always"}
    )
    assert forbidden_persistent.status_code == 409
    allowed = http.post(f"/v1/skill-confirmations/{request_id}", json={"decision": "allow_session"})
    assert allowed.status_code == 200
    written = _wait_for_run(http, str(waiting["skill_run_id"]))
    assert written["state"] == "succeeded"

    again = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={
            "skill_id": "local.echo",
            "capability": "append_note",
            "arguments": {"text": "confirmation stays separate"},
        },
    )
    again_body = cast(dict[str, object], again.json())
    assert again_body["state"] == "waiting_for_confirmation"
    denied = http.post(
        f"/v1/skill-confirmations/{again_body['confirmation_request_id']}",
        json={"decision": "deny"},
    )
    assert cast(dict[str, object], denied.json())["state"] == "failed"

    disabled = http.put("/v1/plugins/local.echo/enabled", json={"enabled": False})
    assert cast(dict[str, object], disabled.json())["enabled"] is False
    unavailable = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={"skill_id": "local.echo", "capability": "echo", "arguments": {"text": "x"}},
    )
    assert unavailable.status_code == 409
    assert http.put("/v1/plugins/local.echo/enabled", json={"enabled": True}).status_code == 200

    removed = http.delete("/v1/plugins/local.echo")
    assert removed.status_code == 200
    removed_body = cast(dict[str, object], removed.json())
    assert removed_body["removed"] is True
    assert "plugin-trash" in str(removed_body["recoverable_from"])


def test_mcp_plugin_timeout_and_cancellation_are_terminal(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    session_id = str(session["session_id"])
    assert (
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).status_code
        == 201
    )

    timed = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={"skill_id": "local.echo", "capability": "wait", "arguments": {"seconds": 5}},
    )
    timed_run = _wait_for_run(
        http, str(cast(dict[str, object], timed.json())["skill_run_id"]), timeout=4
    )
    assert timed_run["state"] == "failed"
    assert cast(dict[str, object], timed_run["error"])["code"] == "skill_timeout"

    cancellable = http.post(
        f"/v1/sessions/{session_id}/skill-runs",
        json={"skill_id": "local.echo", "capability": "wait", "arguments": {"seconds": 1.5}},
    )
    run_id = str(cast(dict[str, object], cancellable.json())["skill_run_id"])
    cancelled = http.post(f"/v1/skill-runs/{run_id}/cancel", json={})
    assert cancelled.status_code == 200
    assert cast(dict[str, object], cancelled.json())["state"] == "cancelled"


def test_skill_input_schema_rejects_invalid_arguments(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    assert (
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).status_code
        == 201
    )
    invalid = http.post(
        f"/v1/sessions/{session['session_id']}/skill-runs",
        json={"skill_id": "local.echo", "capability": "echo", "arguments": {}},
    )
    assert invalid.status_code == 409
    assert "schema validation" in str(cast(dict[str, object], invalid.json())["detail"])


def test_persisted_stdio_mcp_host_discovers_and_routes_capabilities(
    client: TestClient,
    runtime_settings: Settings,
) -> None:
    http = cast(RuntimeHttpClient, client)
    server = (
        Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo" / "server.py"
    )
    created = http.post(
        "/v1/mcp/connections",
        json={
            "name": "Host fixture",
            "transport": "stdio",
            "command": [sys.executable, str(server)],
            "trust_level": "trusted",
            "sandbox_mode": "disabled",
            "network_policy": "allow",
            "bearer_token": "write-only-test-token",
        },
    )
    assert created.status_code == 201, created.text
    body = cast(dict[str, object], created.json())
    connection_id = str(body["connection_id"])
    assert body["bearer_token_configured"] is True
    assert "write-only-test-token" not in created.text

    tested = http.post(f"/v1/mcp/connections/{connection_id}/test", json={})
    assert tested.status_code == 200, tested.text
    tested_body = cast(dict[str, object], tested.json())
    assert tested_body["status"] == "ready"
    capabilities = cast(dict[str, object], tested_body["capabilities"])
    assert len(cast(list[object], capabilities["tools"])) == 3
    assert len(cast(list[object], capabilities["resources"])) == 1
    assert len(cast(list[object], capabilities["resource_templates"])) == 1
    assert len(cast(list[object], capabilities["prompts"])) == 1

    resource = http.post(
        f"/v1/mcp/connections/{connection_id}/resources/read",
        json={"uri": "chatwaifu://example/readme"},
    )
    assert resource.status_code == 200, resource.text
    assert "example resource" in resource.text
    prompt = http.post(
        f"/v1/mcp/connections/{connection_id}/prompts/get",
        json={"name": "greet", "arguments": {"name": "Nene"}},
    )
    assert prompt.status_code == 200, prompt.text
    assert "Hello Nene" in prompt.text

    session_id = str(
        cast(dict[str, object], http.post("/v1/sessions", json={}).json())["session_id"]
    )
    called = http.post(
        f"/v1/sessions/{session_id}/mcp/connections/{connection_id}/tools/call",
        json={"name": "local_echo", "arguments": {"text": "permissioned host"}},
    )
    called_body = cast(dict[str, object], called.json())
    assert called_body["state"] == "waiting_for_confirmation"
    allowed = http.post(
        f"/v1/skill-confirmations/{called_body['confirmation_request_id']}",
        json={"decision": "allow_once"},
    )
    assert allowed.status_code == 200
    completed = _wait_for_run(http, str(called_body["skill_run_id"]))
    assert completed["state"] == "succeeded", completed

    listed = http.get("/v1/mcp/connections")
    assert "write-only-test-token" not in listed.text
    secret_path = runtime_settings.data_dir / "mcp-secrets.json"
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert http.delete(f"/v1/mcp/connections/{connection_id}").status_code == 200


def test_mcp_host_rejects_metadata_address_even_when_remote_is_allowed(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    created = http.post(
        "/v1/mcp/connections",
        json={
            "name": "Metadata endpoint",
            "transport": "streamable_http",
            "url": "http://169.254.169.254/mcp",
            "allow_remote": True,
        },
    )
    assert created.status_code == 201
    connection_id = str(cast(dict[str, object], created.json())["connection_id"])
    tested = http.post(f"/v1/mcp/connections/{connection_id}/test", json={})
    assert tested.status_code == 502
    assert "link-local" in tested.text


def test_corrupt_mcp_secret_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "mcp-secrets.json"
    path.write_text("{broken", encoding="utf-8")
    store = McpConnectionSecretStore(path)
    try:
        store.set(UUID("00000000-0000-0000-0000-000000000001"), "replacement")
    except SkillExecutionError as error:
        assert error.structured.code == "mcp_secret_store_corrupt"
    else:
        raise AssertionError("corrupt secret store must fail closed")
    assert path.read_text(encoding="utf-8") == "{broken"


def _wait_for_run(http: RuntimeHttpClient, run_id: str, *, timeout: float = 2) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    latest: dict[str, object] = {}
    while time.monotonic() < deadline:
        latest = cast(dict[str, object], http.get(f"/v1/skill-runs/{run_id}").json())
        if latest.get("state") in {"succeeded", "failed", "cancelled", "expired"}:
            return latest
        time.sleep(0.01)
    raise AssertionError(f"skill run {run_id} did not finish: {latest}")
