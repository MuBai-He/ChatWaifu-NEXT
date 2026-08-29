"""Platform sandbox planning for untrusted local MCP server processes.

The planner never labels cleaned environment variables or process-group cleanup as a
sandbox.  An ``enforced`` plan always delegates process creation to an operating-system
isolation primitive (Seatbelt, bubblewrap, or an OCI runtime).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.transports import PreparedStdioCommand

SandboxMode = Literal["required", "preferred", "disabled"]
TrustLevel = Literal["trusted", "untrusted"]
NetworkPolicy = Literal["deny", "allow"]
SandboxBackend = Literal["macos_seatbelt", "linux_bubblewrap", "oci", "none"]


class SandboxPolicyError(RuntimeError):
    """A local process cannot satisfy its declared isolation policy."""


@dataclass(frozen=True, slots=True)
class SandboxPlan:
    command: tuple[str, ...]
    backend: SandboxBackend
    enforced: bool
    network_policy: NetworkPolicy
    diagnostic: str
    resource_limits_enforced: tuple[str, ...] = ()


class SandboxPlanner:
    """Build a fail-closed command wrapper for one local MCP invocation."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._platform = platform_name or sys.platform
        self._which = which

    def prepare(
        self,
        command: Sequence[str],
        *,
        working_dir: Path,
        mode: SandboxMode,
        trust_level: TrustLevel,
        network_policy: NetworkPolicy = "deny",
        container_image: str | None = None,
        read_only_roots: Sequence[Path] = (),
        environment: Mapping[str, str] | None = None,
    ) -> SandboxPlan:
        normalized = _validate_command(command)
        root = working_dir.expanduser().resolve()
        if not root.is_dir():
            raise SandboxPolicyError(f"Sandbox working directory is missing: {root}")
        if mode == "disabled":
            if trust_level != "trusted":
                raise SandboxPolicyError("Untrusted local MCP servers cannot disable the sandbox")
            if network_policy != "allow":
                raise SandboxPolicyError(
                    "A disabled sandbox cannot enforce a restricted network policy"
                )
            return SandboxPlan(
                command=normalized,
                backend="none",
                enforced=False,
                network_policy=network_policy,
                diagnostic="Sandbox explicitly disabled for a trusted local server",
                resource_limits_enforced=(),
            )

        plan = self._native_plan(
            normalized,
            root=root,
            network_policy=network_policy,
            container_image=container_image,
            read_only_roots=tuple(path.expanduser().resolve() for path in read_only_roots),
            environment=environment or {},
        )
        if plan is not None:
            return plan
        if mode == "required":
            raise SandboxPolicyError(
                "No enforcing sandbox backend is available; install bubblewrap on Linux, "
                "use macOS Seatbelt, or configure an OCI image/runtime"
            )
        if network_policy != "allow":
            raise SandboxPolicyError(
                "No enforcing sandbox backend is available for the requested network policy"
            )
        return SandboxPlan(
            command=normalized,
            backend="none",
            enforced=False,
            network_policy=network_policy,
            diagnostic="No OS sandbox backend is available; trusted process uses soft isolation",
            resource_limits_enforced=(),
        )

    def capability(self, *, container_image: str | None = None) -> dict[str, object]:
        backend = self._available_backend(container_image=container_image)
        return {
            "available": backend is not None,
            "backend": backend or "none",
            "platform": self._platform,
            "network_deny_supported": backend is not None,
        }

    def _native_plan(
        self,
        command: tuple[str, ...],
        *,
        root: Path,
        network_policy: NetworkPolicy,
        container_image: str | None,
        read_only_roots: tuple[Path, ...],
        environment: Mapping[str, str],
    ) -> SandboxPlan | None:
        if self._platform == "darwin":
            executable = self._which("sandbox-exec")
            if executable:
                profile = _seatbelt_profile(
                    command, root, network_policy, read_only_roots=read_only_roots
                )
                return SandboxPlan(
                    command=(executable, "-p", profile, *command),
                    backend="macos_seatbelt",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic="Enforced by macOS Seatbelt",
                    resource_limits_enforced=(),
                )
        if self._platform.startswith("linux"):
            executable = self._which("bwrap")
            if executable:
                return SandboxPlan(
                    command=_bubblewrap_command(
                        executable,
                        command,
                        root,
                        network_policy,
                        read_only_roots=read_only_roots,
                    ),
                    backend="linux_bubblewrap",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic="Enforced by Linux bubblewrap namespaces",
                    resource_limits_enforced=(),
                )
        if container_image:
            runtime = self._which("docker") or self._which("podman")
            if runtime:
                return SandboxPlan(
                    command=_oci_command(
                        runtime,
                        container_image,
                        command,
                        root,
                        network_policy,
                        read_only_roots=read_only_roots,
                        environment=environment,
                    ),
                    backend="oci",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic=f"Enforced by OCI runtime {Path(runtime).name}",
                    resource_limits_enforced=("process_count", "memory", "cpu"),
                )
        return None

    def _available_backend(self, *, container_image: str | None) -> SandboxBackend | None:
        if self._platform == "darwin" and self._which("sandbox-exec"):
            return "macos_seatbelt"
        if self._platform.startswith("linux") and self._which("bwrap"):
            return "linux_bubblewrap"
        if container_image and (self._which("docker") or self._which("podman")):
            return "oci"
        return None


