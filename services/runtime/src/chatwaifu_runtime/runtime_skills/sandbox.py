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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    ) -> SandboxPlan:
        normalized = _validate_command(command)
        root = working_dir.expanduser().resolve()
        if not root.is_dir():
            raise SandboxPolicyError(f"Sandbox working directory is missing: {root}")
        if mode == "disabled":
            if trust_level != "trusted":
                raise SandboxPolicyError("Untrusted local MCP servers cannot disable the sandbox")
            return SandboxPlan(
                command=normalized,
                backend="none",
                enforced=False,
                network_policy=network_policy,
                diagnostic="Sandbox explicitly disabled for a trusted local server",
            )

        plan = self._native_plan(
            normalized,
            root=root,
            network_policy=network_policy,
            container_image=container_image,
        )
        if plan is not None:
            return plan
        if mode == "required":
            raise SandboxPolicyError(
                "No enforcing sandbox backend is available; install bubblewrap on Linux, "
                "use macOS Seatbelt, or configure an OCI image/runtime"
            )
        return SandboxPlan(
            command=normalized,
            backend="none",
            enforced=False,
            network_policy=network_policy,
            diagnostic="No OS sandbox backend is available; trusted process uses soft isolation",
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
    ) -> SandboxPlan | None:
        if self._platform == "darwin":
            executable = self._which("sandbox-exec")
            if executable:
                profile = _seatbelt_profile(command, root, network_policy)
                return SandboxPlan(
                    command=(executable, "-p", profile, *command),
                    backend="macos_seatbelt",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic="Enforced by macOS Seatbelt",
                )
        if self._platform.startswith("linux"):
            executable = self._which("bwrap")
            if executable:
                return SandboxPlan(
                    command=_bubblewrap_command(executable, command, root, network_policy),
                    backend="linux_bubblewrap",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic="Enforced by Linux bubblewrap namespaces",
                )
        if container_image:
            runtime = self._which("docker") or self._which("podman")
            if runtime:
                return SandboxPlan(
                    command=_oci_command(runtime, container_image, command, root, network_policy),
                    backend="oci",
                    enforced=True,
                    network_policy=network_policy,
                    diagnostic=f"Enforced by OCI runtime {Path(runtime).name}",
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


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if not command or len(command) > 64:
        raise SandboxPolicyError("MCP command requires between 1 and 64 arguments")
    normalized = tuple(str(part) for part in command)
    if any(not part or "\0" in part or len(part) > 16_384 for part in normalized):
        raise SandboxPolicyError("MCP command contains an invalid argument")
    return normalized


def _seatbelt_profile(command: tuple[str, ...], root: Path, network_policy: NetworkPolicy) -> str:
    readable_roots = {
        Path("/System"),
        Path("/Library/Frameworks"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/private/var/db/timezone"),
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
        root,
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
    result.extend(("--bind", str(root), str(root), "--chdir", str(root), "--"))
    result.extend(command)
    return tuple(result)


def _oci_command(
    runtime: str,
    image: str,
    command: tuple[str, ...],
    root: Path,
    network_policy: NetworkPolicy,
) -> tuple[str, ...]:
    if not image.strip() or any(character.isspace() for character in image):
        raise SandboxPolicyError("OCI sandbox image is invalid")
    mapped_command = tuple(
        f"/plugin/{part}" if index > 0 and Path(part).is_absolute() else part
        for index, part in enumerate(command)
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
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--workdir",
        "/plugin",
        image,
        *mapped_command,
    )


def _resolve_executable(command: str) -> Path | None:
    candidate = Path(command)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    located = shutil.which(command, path=os.environ.get("PATH"))
    return Path(located).resolve() if located else None


def _scheme_string(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=True)
