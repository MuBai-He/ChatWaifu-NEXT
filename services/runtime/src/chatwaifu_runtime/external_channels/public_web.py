"""Host opt-in for bounded public reads in a current QQ owner conversation."""

from collections.abc import Callable
from uuid import UUID

from chatwaifu_protocol.channels import ChannelChatType, ChannelTurnStatus

from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext


class ChannelPublicWebPolicy:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        active_generation: Callable[[UUID], UUID | None],
        *,
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._active_generation = active_generation
        self._enabled = enabled

    async def __call__(self, context: GenerationSkillContext, skill_id: str) -> bool:
        if (
            not self._enabled
            or skill_id not in {"web.search", "web.read"}
            or context.origin != "agent"
            or context.turn_id is None
            or context.generation_id is None
            or self._active_generation(context.session_id) != context.generation_id
        ):
            return False
        for turn in await self._repository.list_inflight_turns():
            if (
                turn.session_id != context.session_id
                or turn.turn_id != context.turn_id
                or turn.generation_id != context.generation_id
                or turn.chat_type is not ChannelChatType.DIRECT
                or turn.group_route_id is not None
                or turn.status not in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}
            ):
                continue
            connection = await self._repository.get_connection(turn.connection_id)
            binding = await self._repository.find_binding(turn.connection_id, turn.conversation_key)
            if connection is None or binding is None:
                return False
            config = connection.configuration
            return (
                connection.deleted_at is None
                and config.enabled
                and config.provider_id == "qq_napcat"
                and config.account_key == turn.account_key
                and config.principal_scope == turn.principal_scope
                and turn.sender_key in config.allowed_sender_keys
                and binding.binding_id == turn.binding_id
                and binding.session_id == context.session_id
                and binding.sender_key == turn.sender_key
                and binding.chat_type is ChannelChatType.DIRECT
                and binding.group_route_id is None
                and self._active_generation(context.session_id) == context.generation_id
            )
        return False
