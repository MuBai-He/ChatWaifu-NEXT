"""Unresolved provider group identities; these records never grant Runtime scope."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject

if TYPE_CHECKING:
    from .messages import NapCatImageReference

GroupNoticeType = Literal["group_increase", "group_decrease", "group_admin"]
GroupNoticeSubtype = Literal["approve", "invite", "leave", "kick", "kick_me", "set", "unset"]
GROUP_NOTICE_TYPES = frozenset({"group_increase", "group_decrease", "group_admin"})
_NOTICE_SUBTYPES = {
    "group_increase": frozenset({"approve", "invite"}),
    "group_decrease": frozenset({"leave", "kick", "kick_me"}),
    "group_admin": frozenset({"set", "unset"}),
}


def qq_group_identifier(value: object) -> str | None:
    """Require a canonical positive QQ identifier, without bool/float coercion."""
    if type(value) not in {str, int} or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        return None
    return str(value)


@dataclass(frozen=True, slots=True)
class NapCatGroupInboundMessage:
    connection_id: UUID
    account_key: str
    group_id: str
    sender_key: str
    external_message_id: str
    text: str = field(repr=False)
    received_at: datetime
    images: tuple[NapCatImageReference, ...] = field(default=(), repr=False)
    bot_mentioned: bool = True
    mention_only: bool = False


@dataclass(frozen=True, slots=True)
class NapCatGroupMemberList:
    """Observed non-self accounts, not a freshness or atomic-audience guarantee."""

    account_key: str
    group_id: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NapCatGroupMembershipNotice:
    account_key: str
    group_id: str
    user_id: str
    notice_type: GroupNoticeType
    sub_type: GroupNoticeSubtype
    operator_id: str | None = None

    def to_event(self) -> JsonObject:
        event: JsonObject = {
            "post_type": "notice",
            "self_id": self.account_key,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "notice_type": self.notice_type,
            "sub_type": self.sub_type,
        }
        if self.operator_id is not None:
            event["operator_id"] = self.operator_id
        return event


def normalize_group_notice(
    event: JsonObject, *, account: str, group_id: str
) -> NapCatGroupMembershipNotice | None:
    """Parse only supported observations for a fixed account and group."""
    notice_type = event.get("notice_type")
    sub_type = event.get("sub_type")
    user_id = qq_group_identifier(event.get("user_id"))
    if (
        qq_group_identifier(account) != account
        or qq_group_identifier(group_id) != group_id
        or event.get("post_type") != "notice"
        or qq_group_identifier(event.get("self_id")) != account
        or qq_group_identifier(event.get("group_id")) != group_id
        or not isinstance(notice_type, str)
        or notice_type not in _NOTICE_SUBTYPES
        or not isinstance(sub_type, str)
        or sub_type not in _NOTICE_SUBTYPES[notice_type]
        or user_id is None
    ):
        return None
    raw_operator = event.get("operator_id")
    operator = qq_group_identifier(raw_operator)
    # A missing/zero operator is not identity evidence. Joining/leaving must
    # still invalidate the audience even when the provider does not know it.
    if (
        raw_operator is not None
        and operator is None
        and (type(raw_operator) not in {int, str} or str(raw_operator) != "0")
    ):
        return None
    return NapCatGroupMembershipNotice(
        account,
        group_id,
        user_id,
        cast(GroupNoticeType, notice_type),
        cast(GroupNoticeSubtype, sub_type),
        operator,
    )
