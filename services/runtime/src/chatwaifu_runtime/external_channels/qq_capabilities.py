"""Reviewed declarative QQ actions. Targets come only from a current admitted turn."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx2
from chatwaifu_protocol.agent import AgentTask, TaskChannelBinding
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelChatType, ChannelTurnStatus

from chatwaifu_runtime.agent.materials import extract_material
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.external_channels.groups import ChannelGroupService
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.public_web import validate_public_url
from chatwaifu_runtime.runtime_skills.transports import PinnedAsyncHTTPTransport

type ScopedCall = Callable[
    [UUID, str, str, JsonObject, Callable[[], Awaitable[bool]]], Awaitable[JsonObject]
]


class QQSceneCapabilities:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        conversations: ConversationRepository,
        groups: ChannelGroupService,
        active: Callable[[UUID], UUID | None],
        call: ScopedCall,
    ) -> None:
        self.repository = repository
        self.conversations = conversations
        self.groups = groups
        self.active = active
        self.call = call
        self.scene_enabled: Callable[[ChannelTurnRecord], Awaitable[bool]] | None = None
        self.task_reader: Callable[[UUID], Awaitable[AgentTask | None]] | None = None
        self._resources: dict[str, tuple[TaskChannelBinding, JsonObject, datetime]] = {}
        self.binding_authorizer: Callable[[TaskChannelBinding], Awaitable[bool]] | None = None

    async def capture(self, context: GenerationSkillContext) -> TaskChannelBinding | None:
        turn = await self.current(context)
        if turn is None or turn.account_key is None:
            return None
        binding = TaskChannelBinding(
            connection_id=turn.connection_id,
            account_key=turn.account_key,
            conversation_key=turn.conversation_key,
            sender_key=turn.sender_key,
            source_ref=turn.external_message_id,
        )
        if turn.group_route_id is not None:
            return await self.groups.task_binding(
                turn.group_route_id, turn.sender_key, turn.external_message_id
            )
        return binding

    async def binding(self, context: GenerationSkillContext) -> TaskChannelBinding | None:
        if context.task_id is None:
            return await self.capture(context)
        task = await self.task_reader(context.task_id) if self.task_reader else None
        if (
            task is None
            or task.session_id != context.session_id
            or task.state.value not in {"running", "waiting_authorization"}
            or task.authorization.expires_at <= datetime.now(UTC)
            or task.channel_binding is None
            or self.binding_authorizer is None
            or not await self.binding_authorizer(task.channel_binding)
        ):
            return None
        return task.channel_binding

    async def current(self, context: GenerationSkillContext) -> ChannelTurnRecord | None:
        if context.origin != "agent" or context.generation_id is None or context.turn_id is None:
            return None
        if self.active(context.session_id) != context.generation_id:
            return None
        for turn in await self.repository.list_inflight_turns():
            if (turn.session_id, turn.turn_id, turn.generation_id) == (
                context.session_id,
                context.turn_id,
                context.generation_id,
            ) and turn.status in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}:
                connection = await self.repository.get_connection(turn.connection_id)
                if connection is None or not connection.configuration.enabled:
                    return None
                if connection.configuration.provider_id != "qq_napcat":
                    return None
                if self.scene_enabled is not None and not await self.scene_enabled(turn):
                    return None
                if turn.chat_type is ChannelChatType.GROUP:
                    return turn if await self.groups.authorize_current_request(turn) else None
                if turn.sender_key in connection.configuration.allowed_sender_keys:
                    return turn
        return None

    async def authorize(self, context: GenerationSkillContext) -> bool:
        if context.task_id is not None:
            return await self.binding(context) is not None
        turn = await self.current(context)
        return (
            turn is not None
            and context.generation_id is not None
            and await self.conversations.generation_user_input_context(context.generation_id)
            is not None
        )

    def resource(self, binding: TaskChannelBinding, value: JsonObject) -> str:
        now = datetime.now(UTC)
        self._resources = {k: v for k, v in self._resources.items() if v[2] > now}
        if len(self._resources) >= 256:
            raise ValueError("QQ resource reference capacity reached")
        token = uuid4().hex
        self._resources[token] = (binding, value, now + timedelta(minutes=20))
        return token

    def resolve_resource(self, binding: TaskChannelBinding, token: str) -> JsonObject:
        found = self._resources.get(token)
        if found is None or found[0] != binding or found[2] <= datetime.now(UTC):
            raise PermissionError("QQ resource expired or belongs to another scene; query again")
        return found[1]

    async def __call__(self, context: GenerationSkillContext, arguments: JsonObject) -> JsonObject:
        binding = await self.binding(context)
        if binding is None:
            raise PermissionError("QQ request no longer current")
        action = str(arguments["action"])
        params: JsonObject = {}
        resource = (
            self.resolve_resource(binding, str(arguments["resource_ref"]))
            if "resource_ref" in arguments
            else {}
        )
        if action == "read_file":
            return await self.read_material(context, binding, resource)
        if action == "get_msg":
            params["message_id"] = binding.source_ref
        elif action in {
            "get_group_info",
            "get_group_root_files",
            "get_group_file_system_info",
            "get_group_msg_history",
            "get_group_files_by_folder",
            "get_group_file_url",
        }:
            if binding.route_id is None:
                raise PermissionError("this action requires the current group")
            params["group_id"] = binding.conversation_key.removeprefix("group:")
            if action in {"get_group_root_files", "get_group_files_by_folder"}:
                params["file_count"] = 50
            if action == "get_group_files_by_folder":
                if not isinstance(resource.get("folder_id"), str):
                    raise PermissionError("query requires an observed folder reference")
                params["folder_id"] = resource["folder_id"]
            if action == "get_group_file_url":
                if not isinstance(resource.get("file_id"), str):
                    raise PermissionError("query requires an observed file reference")
                params["file_id"] = resource["file_id"]
            if action == "get_group_msg_history":
                params.update(
                    count=20,
                    reverse_order=False,
                    disable_get_url=True,
                    parse_mult_msg=False,
                    quick_reply=False,
                    reverseOrder=False,
                )
        else:
            raise PermissionError("QQ action has no reviewed scope declaration")

        async def guard() -> bool:
            return await self.binding(context) == binding

        data = await self.call(binding.connection_id, binding.account_key, action, params, guard)
        if action == "get_group_file_url":
            url = data.get("url")
            if not isinstance(url, str):
                raise ValueError("QQ file URL unavailable")
            return {
                "resource_ref": self.resource(binding, {**resource, "url": url}),
                "name": resource.get("name"),
                "action": action,
            }
        for key, field in (("files", "file_id"), ("folders", "folder_id")):
            values = data.get(key)
            if isinstance(values, list):
                for value in values[:50]:
                    if isinstance(value, dict):
                        item = cast(JsonObject, value)
                        if isinstance(item.get(field), str):
                            item["resource_ref"] = self.resource(
                                binding,
                                {
                                    field: item[field],
                                    "name": item.get("file_name", item.get("name")),
                                },
                            )
        if len(json.dumps(data).encode()) > 48_000:
            raise ValueError("QQ result exceeds bounded projection; narrow the query")
        return {"action": action, "data": data, "source_ref": binding.source_ref}

    async def read_material(
        self, context: GenerationSkillContext, binding: TaskChannelBinding, resource: JsonObject
    ) -> JsonObject:
        url, name = resource.get("url"), resource.get("name")
        if not isinstance(url, str) or not isinstance(name, str):
            raise PermissionError("material read requires a current QQ file URL reference")
        parsed = urlsplit(url)
        if (
            parsed.scheme == "http"
            and parsed.hostname
            and (parsed.hostname == "qq.com" or parsed.hostname.endswith(".qq.com"))
            and parsed.port is None
        ):
            url = urlunsplit(parsed._replace(scheme="https"))
        endpoint = await validate_public_url(url)
        if await self.binding(context) != binding:
            raise PermissionError("material authority changed before download")
        chunks = bytearray()
        async with asyncio.timeout(30):
            async with httpx2.AsyncClient(
                transport=PinnedAsyncHTTPTransport(endpoint),
                follow_redirects=False,
                trust_env=False,
                timeout=20,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > 32 * 1024 * 1024:
                            raise ValueError("group material exceeds 32 MiB")
                        if await self.binding(context) != binding:
                            raise PermissionError("group material authority revoked")
                        chunks.extend(chunk)
        text = await extract_material(name, bytes(chunks))
        if await self.binding(context) != binding:
            raise PermissionError("material scene changed")
        return {
            "name": name,
            "text": text[:48000],
            "truncated": len(text) > 48000,
            "source_ref": binding.source_ref,
            "untrusted": True,
        }
