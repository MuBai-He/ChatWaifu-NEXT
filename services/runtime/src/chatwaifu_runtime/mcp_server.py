"""Loopback-only MCP surface for permissioned Runtime Skills.

The MCP endpoint is deliberately a thin protocol adapter.  It never executes a
capability directly: every tool call is converted into a ``SkillInvocation`` and
sent through ``RuntimeSkillService`` so schema validation, permission grants,
per-call confirmation, timeout, cancellation, persistence, and audit events keep
their existing ownership.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from pathlib import PurePath
from typing import Any, cast
from uuid import UUID

import mcp.types as mcp_types
from chatwaifu_protocol.base import JsonObject, JsonValue, SideEffect
from chatwaifu_protocol.session import SessionState
from chatwaifu_protocol.skills import (
    SkillCapability,
    SkillDefinition,
    SkillInvocation,
    SkillRunSnapshot,
    SkillRunState,
)
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from chatwaifu_runtime import __version__
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError

_TERMINAL_EVENTS = frozenset({"skill.run_completed", "skill.run_failed", "skill.run_cancelled"})
_TERMINAL_STATES = frozenset(
    {
        SkillRunState.SUCCEEDED,
        SkillRunState.FAILED,
        SkillRunState.CANCELLED,
        SkillRunState.EXPIRED,
    }
)
_MCP_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "worker_token",
    }
)


class RuntimeMcpServer:
    """Expose safe Runtime metadata and permissioned Skills over MCP."""

    def __init__(self, container: RuntimeContainer) -> None:
        self._container = container
        configured_token = container.settings.security.admin_token
        self._admin_token = (
            configured_token.get_secret_value() if configured_token is not None else None
        )
        self._anonymous_session_id: UUID | None = None
        self._anonymous_session_lock = asyncio.Lock()
        self._transport_lifespan: AbstractAsyncContextManager[object] | None = None
        self._started = False

        self.server: Server[dict[str, object]] = Server(
            "chatwaifu-runtime",
            version=__version__,
            title="ChatWaifu NEXT Runtime",
            description="Local ChatWaifu character metadata and permissioned Runtime Skills.",
            instructions=(
                "This endpoint is local-only. Tool calls are audited Runtime Skill jobs. "
                "A pending_confirmation result means no side effect has executed; ask the "
                "local user to approve that request in ChatWaifu before continuing."
            ),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
            on_list_prompts=self._list_prompts,
            on_get_prompt=self._get_prompt,
        )
        self.transport_app: Starlette = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            host=container.settings.runtime.host,
            max_request_body_size=1_048_576,
        )
        transport: ASGIApp = self.transport_app
        if self._admin_token is not None:
            transport = _BearerTokenMiddleware(transport, self._admin_token)
        self.app = transport

    @property
    def has_admin_token(self) -> bool:
        """Whether authenticated, side-effecting tools may be advertised."""

        return self._admin_token is not None

    async def start(self) -> None:
        """Start the official Streamable HTTP session manager."""

        if self._started:
            return
        context = self.transport_app.router.lifespan_context(self.transport_app)
        await context.__aenter__()
        self._transport_lifespan = cast(AbstractAsyncContextManager[object], context)
        self._started = True

    async def stop(self) -> None:
        """Close anonymous read context and Streamable HTTP background work."""

        if not self._started:
            return
        self._started = False
        anonymous_session_id = self._anonymous_session_id
        self._anonymous_session_id = None
        if anonymous_session_id is not None:
            session = await self._container.sessions.get_session(anonymous_session_id)
            if session is not None and session.state is not SessionState.CLOSED:
                await self._container.sessions.close_session(anonymous_session_id)
        context = self._transport_lifespan
        self._transport_lifespan = None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def _list_tools(
        self,
        _context: ServerRequestContext[dict[str, object]],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        tools = [
            self._mcp_tool(skill, capability, tool_name)
            for skill, capability, tool_name in self._published_tool_catalog()
        ]
        return mcp_types.ListToolsResult(tools=tools, cache_scope="private", ttl_ms=1_000)

    async def _call_tool(
        self,
        _context: ServerRequestContext[dict[str, object]],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        selected = self._find_capability(params.name)
        if selected is None:
            return _error_result(
                "tool_unavailable",
                "The requested Runtime Skill tool is unavailable or not permitted.",
            )
        skill, capability = selected
        raw_arguments = dict(params.arguments or {})
        session_value = raw_arguments.pop("session_id", None)

        explicit_session_id: UUID | None = None
        if session_value is not None:
            if not isinstance(session_value, str):
                return _error_result("invalid_session", "session_id must be a UUID string.")
            try:
                explicit_session_id = UUID(session_value)
            except ValueError:
                return _error_result("invalid_session", "session_id must be a valid UUID.")
            session = await self._container.sessions.get_session(explicit_session_id)
            if session is None:
                return _error_result(
                    "unknown_session", "The requested ChatWaifu session does not exist."
                )
            if session.state is not SessionState.READY:
                return _error_result(
                    "session_unavailable", "The requested ChatWaifu session is not ready."
                )

        if capability.side_effect is not SideEffect.READ and explicit_session_id is None:
            return _error_result(
                "session_required",
                "Side-effecting Runtime Skill tools require an explicit active session_id.",
            )
        if capability.side_effect is not SideEffect.READ and self._admin_token is None:
            return _error_result(
                "tool_unavailable",
                "Side-effecting Runtime Skill tools are disabled until an admin token is "
                "configured.",
            )

        session_id = explicit_session_id or await self._anonymous_read_session()
        return await self._invoke_skill(skill, capability, session_id, raw_arguments)

    async def _list_resources(
        self,
        _context: ServerRequestContext[dict[str, object]],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListResourcesResult:
        return mcp_types.ListResourcesResult(
            resources=[
                mcp_types.Resource(
                    uri="runtime://status",
                    name="runtime-status",
                    title="ChatWaifu Runtime status",
                    description="Secret-free local Runtime and provider availability metadata.",
                    mime_type="application/json",
                ),
                mcp_types.Resource(
                    uri="runtime://characters",
                    name="characters",
                    title="ChatWaifu characters",
                    description="Public presentation metadata for installed characters.",
                    mime_type="application/json",
                ),
            ],
            cache_scope="private",
            ttl_ms=1_000,
        )

    async def _read_resource(
        self,
        _context: ServerRequestContext[dict[str, object]],
        params: mcp_types.ReadResourceRequestParams,
    ) -> mcp_types.ReadResourceResult:
        uri = str(params.uri)
        if uri == "runtime://status":
            providers = self._container.providers.public_status()
            body: JsonObject = {
                "name": "chatwaifu-runtime",
                "version": __version__,
                "status": "ready",
                "providers": cast(JsonValue, providers),
                "stt_provider": self._container.stt.kind,
                "transport": "pipecat_smallwebrtc",
                "persistence": "sqlite_wal",
                "mcp_transport": "streamable_http",
            }
        elif uri == "runtime://characters":
            body = {
                "characters": [
                    {
                        "character_id": profile.character_id,
                        "display_name": profile.display_name,
                        "tagline": profile.tagline,
                        "content_notice": profile.content_notice,
                        "accent_color": profile.accent_color,
                        "language": profile.voice_profile.language,
                    }
                    for profile in self._container.characters.list()
                ]
            }
        else:
            raise ValueError("Unknown ChatWaifu Runtime resource")
        return mcp_types.ReadResourceResult(
            contents=[
                mcp_types.TextResourceContents(
                    uri=uri,
                    mime_type="application/json",
                    text=json.dumps(_sanitize_public(body), ensure_ascii=False, sort_keys=True),
                )
            ],
            cache_scope="private",
            ttl_ms=1_000,
        )

    async def _list_prompts(
        self,
        _context: ServerRequestContext[dict[str, object]],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListPromptsResult:
        return mcp_types.ListPromptsResult(
            prompts=[
                mcp_types.Prompt(
                    name="chatwaifu-character-turn",
                    title="Prepare a ChatWaifu character turn",
                    description=(
                        "Prepare a character-aware prompt from public metadata without exposing "
                        "private memories or internal persona instructions."
                    ),
                    arguments=[
                        mcp_types.PromptArgument(
                            name="user_message",
                            description="The user's current message.",
                            required=True,
                        ),
                        mcp_types.PromptArgument(
                            name="character_id",
                            description="Installed character id; defaults to default.",
                            required=False,
                        ),
                        mcp_types.PromptArgument(
                            name="session_id",
                            description="Optional active ChatWaifu session UUID.",
                            required=False,
                        ),
                    ],
                )
            ],
            cache_scope="private",
            ttl_ms=1_000,
        )

    async def _get_prompt(
        self,
        _context: ServerRequestContext[dict[str, object]],
        params: mcp_types.GetPromptRequestParams,
    ) -> mcp_types.GetPromptResult:
        if params.name != "chatwaifu-character-turn":
            raise ValueError("Unknown ChatWaifu Runtime prompt")
        arguments = params.arguments or {}
        user_message = arguments.get("user_message", "").strip()
        if not user_message:
            raise ValueError("user_message is required")
        if len(user_message) > 8_000:
            raise ValueError("user_message is too long")

        character_id = arguments.get("character_id", "default").strip() or "default"
        session_value = arguments.get("session_id", "").strip()
        session_label = "none"
        if session_value:
            try:
                session_id = UUID(session_value)
            except ValueError as error:
                raise ValueError("session_id must be a valid UUID") from error
            session = await self._container.sessions.get_session(session_id)
            if session is None:
                raise ValueError("Unknown ChatWaifu session")
            if session.state is not SessionState.READY:
                raise ValueError("ChatWaifu session is not ready")
            if "character_id" in arguments and character_id != session.character_id:
                raise ValueError("character_id does not match session_id")
            character_id = session.character_id
            session_label = str(session.session_id)

        character = self._container.characters.get(character_id)
        if character is None:
            raise ValueError("Unknown ChatWaifu character")
        text = (
            "Prepare one ChatWaifu character reply using the local Runtime when appropriate.\n"
            f"Character id: {character.character_id}\n"
            f"Display name: {character.display_name}\n"
            f"Public character summary: {character.tagline}\n"
            f"Session id: {session_label}\n"
            f"User message: {user_message}\n\n"
            "Keep the reply consistent with the character summary. Do not invent tool results, "
            "private memories, relationship state, or actions. If a Runtime Skill returns "
            "pending_confirmation, wait for the local user's decision before claiming success."
        )
        return mcp_types.GetPromptResult(
            description="Character-aware ChatWaifu turn scaffold.",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=mcp_types.TextContent(type="text", text=text),
                )
            ],
        )

    def _published_capabilities(self) -> list[tuple[SkillDefinition, SkillCapability]]:
        result: list[tuple[SkillDefinition, SkillCapability]] = []
        for skill in self._container.runtime_skills.list():
            if not skill.enabled:
                continue
            for capability in skill.capabilities:
                if capability.side_effect is not SideEffect.READ and self._admin_token is None:
                    continue
                if (
                    capability.side_effect is not SideEffect.READ
                    and not capability.confirmation_required
                    and not capability.required_permissions
                ):
                    # A malformed manifest must not turn MCP authentication into
                    # authorization. Side effects need a Permission Broker gate.
                    continue
                if not _supports_mcp_input(capability.input_schema):
                    continue
                result.append((skill, capability))
        return result

    def _find_capability(self, tool_name: str) -> tuple[SkillDefinition, SkillCapability] | None:
        for skill, capability, published_name in self._published_tool_catalog():
            if published_name == tool_name:
                return skill, capability
        return None

    def _published_tool_catalog(
        self,
    ) -> list[tuple[SkillDefinition, SkillCapability, str]]:
        return allocate_mcp_tool_names(self._published_capabilities())

    def _mcp_tool(
        self, skill: SkillDefinition, capability: SkillCapability, tool_name: str
    ) -> mcp_types.Tool:
        read_only = capability.side_effect is SideEffect.READ
        return mcp_types.Tool(
            name=tool_name,
            title=f"{skill.name} · {capability.name}",
            description=_sanitize_text(capability.description),
            input_schema=_tool_input_schema(capability),
            annotations=mcp_types.ToolAnnotations(
                read_only_hint=read_only,
                destructive_hint=capability.side_effect is SideEffect.DESTRUCTIVE,
                idempotent_hint=True if read_only else None,
                open_world_hint=capability.side_effect is SideEffect.EXTERNAL_COMMUNICATION,
            ),
            _meta={
                "chatwaifu/skillId": skill.skill_id,
                "chatwaifu/skillVersion": skill.version,
                "chatwaifu/capability": capability.name,
                "chatwaifu/sideEffect": capability.side_effect.value,
                "chatwaifu/confirmationRequired": capability.confirmation_required,
            },
        )

    async def _anonymous_read_session(self) -> UUID:
        async with self._anonymous_session_lock:
            if self._anonymous_session_id is not None:
                session = await self._container.sessions.get_session(self._anonymous_session_id)
                if session is not None and session.state is SessionState.READY:
                    return session.session_id
            created = await self._container.sessions.create_session("default")
            self._anonymous_session_id = created.session_id
            return created.session_id

    async def _invoke_skill(
        self,
        skill: SkillDefinition,
        capability: SkillCapability,
        session_id: UUID,
        arguments: dict[str, Any],
    ) -> mcp_types.CallToolResult:
        subscription = self._container.event_hub.subscribe(
            lambda event: event.get("event_type") in _TERMINAL_EVENTS,
            queue_size=64,
        )
        run_id: UUID | None = None
        try:
            snapshot = await self._container.runtime_skills.invoke(
                session_id,
                SkillInvocation(
                    skill_id=skill.skill_id,
                    capability=capability.name,
                    arguments=cast(JsonObject, arguments),
                ),
                principal="mcp_admin" if self._admin_token is not None else "mcp_readonly",
            )
            run_id = snapshot.skill_run_id
            if snapshot.state is SkillRunState.WAITING_FOR_CONFIRMATION:
                return self._run_result(snapshot, status="pending_confirmation")
            if snapshot.state in _TERMINAL_STATES:
                return self._run_result(snapshot)

            try:
                async with asyncio.timeout(capability.timeout_seconds + 2):
                    async for event in _subscription_events(subscription):
                        if str(event.get("skill_run_id")) != str(run_id):
                            continue
                        completed = await self._container.runtime_skills.get_run(run_id)
                        return self._run_result(completed)
            except TimeoutError:
                if not skill.interruptible:
                    current = await self._container.runtime_skills.get_run(run_id)
                    return self._run_result(current, status="still_running")
                cancelled = await self._container.runtime_skills.cancel(run_id)
                return self._run_result(cancelled, status="cancelled")
        except asyncio.CancelledError:
            if run_id is not None and skill.interruptible:
                await self._container.runtime_skills.cancel(run_id)
            raise
        except SkillExecutionError as error:
            return _error_result(error.structured.code, _sanitize_text(error.structured.message))
        except (KeyError, ValueError) as error:
            return _error_result("skill_rejected", _sanitize_text(str(error)))
        finally:
            self._container.event_hub.unsubscribe(subscription)
        return _error_result("skill_incomplete", "Runtime Skill did not produce a terminal result.")

    def _run_result(
        self, snapshot: SkillRunSnapshot, *, status: str | None = None
    ) -> mcp_types.CallToolResult:
        public_status = status or snapshot.state.value
        payload: dict[str, JsonValue] = {
            "status": public_status,
            "skill_run_id": str(snapshot.skill_run_id),
            "session_id": str(snapshot.session_id),
            "skill_id": snapshot.skill_id,
            "capability": snapshot.capability,
        }
        if snapshot.confirmation_request_id is not None:
            payload["confirmation_request_id"] = str(snapshot.confirmation_request_id)
            payload["message"] = (
                "No side effect has executed. Approve or deny this request in the local "
                "ChatWaifu confirmation UI."
            )
        if snapshot.result is not None:
            payload["data"] = cast(JsonValue, _sanitize_public(snapshot.result.data))
        if snapshot.error is not None:
            payload["error"] = cast(
                JsonValue,
                _sanitize_public(
                    {
                        "code": snapshot.error.code,
                        "message": snapshot.error.message,
                        "retryable": snapshot.error.retryable,
                    }
                ),
            )
        sanitized = cast(dict[str, JsonValue], _sanitize_public(payload))
        is_error = snapshot.state in {
            SkillRunState.FAILED,
            SkillRunState.CANCELLED,
            SkillRunState.EXPIRED,
        }
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text", text=json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
                )
            ],
            structured_content=sanitized,
            is_error=is_error,
        )


class _BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            authorization = next(
                (
                    value
                    for key, value in cast(list[tuple[bytes, bytes]], scope.get("headers", []))
                    if key.lower() == b"authorization"
                ),
                b"",
            )
            expected = b"Bearer " + self._token
            if not hmac.compare_digest(authorization, expected):
                response = JSONResponse(
                    {"error": "unauthorized", "message": "A valid MCP bearer token is required."},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


async def _subscription_events(subscription: Any) -> AsyncIterator[dict[str, object]]:
    while True:
        yield cast(dict[str, object], await subscription.receive())


def _tool_name(skill: SkillDefinition, capability: SkillCapability) -> str:
    normalized = _MCP_TOOL_NAME.sub("_", f"{skill.skill_id}__{capability.name}").strip("_")
    return normalized[:128]


def allocate_mcp_tool_names(
    capabilities: list[tuple[SkillDefinition, SkillCapability]],
) -> list[tuple[SkillDefinition, SkillCapability, str]]:
    """Allocate stable MCP names without normalization or truncation collisions."""

    grouped: dict[str, list[tuple[SkillDefinition, SkillCapability]]] = {}
    for skill, capability in capabilities:
        grouped.setdefault(_tool_name(skill, capability), []).append((skill, capability))
    reserved = set(grouped)
    used: set[str] = set()
    result: list[tuple[SkillDefinition, SkillCapability, str]] = []
    for base_name, entries in grouped.items():
        if len(entries) == 1 and base_name not in used:
            skill, capability = entries[0]
            used.add(base_name)
            result.append((skill, capability, base_name))
            continue
        for skill, capability in entries:
            identity = f"{skill.skill_id}\0{capability.name}".encode()
            digest = hashlib.sha256(identity).hexdigest()
            suffix_length = 12
            while True:
                suffix = digest[:suffix_length]
                candidate = f"{base_name[: 126 - suffix_length]}__{suffix}"
                if candidate not in used and candidate not in reserved:
                    break
                suffix_length += 2
                if suffix_length > len(digest):
                    raise RuntimeError("MCP tool-name collision could not be resolved")
            used.add(candidate)
            result.append((skill, capability, candidate))
    return result


def _supports_mcp_input(schema: Mapping[str, JsonValue]) -> bool:
    properties = schema.get("properties")
    return (
        schema.get("type") == "object"
        and isinstance(properties, dict)
        and "session_id" not in properties
    )


def _tool_input_schema(capability: SkillCapability) -> dict[str, Any]:
    schema = cast(dict[str, Any], deepcopy(capability.input_schema))
    properties = cast(dict[str, Any], schema.setdefault("properties", {}))
    properties["session_id"] = {
        "type": "string",
        "format": "uuid",
        "description": (
            "Active ChatWaifu session UUID. Optional for read-only tools and required for "
            "side-effecting tools."
        ),
    }
    return schema


def _sanitize_public(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in cast(Mapping[object, object], value).items():
            key = str(raw_key)
            if key.lower() in _SECRET_KEYS:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_public(raw_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_public(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return [_sanitize_public(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    if PurePath(value).is_absolute() or _WINDOWS_ABSOLUTE_PATH.match(value):
        return "[local-path-redacted]"
    return value


def _error_result(code: str, message: str) -> mcp_types.CallToolResult:
    payload = {"status": "error", "error": {"code": code, "message": message}}
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text", text=json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )
        ],
        structured_content=payload,
        is_error=True,
    )
