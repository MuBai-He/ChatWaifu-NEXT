"""Product Runtime Skill discovery, plugin, and job contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import JsonObject, JsonValue, ProtocolModel, SideEffect
from chatwaifu_protocol.errors import StructuredError


class SkillRunState(StrEnum):
    CREATED = "created"
    ACTIVATING = "activating"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SkillCapability(ProtocolModel):
    name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    side_effect: SideEffect = SideEffect.READ
    required_permissions: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    timeout_seconds: float = Field(default=30, gt=0)
    adapter_tool: str | None = Field(default=None, min_length=1, max_length=256)
    adapter_operation: Literal["invoke", "resource_read", "prompt_get"] = "invoke"


class SkillDefinition(ProtocolModel):
    skill_id: str
    version: str
    name: str
    description: str
    capabilities: list[SkillCapability] = Field(default_factory=list[SkillCapability])
    interruptible: bool = True
    background_allowed: bool = False
    source: Literal["builtin", "plugin", "mcp_connection"] = "builtin"
    plugin_id: str | None = None
    mcp_connection_id: UUID | None = None
    enabled: bool = True


class PluginTransport(ProtocolModel):
    kind: Literal["stdio"] = "stdio"
    command: list[str] = Field(min_length=1, max_length=32)
    trust_level: Literal["trusted", "untrusted"] = "untrusted"
    sandbox_mode: Literal["required", "preferred", "disabled"] = "required"
    network_policy: Literal["deny", "loopback", "allow"] = "deny"

    @model_validator(mode="after")
    def validate_sandbox_policy(self) -> PluginTransport:
        _validate_local_process_policy(
            trust_level=self.trust_level,
            sandbox_mode=self.sandbox_mode,
            network_policy=self.network_policy,
        )
        return self


class PluginManifest(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    plugin_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    transport: PluginTransport
    skills: list[str] = Field(min_length=1, max_length=32)


class PluginSnapshot(ProtocolModel):
    plugin_id: str
    version: str
    name: str
    description: str
    enabled: bool
    trust_level: Literal["trusted", "untrusted"] = "untrusted"
    sandbox_mode: Literal["required", "preferred", "disabled"] = "required"
    network_policy: Literal["deny", "loopback", "allow"] = "deny"
    sandbox_backend: str | None = None
    sandbox_limits_enforced: list[str] = Field(default_factory=list[str])
    install_path: str
    installed_at: AwareDatetime
    updated_at: AwareDatetime


class SkillInvocation(ProtocolModel):
    skill_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=256)
    arguments: JsonObject = Field(default_factory=dict)
    background: bool = False


class SkillRunSnapshot(ProtocolModel):
    skill_run_id: UUID
    skill_id: str
    skill_version: str
    capability: str
    plugin_id: str | None = None
    mcp_connection_id: UUID | None = None
    session_id: UUID
    state: SkillRunState
    progress: float | None = Field(default=None, ge=0, le=1)
    confirmation_request_id: UUID | None = None
    result: SkillResult | None = None
    error: StructuredError | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class SkillResult(ProtocolModel):
    status: str
    data: JsonValue = None
    spoken_summary: str | None = None
    ui_cards: list[JsonObject] = Field(default_factory=list[JsonObject])
    avatar_cues: list[AvatarCue] = Field(default_factory=list[AvatarCue])
    memory_proposal_ids: list[UUID] = Field(default_factory=list[UUID])
    prospective_task_ids: list[UUID] = Field(default_factory=list[UUID])
    provenance: list[str] = Field(default_factory=list)


class McpConnectionConfiguration(ProtocolModel):
    """Persisted MCP Host connection settings; authentication secrets are excluded."""

    connection_id: UUID
    name: str = Field(min_length=1, max_length=128)
    transport: Literal["stdio", "streamable_http", "sse"]
    command: list[str] = Field(default_factory=list, max_length=32)
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    allow_remote: bool = False
    enabled: bool = True
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    trust_level: Literal["trusted", "untrusted"] = "untrusted"
    sandbox_mode: Literal["required", "preferred", "disabled"] = "required"
    network_policy: Literal["deny", "loopback", "allow"] = "deny"

    @model_validator(mode="after")
    def validate_transport_fields(self) -> McpConnectionConfiguration:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio MCP connections require command")
            if self.url is not None:
                raise ValueError("stdio MCP connections do not accept url")
            if self.allow_remote:
                raise ValueError("stdio MCP connections do not use allow_remote")
            _validate_local_process_policy(
                trust_level=self.trust_level,
                sandbox_mode=self.sandbox_mode,
                network_policy=self.network_policy,
            )
        else:
            if self.command:
                raise ValueError("network MCP connections do not accept command")
            if self.url is None:
                raise ValueError("network MCP connections require url")
            if self.sandbox_mode != "disabled":
                raise ValueError("network MCP connections require sandbox_mode=disabled")
            expected_network = "allow" if self.allow_remote else "loopback"
            if self.network_policy != expected_network:
                raise ValueError(
                    f"network MCP connections require network_policy={expected_network}"
                )
        return self


def _validate_local_process_policy(
    *,
    trust_level: Literal["trusted", "untrusted"],
    sandbox_mode: Literal["required", "preferred", "disabled"],
    network_policy: Literal["deny", "loopback", "allow"],
) -> None:
    if network_policy == "loopback":
        raise ValueError("local MCP processes do not support a host-loopback-only network policy")
    if sandbox_mode == "disabled":
        if trust_level != "trusted":
            raise ValueError("untrusted local MCP processes cannot disable the sandbox")
        if network_policy != "allow":
            raise ValueError("disabled local MCP sandboxing requires network_policy=allow")


class McpToolDescriptor(ProtocolModel):
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=8_000)
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject | None = None


class McpResourceDescriptor(ProtocolModel):
    uri: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=8_000)
    mime_type: str | None = Field(default=None, max_length=256)


class McpResourceTemplateDescriptor(ProtocolModel):
    uri_template: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=8_000)
    mime_type: str | None = Field(default=None, max_length=256)


class McpPromptDescriptor(ProtocolModel):
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=8_000)
    arguments: list[JsonObject] = Field(default_factory=lambda: list[JsonObject]())


class McpCapabilitySnapshot(ProtocolModel):
    connection_id: UUID
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    tools: list[McpToolDescriptor] = Field(default_factory=lambda: list[McpToolDescriptor]())
    resources: list[McpResourceDescriptor] = Field(
        default_factory=lambda: list[McpResourceDescriptor]()
    )
    resource_templates: list[McpResourceTemplateDescriptor] = Field(
        default_factory=lambda: list[McpResourceTemplateDescriptor]()
    )
    prompts: list[McpPromptDescriptor] = Field(default_factory=lambda: list[McpPromptDescriptor]())
    discovered_at: AwareDatetime | None = None


class McpConnectionSnapshot(McpConnectionConfiguration):
    status: Literal["untested", "ready", "error", "disabled"] = "untested"
    bearer_token_configured: bool = False
    sandbox_backend: str | None = None
    sandbox_limits_enforced: list[str] = Field(default_factory=list[str])
    capabilities: McpCapabilitySnapshot
    last_error: str | None = None
    last_tested_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
