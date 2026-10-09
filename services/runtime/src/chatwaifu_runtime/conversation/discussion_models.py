"""Versioned, untrusted ephemeral evidence; never a user turn or memory source."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from chatwaifu_runtime.config.group_discussion import GroupDiscussionConfig


@dataclass(frozen=True, slots=True)
class DiscussionMessage:
    message_id: str
    participant_id: str
    received_at: datetime
    expires_at: datetime
    text: str = field(repr=False)
    omitted_characters: int = 0


@dataclass(frozen=True, slots=True)
class GroupDiscussionContext:
    connection_id: UUID
    account_key: str
    group_id: str
    route_id: UUID
    route_revision: int
    scene_id: str
    audience_ids: tuple[str, ...]
    messages: tuple[DiscussionMessage, ...] = field(repr=False)
    policy: GroupDiscussionConfig
    version: int = 1
    mention_only: bool = False

    def __post_init__(self) -> None:
        if type(self.mention_only) is not bool:
            raise ValueError("mention-only trigger must be boolean")
        if self.version != 1 or not self.scene_id or not 2 <= len(self.audience_ids) <= 2000:
            raise ValueError("unsupported discussion scope")
        if len(self.messages) > self.policy.cache_messages:
            raise ValueError("discussion snapshot exceeds message capacity")
        if sum(len(message.text) for message in self.messages) > self.policy.cache_characters:
            raise ValueError("discussion snapshot exceeds character capacity")
        if len(set(self.audience_ids)) != len(self.audience_ids):
            raise ValueError("duplicate discussion audience")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("duplicate discussion message identity")
        for message in self.messages:
            if (
                message.participant_id not in self.audience_ids
                or not 0 < len(message.text) <= self.policy.message_characters
                or message.received_at.utcoffset() is None
                or message.expires_at.utcoffset() is None
                or message.expires_at <= message.received_at
            ):
                raise ValueError("discussion message outside authorized scope")