class RuntimeSandboxLauncher:
    """Adapt :class:`SandboxPlanner` to the MCP transport launcher contract."""

    def __init__(
        self,
        planner: SandboxPlanner | None = None,
        *,
        container_image: str | None = None,
    ) -> None:
        self._planner = planner or SandboxPlanner()
        self._container_image = container_image

    def prepare(
        self,
        command: PreparedStdioCommand,
        *,
        trust_level: str,
        sandbox_mode: str,
        network_policy: str,
    ) -> PreparedStdioCommand:
        if trust_level not in {"trusted", "untrusted"}:
            raise SkillExecutionError("invalid_sandbox_policy", "Invalid MCP trust level")
        if sandbox_mode not in {"required", "preferred", "disabled"}:
            raise SkillExecutionError("invalid_sandbox_policy", "Invalid MCP sandbox mode")
        if network_policy == "loopback":
            raise SkillExecutionError(
                "sandbox_network_policy_unavailable",
                "No active sandbox backend can enforce host-loopback-only networking; "
                "choose deny or allow explicitly",
            )
        if network_policy not in {"deny", "allow", "loopback"}:
            raise SkillExecutionError("invalid_sandbox_policy", "Invalid MCP network policy")
        effective_network: NetworkPolicy = "allow" if network_policy == "allow" else "deny"
        try:
            plan = self._planner.prepare(
                (command.command, *command.args),
                working_dir=command.cwd,
                mode=cast(SandboxMode, sandbox_mode),
                trust_level=cast(TrustLevel, trust_level),
                network_policy=effective_network,
                container_image=self._container_image,
                read_only_roots=command.read_only_roots,
                environment=command.env,
            )
        except SandboxPolicyError as error:
            raise SkillExecutionError("sandbox_unavailable", str(error)) from error
        return PreparedStdioCommand(
            command=plan.command[0],
            args=plan.command[1:],
            cwd=command.cwd,
            env=command.env,
            # ``none`` is an observed backend too: it distinguishes an invoked
            # trusted soft/disabled process from a plugin that has never run.
            sandbox_backend=plan.backend,
            sandbox_limits_enforced=plan.resource_limits_enforced,
            read_only_roots=command.read_only_roots,
        )


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if not command or len(command) > 64:
        raise SandboxPolicyError("MCP command requires between 1 and 64 arguments")
    normalized = tuple(str(part) for part in command)
    if any(not part or "\0" in part or len(part) > 16_384 for part in normalized):
        raise SandboxPolicyError("MCP command contains an invalid argument")
    return normalized


def _seatbelt_profile(
    command: tuple[str, ...],
    root: Path,
    network_policy: NetworkPolicy,
    *,
    read_only_roots: tuple[Path, ...] = (),
) -> str:
    readable_roots = {
        Path("/System"),
        Path("/Library/Frameworks"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/private/var/db/timezone"),
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
        root,
        *read_only_roots,
    }
    resolved_executable = _resolve_executable(command[0])
    if resolved_executable is not None:
        readable_roots.add(resolved_executable.parent)
    read_rules = "\n".join(
        f"(allow file-read* (subpath {_scheme_string(path)}))"
        for path in sorted(readable_roots, key=str)
        if path.exists()
    )
    network_rule = "(allow network*)" if network_policy == "allow" else "(deny network*)"
    return "\n".join(
        (
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(allow process*)",
            "(allow signal (target self))",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow file-read-metadata)",
            read_rules,
            f"(allow file-write* (subpath {_scheme_string(root)}))",
            '(allow file-write* (subpath "/private/tmp"))',
            network_rule,
        )
    )


