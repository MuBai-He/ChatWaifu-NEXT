"""OS sandbox policy tests for local MCP processes."""

import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.sandbox import (
    RuntimeSandboxLauncher,
    SandboxPlanner,
    SandboxPolicyError,
)
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    NoopSandboxLauncher,
    PreparedStdioCommand,
)


def test_untrusted_process_cannot_disable_sandbox(tmp_path: Path) -> None:
    planner = SandboxPlanner(platform_name="win32", which=lambda _: None)

    with pytest.raises(SandboxPolicyError, match="cannot disable"):
        planner.prepare(["server"], working_dir=tmp_path, mode="disabled", trust_level="untrusted")


def test_disabled_sandbox_cannot_claim_restricted_network_policy(tmp_path: Path) -> None:
    planner = SandboxPlanner(platform_name="win32", which=lambda _: None)

    with pytest.raises(SandboxPolicyError, match="cannot enforce a restricted network policy"):
        planner.prepare(
            ["server"],
            working_dir=tmp_path,
            mode="disabled",
            trust_level="trusted",
            network_policy="deny",
        )


def test_required_sandbox_fails_closed_when_backend_is_missing(tmp_path: Path) -> None:
    planner = SandboxPlanner(platform_name="win32", which=lambda _: None)

    with pytest.raises(SandboxPolicyError, match="No enforcing sandbox"):
        planner.prepare(["server"], working_dir=tmp_path, mode="required", trust_level="untrusted")


def test_preferred_sandbox_reports_soft_fallback_for_trusted_process(tmp_path: Path) -> None:
    planner = SandboxPlanner(platform_name="win32", which=lambda _: None)

    plan = planner.prepare(
        ["server"],
        working_dir=tmp_path,
        mode="preferred",
        trust_level="trusted",
        network_policy="allow",
    )

    assert plan.backend == "none"
    assert plan.enforced is False
    assert "soft isolation" in plan.diagnostic


def test_preferred_sandbox_fails_closed_for_network_deny_without_backend(
    tmp_path: Path,
) -> None:
    planner = SandboxPlanner(platform_name="win32", which=lambda _: None)

    with pytest.raises(SandboxPolicyError, match="requested network policy"):
        planner.prepare(
            ["server"],
            working_dir=tmp_path,
            mode="preferred",
            trust_level="trusted",
            network_policy="deny",
        )


def test_macos_plan_uses_seatbelt_and_denies_network(tmp_path: Path) -> None:
    planner = SandboxPlanner(
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )

    plan = planner.prepare(
        ["python", "server.py"],
        working_dir=tmp_path,
        mode="required",
        trust_level="untrusted",
        network_policy="deny",
    )

    assert plan.backend == "macos_seatbelt"
    assert plan.enforced is True
    assert plan.command[:2] == ("/usr/bin/sandbox-exec", "-p")
    assert "(deny network*)" in plan.command[2]
    assert str(tmp_path) in plan.command[2]


