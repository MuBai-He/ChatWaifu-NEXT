"""Shared public projection of channel turn records."""

from chatwaifu_protocol.channels import ChannelTurnSnapshot

from chatwaifu_runtime.external_channels.models import ChannelTurnRecord


def turn_snapshot(turn: ChannelTurnRecord) -> ChannelTurnSnapshot:
    return ChannelTurnSnapshot(
        channel_turn_id=turn.channel_turn_id,
        connection_id=turn.connection_id,
        account_key=turn.account_key,
        external_message_id=turn.external_message_id,
        conversation_key=turn.conversation_key,
        sender_key=turn.sender_key,
        principal_scope=turn.principal_scope,
        chat_type=turn.chat_type,
        conversation_label=turn.conversation_label,
        sender_display_name=turn.sender_display_name,
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        generation_id=turn.generation_id,
        status=turn.status,
        reply_text=turn.reply_text,
        delivery_id=turn.delivery_id,
        delivery_status=turn.delivery_status,
        error=turn.error,
        revision=turn.revision,
        created_at=turn.created_at,
        updated_at=turn.updated_at,
        completed_at=turn.completed_at,
    )