def _bubblewrap_command(
    executable: str,
    command: tuple[str, ...],
    root: Path,
    network_policy: NetworkPolicy,
    *,
    read_only_roots: tuple[Path, ...] = (),
) -> tuple[str, ...]:
    result = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    if network_policy == "allow":
        result.append("--share-net")
    for system_root in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
        if Path(system_root).exists():
            result.extend(("--ro-bind", system_root, system_root))
    for python_root in {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}:
        if python_root.exists() and not any(
            python_root.is_relative_to(Path(parent))
            for parent in ("/usr", "/bin", "/lib", "/lib64")
            if Path(parent).exists()
        ):
            result.extend(("--ro-bind", str(python_root), str(python_root)))
    for read_only_root in read_only_roots:
        if read_only_root != root:
            result.extend(("--ro-bind", str(read_only_root), str(read_only_root)))
    result.extend(("--bind", str(root), str(root), "--chdir", str(root), "--"))
    result.extend(command)
    return tuple(result)


def _oci_command(
    runtime: str,
    image: str,
    command: tuple[str, ...],
    root: Path,
    network_policy: NetworkPolicy,
    *,
    read_only_roots: tuple[Path, ...] = (),
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    if not image.strip() or any(character.isspace() for character in image):
        raise SandboxPolicyError("OCI sandbox image is invalid")
    mapped_command = tuple(
        _map_oci_command_part(
            part,
            index=index,
            writable_root=root,
            read_only_roots=read_only_roots,
        )
        for index, part in enumerate(command)
    )
    read_only_mounts: tuple[str, ...] = tuple(
        part
        for index, path in enumerate(read_only_roots)
        for part in ("--mount", f"type=bind,src={path},dst=/package-{index},readonly")
    )
    container_environment = _oci_environment(environment or {}, read_only_roots=read_only_roots)
    environment_arguments: tuple[str, ...] = tuple(
        part
        for key, value in sorted(container_environment.items())
        for part in ("--env", f"{key}={value}")
    )
    return (
        runtime,
        "run",
        "--rm",
        "--interactive",
        "--network",
        "none" if network_policy == "deny" else "bridge",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--mount",
        f"type=bind,src={root},dst=/plugin",
        *read_only_mounts,
        *environment_arguments,
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--workdir",
        "/plugin",
        image,
        *mapped_command,
    )


def _map_oci_command_part(
    part: str,
    *,
    index: int,
    writable_root: Path,
    read_only_roots: tuple[Path, ...],
) -> str:
    candidate = Path(part)
    if not candidate.is_absolute():
        return part
    resolved = candidate.resolve()
    if resolved.is_relative_to(writable_root):
        relative = resolved.relative_to(writable_root).as_posix()
        return f"/plugin/{relative}" if relative != "." else "/plugin"
    for root_index, read_only_root in enumerate(read_only_roots):
        if resolved.is_relative_to(read_only_root):
            relative = resolved.relative_to(read_only_root).as_posix()
            base = f"/package-{root_index}"
            return f"{base}/{relative}" if relative != "." else base
    if index == 0:
        # The configured image owns its executable runtime.  Host interpreter
        # paths are never valid inside an OCI filesystem.
        return "python" if candidate.name.startswith("python") else candidate.name
    return part


def _oci_environment(
    environment: Mapping[str, str],
    *,
    read_only_roots: tuple[Path, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in (
        "CHATWAIFU_MCP_SUBJECT_ID",
        "CHATWAIFU_PLUGIN_DATA_DIR",
        "CHATWAIFU_PLUGIN_PACKAGE_DIR",
    ):
        value = environment.get(key)
        if not value:
            continue
        if key == "CHATWAIFU_PLUGIN_DATA_DIR":
            result[key] = "/plugin"
        elif key == "CHATWAIFU_PLUGIN_PACKAGE_DIR":
            package_path = Path(value).resolve()
            try:
                root_index = read_only_roots.index(package_path)
            except ValueError:
                continue
            result[key] = f"/package-{root_index}"
        else:
            result[key] = value
    return result


def _resolve_executable(command: str) -> Path | None:
    candidate = Path(command)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    located = shutil.which(command, path=os.environ.get("PATH"))
    return Path(located).resolve() if located else None


def _scheme_string(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=True)