def test_linux_plan_uses_namespaces_without_binding_home(tmp_path: Path) -> None:
    planner = SandboxPlanner(
        platform_name="linux",
        which=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    plan = planner.prepare(
        ["python3", "server.py"],
        working_dir=tmp_path,
        mode="required",
        trust_level="untrusted",
        network_policy="deny",
    )

    assert plan.backend == "linux_bubblewrap"
    assert "--unshare-all" in plan.command
    assert "--share-net" not in plan.command
    assert str(Path.home()) not in plan.command


def test_oci_backend_is_available_on_windows_when_image_is_configured(tmp_path: Path) -> None:
    planner = SandboxPlanner(
        platform_name="win32",
        which=lambda name: "C:/docker.exe" if name == "docker" else None,
    )

    plan = planner.prepare(
        ["python", "server.py"],
        working_dir=tmp_path,
        mode="required",
        trust_level="untrusted",
        container_image="python:3.12-alpine",
    )

    assert plan.backend == "oci"
    assert plan.enforced is True
    assert "none" in plan.command
    assert "no-new-privileges" in plan.command
    assert plan.resource_limits_enforced == ("process_count", "memory", "cpu")


def test_oci_maps_read_only_package_and_writable_data_paths(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    data_root = tmp_path / "data"
    package_root.mkdir()
    data_root.mkdir()
    server = package_root / "server.py"
    server.write_text("print('fixture')\n", encoding="utf-8")
    planner = SandboxPlanner(
        platform_name="win32",
        which=lambda name: "C:/docker.exe" if name == "docker" else None,
    )

    plan = planner.prepare(
        [sys.executable, str(server)],
        working_dir=data_root,
        mode="required",
        trust_level="untrusted",
        network_policy="deny",
        container_image="python:3.12-alpine",
        read_only_roots=(package_root,),
        environment={
            "CHATWAIFU_PLUGIN_PACKAGE_DIR": str(package_root),
            "CHATWAIFU_PLUGIN_DATA_DIR": str(data_root),
            "CHATWAIFU_MCP_SUBJECT_ID": "fixture",
        },
    )

    assert "/package-0/server.py" in plan.command
    assert f"type=bind,src={package_root},dst=/package-0,readonly" in plan.command
    assert f"type=bind,src={data_root},dst=/plugin" in plan.command
    assert "CHATWAIFU_PLUGIN_PACKAGE_DIR=/package-0" in plan.command
    assert "CHATWAIFU_PLUGIN_DATA_DIR=/plugin" in plan.command


def test_runtime_launcher_normalizes_required_backend_failure(tmp_path: Path) -> None:
    launcher = RuntimeSandboxLauncher(SandboxPlanner(platform_name="win32", which=lambda _: None))
    command = PreparedStdioCommand(command="server", args=(), cwd=tmp_path, env={})

    with pytest.raises(SkillExecutionError) as raised:
        launcher.prepare(
            command,
            trust_level="untrusted",
            sandbox_mode="required",
            network_policy="deny",
        )

    assert raised.value.structured.code == "sandbox_unavailable"


def test_runtime_launcher_rejects_unenforceable_loopback_policy(tmp_path: Path) -> None:
    launcher = RuntimeSandboxLauncher(SandboxPlanner(platform_name="win32", which=lambda _: None))
    command = PreparedStdioCommand(command="server", args=(), cwd=tmp_path, env={})

    with pytest.raises(SkillExecutionError) as raised:
        launcher.prepare(
            command,
            trust_level="trusted",
            sandbox_mode="disabled",
            network_policy="loopback",
        )

    assert raised.value.structured.code == "sandbox_network_policy_unavailable"


def test_runtime_launcher_reports_actual_backend(tmp_path: Path) -> None:
    launcher = RuntimeSandboxLauncher(
        SandboxPlanner(
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
        )
    )
    command = PreparedStdioCommand(command="python", args=("server.py",), cwd=tmp_path, env={})

    prepared = launcher.prepare(
        command,
        trust_level="untrusted",
        sandbox_mode="required",
        network_policy="deny",
    )

    assert prepared.command == "/usr/bin/sandbox-exec"
    assert prepared.sandbox_backend == "macos_seatbelt"


@pytest.mark.parametrize("sandbox_mode", ["disabled", "preferred", "required"])
def test_noop_launcher_always_rejects_loopback_policy(tmp_path: Path, sandbox_mode: str) -> None:
    launcher = NoopSandboxLauncher()
    command = PreparedStdioCommand(command="server", args=(), cwd=tmp_path, env={})

    with pytest.raises(SkillExecutionError) as raised:
        launcher.prepare(
            command,
            trust_level="trusted",
            sandbox_mode=sandbox_mode,
            network_policy="loopback",
        )

    assert raised.value.structured.code == "sandbox_network_policy_unavailable"


def test_noop_launcher_rejects_disabled_restricted_network(tmp_path: Path) -> None:
    launcher = NoopSandboxLauncher()
    command = PreparedStdioCommand(command="server", args=(), cwd=tmp_path, env={})

    with pytest.raises(SkillExecutionError) as raised:
        launcher.prepare(
            command,
            trust_level="trusted",
            sandbox_mode="disabled",
            network_policy="deny",
        )

    assert raised.value.structured.code == "sandbox_network_policy_unavailable"


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="macOS Seatbelt is unavailable",
)
async def test_macos_seatbelt_allows_official_mcp_stdio_handshake(tmp_path: Path) -> None:
    source_fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    fixture = tmp_path / source_fixture.name
    shutil.copy2(source_fixture, fixture)
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="Seatbelt stdio fixture",
        transport="stdio",
        command=[sys.executable, str(fixture)],
        trust_level="untrusted",
        sandbox_mode="required",
        network_policy="deny",
        timeout_seconds=5,
    )
    transport = McpClientTransport(RuntimeSandboxLauncher())

    async with transport.connection_session(
        config,
        bearer_token=None,
        working_root=tmp_path,
    ) as (session, initialized):
        assert initialized.server_info.name == "chatwaifu-seatbelt-fixture"
        assert initialized.server_info.version == "1.0.0"
        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == ["ping"]
        result = await session.call_tool("ping", {"text": "sandbox handshake"})
        assert result.is_error is False
        assert cast(dict[str, Any], result.structured_content) == {"reply": "sandbox handshake"}


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="macOS Seatbelt is unavailable",
)
def test_macos_seatbelt_actually_denies_read_outside_plugin_root(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    outside_secret = tmp_path / "outside-secret.txt"
    outside_secret.write_text("must-not-leak", encoding="utf-8")
    planner = SandboxPlanner()
    plan = planner.prepare(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; print(Path({str(outside_secret)!r}).read_text())",
        ],
        working_dir=plugin_root,
        mode="required",
        trust_level="untrusted",
    )

    completed = subprocess.run(
        plan.command,
        cwd=plugin_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode != 0
    assert "must-not-leak" not in completed.stdout


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="macOS Seatbelt is unavailable",
)
def test_macos_seatbelt_actually_denies_loopback_network(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    planner = SandboxPlanner()
    plan = planner.prepare(
        [
            sys.executable,
            "-c",
            f"import socket; socket.create_connection(('127.0.0.1', {port})); print('connected')",
        ],
        working_dir=tmp_path,
        mode="required",
        trust_level="untrusted",
        network_policy="deny",
    )
    try:
        completed = subprocess.run(
            plan.command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    finally:
        listener.close()

    assert completed.returncode != 0
    assert "connected" not in completed.stdout
