"""Validated Runtime Skill discovery without executing plugin code."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml  # pyright: ignore[reportMissingTypeStubs]
from chatwaifu_protocol.base import SideEffect
from chatwaifu_protocol.skills import (
    McpConnectionSnapshot,
    PluginManifest,
    SkillCapability,
    SkillDefinition,
)

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError

MAX_MANIFEST_BYTES = 256 * 1024
MAX_INSTRUCTIONS_BYTES = 512 * 1024
_FRONTMATTER = re.compile(r"\A---\s*\n(?P<body>.*?)\n---(?:\s*\n|\Z)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class SkillAdapterSpec:
    kind: Literal["builtin", "mcp", "mcp_connection"]
    target: str


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    definition: SkillDefinition
    adapter: SkillAdapterSpec
    instructions_path: Path | None = None
    instructions_text: str | None = None
    plugin: PluginManifest | None = None
    plugin_root: Path | None = None


class SkillRegistry:
    def __init__(self, builtin_root: Path) -> None:
        self._builtin_root = builtin_root
        self._entries: dict[str, RegistryEntry] = {}

    def reload(
        self,
        plugins: list[tuple[PluginManifest, Path, bool]],
        mcp_connections: list[McpConnectionSnapshot] | None = None,
    ) -> None:
        entries: dict[str, RegistryEntry] = {}
        for manifest_path in sorted(self._builtin_root.glob("*/chatwaifu.yaml")):
            entry = _load_skill(manifest_path, source="builtin")
            _insert(entries, entry)
        for plugin, root, enabled in plugins:
            for relative in plugin.skills:
                manifest_path = _safe_child(root, relative)
                entry = _load_skill(
                    manifest_path,
                    source="plugin",
                    plugin=plugin,
                    plugin_root=root,
                    enabled=enabled,
                )
                _insert(entries, entry)
        for connection in mcp_connections or []:
            if connection.capabilities.tools:
                _insert(entries, _load_mcp_connection(connection))
        self._entries = entries

    def list(self, *, include_disabled: bool = True) -> list[SkillDefinition]:
        return [
            entry.definition
            for entry in self._entries.values()
            if include_disabled or entry.definition.enabled
        ]

    def get(self, skill_id: str) -> RegistryEntry | None:
        return self._entries.get(skill_id)

    def instructions(self, skill_id: str) -> str:
        entry = self._entries.get(skill_id)
        if entry is None:
            raise KeyError(skill_id)
        if entry.instructions_text is not None:
            return entry.instructions_text
        if entry.instructions_path is None:
            raise SkillExecutionError("missing_instructions", "Skill instructions are missing")
        return _read_bounded(entry.instructions_path, MAX_INSTRUCTIONS_BYTES)


def load_plugin_manifest(root: Path) -> PluginManifest:
    path = _safe_child(root, "plugin.json")
    return PluginManifest.model_validate_json(_read_bounded(path, MAX_MANIFEST_BYTES))


def _load_skill(
    path: Path,
    *,
    source: Literal["builtin", "plugin"],
    plugin: PluginManifest | None = None,
    plugin_root: Path | None = None,
    enabled: bool = True,
) -> RegistryEntry:
    loaded: object = yaml.safe_load(_read_bounded(path, MAX_MANIFEST_BYTES))
    if not isinstance(loaded, dict):
        raise SkillExecutionError("invalid_manifest", f"Invalid skill manifest: {path}")
    raw = cast(dict[str, object], loaded)
    if raw.get("schema_version") != "1.0":
        raise SkillExecutionError("invalid_manifest", f"Invalid skill manifest: {path}")
    definition_value = raw.get("definition")
    adapter_value = raw.get("adapter")
    instructions_value = raw.get("instructions", "SKILL.md")
    if not isinstance(definition_value, dict) or not isinstance(adapter_value, dict):
        raise SkillExecutionError("invalid_manifest", f"Missing definition or adapter: {path}")
    definition_raw = cast(dict[str, object], definition_value)
    adapter_raw = cast(dict[str, object], adapter_value)
    if not isinstance(instructions_value, str):
        raise SkillExecutionError("invalid_manifest", f"Invalid instructions path: {path}")
    expected_kind = "builtin" if source == "builtin" else "mcp"
    kind = adapter_raw.get("kind")
    target = adapter_raw.get("handler") if kind == "builtin" else adapter_raw.get("tool")
    if kind != expected_kind or not isinstance(target, str) or not target:
        raise SkillExecutionError("invalid_manifest", f"Invalid adapter in {path}")
    definition_raw = dict(definition_raw)
    definition_raw.update(
        {
            "source": source,
            "plugin_id": plugin.plugin_id if plugin else None,
            "enabled": enabled,
        }
    )
    definition = SkillDefinition.model_validate(definition_raw)
    instructions_path = _safe_child(path.parent, instructions_value)
    frontmatter = _parse_frontmatter(_read_bounded(instructions_path, MAX_INSTRUCTIONS_BYTES))
    if (
        frontmatter.get("id") != definition.skill_id
        or frontmatter.get("version") != definition.version
    ):
        raise SkillExecutionError(
            "manifest_mismatch",
            f"SKILL.md id/version does not match chatwaifu.yaml: {path.parent}",
        )
    return RegistryEntry(
        definition=definition,
        adapter=SkillAdapterSpec(kind=cast(Literal["builtin", "mcp"], kind), target=target),
        instructions_path=instructions_path,
        plugin=plugin,
        plugin_root=plugin_root,
    )


def _load_mcp_connection(connection: McpConnectionSnapshot) -> RegistryEntry:
    skill_id = f"mcp.{connection.connection_id.hex}"
    side_effect = (
        SideEffect.WRITE if connection.transport == "stdio" else SideEffect.EXTERNAL_COMMUNICATION
    )
    permission = f"mcp.connection.{connection.connection_id}.tool.call"
    capabilities = [
        SkillCapability(
            name=tool.name,
            adapter_tool=tool.name,
            description=tool.description or f"Call MCP tool {tool.name}",
            input_schema=tool.input_schema,
            output_schema=tool.output_schema or {"type": "object"},
            side_effect=side_effect,
            required_permissions=[permission],
            confirmation_required=True,
            timeout_seconds=connection.timeout_seconds,
        )
        for tool in connection.capabilities.tools
    ]
    definition = SkillDefinition(
        skill_id=skill_id,
        version=connection.capabilities.server_version or "0.0.0",
        name=connection.name,
        description=(
            "Discovered tools from MCP server "
            f"{connection.capabilities.server_name or connection.name}."
        ),
        capabilities=capabilities,
        interruptible=True,
        background_allowed=False,
        source="mcp_connection",
        mcp_connection_id=connection.connection_id,
        enabled=connection.enabled and connection.status == "ready",
    )
    instructions = (
        f"# {connection.name}\n\n"
        "This skill exposes tools discovered from an external MCP connection. "
        "Only invoke the specific tool required by the user. Every call remains subject "
        "to Runtime permissions and per-invocation confirmation.\n"
    )
    return RegistryEntry(
        definition=definition,
        adapter=SkillAdapterSpec(kind="mcp_connection", target=""),
        instructions_text=instructions,
    )


def _insert(entries: dict[str, RegistryEntry], entry: RegistryEntry) -> None:
    if entry.definition.skill_id in entries:
        raise SkillExecutionError(
            "duplicate_skill_id", f"Duplicate Runtime Skill id: {entry.definition.skill_id}"
        )
    entries[entry.definition.skill_id] = entry


def _safe_child(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise SkillExecutionError("unsafe_path", "Manifest paths must be relative")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    if not candidate.is_relative_to(resolved_root) or candidate.is_symlink():
        raise SkillExecutionError("unsafe_path", f"Path escapes plugin root: {relative}")
    if not candidate.is_file():
        raise SkillExecutionError("missing_file", f"Required manifest file is missing: {relative}")
    return candidate


def _read_bounded(path: Path, limit: int) -> str:
    if path.is_symlink() or not path.is_file():
        raise SkillExecutionError("unsafe_path", f"Expected a regular file: {path}")
    if path.stat().st_size > limit:
        raise SkillExecutionError("manifest_too_large", f"File exceeds size limit: {path}")
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillExecutionError("invalid_instructions", "SKILL.md requires YAML frontmatter")
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise SkillExecutionError(
                "invalid_instructions", "Only flat SKILL.md frontmatter is supported"
            )
        result[key.strip()] = value.strip().strip("\"'")
    return result
