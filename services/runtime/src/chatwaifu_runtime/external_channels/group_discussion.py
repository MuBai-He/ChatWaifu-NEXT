"""Bounded in-memory collection only. No model, transcript, memory or provider I/O."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from chatwaifu_runtime.config.group_discussion import GroupDiscussionConfig
from chatwaifu_runtime.conversation.discussion_models import (
    DiscussionMessage,
    GroupDiscussionContext,
)
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteRecord,
)


@dataclass(frozen=True, slots=True)
class _Scope:
    account_key: str
    connection_revision: int
    route_id: UUID
    route_revision: int
    scene_id: str
    audience_fingerprint: str


@dataclass(slots=True)
class _Cache:
    scope: _Scope
    route: ChannelGroupRouteRecord
    messages: list[DiscussionMessage] = field(default_factory=list[DiscussionMessage])
    rates: dict[str, deque[datetime]] = field(default_factory=dict[str, deque[datetime]])
    # Dedup survives fair-quota eviction but remains bounded and expires.
    seen: dict[str, datetime] = field(default_factory=dict[str, datetime])


class GroupDiscussionCache:
    def __init__(self, policy: GroupDiscussionConfig) -> None:
        self.policy = policy
        self._groups: dict[tuple[UUID, str], _Cache] = {}

    def clear(self, connection_id: UUID | None = None, group_id: str | None = None) -> None:
        for key in tuple(self._groups):
            if (connection_id is None or key[0] == connection_id) and (
                group_id is None or key[1] == group_id
            ):
                self._groups.pop(key, None)

    def clear_route(self, route_id: UUID) -> None:
        for key, cache in tuple(self._groups.items()):
            if cache.scope.route_id == route_id:
                self._groups.pop(key, None)

    def clear_scene(self, scene_id: str) -> None:
        for key, cache in tuple(self._groups.items()):
            if cache.scope.scene_id == scene_id:
                self._groups.pop(key, None)

    def clear_link(self, link_id: UUID) -> None:
        for key, cache in tuple(self._groups.items()):
            if any(member.link_id == link_id for member in cache.route.members):
                self._groups.pop(key, None)

    def _prune(self, now: datetime) -> None:
        for key, cache in tuple(self._groups.items()):
            cache.messages[:] = [m for m in cache.messages if m.expires_at > now]
            cache.seen = {mid: expiry for mid, expiry in cache.seen.items() if expiry > now}
            for times in cache.rates.values():
                while (
                    times
                    and (now - times[0]).total_seconds() >= self.policy.frequency_window_seconds
                ):
                    times.popleft()
            if not cache.messages and not cache.seen and not any(cache.rates.values()):
                self._groups.pop(key, None)

    def _cache(
        self, route: ChannelGroupRouteRecord, connection_revision: int, now: datetime
    ) -> _Cache | None:
        self._prune(now)
        if not self.policy.enabled:
            return None
        key = (route.connection_id, route.group_id)
        scope = _Scope(
            route.account_key,
            connection_revision,
            route.route_id,
            route.revision,
            route.scene_id,
            route.audience_fingerprint,
        )
        existing = self._groups.get(key)
        if existing is not None and existing.scope != scope:
            self._groups.pop(key)
            existing = None
        if existing is None:
            if len(self._groups) >= self.policy.max_groups:
                return None  # Do not evict another authorized group's live context.
            existing = self._groups[key] = _Cache(scope, route)
        return existing

    def observe(
        self,
        descriptor: ChannelGroupInboundDescriptor,
        route: ChannelGroupRouteRecord,
        connection_revision: int,
        now: datetime,
    ) -> str:
        if descriptor.mention_only:
            return "mention_only"
        cache = self._cache(route, connection_revision, now)
        if cache is None:
            return "disabled_or_capacity"
        member = next(m for m in route.members if m.sender_key == descriptor.sender_key)
        if descriptor.external_message_id in cache.seen:
            return "duplicate_id"
        text = descriptor.text[: self.policy.message_characters]
        if any(
            m.participant_id == member.participant_id
            and m.text == text
            and (now - m.received_at).total_seconds() < self.policy.duplicate_window_seconds
            for m in cache.messages
        ):
            return "duplicate_content"
        times = cache.rates.setdefault(member.participant_id, deque())
        if len(times) >= self.policy.member_messages_per_window:
            return "frequency"
        times.append(now)
        count_limit = min(
            self.policy.member_messages, self.policy.cache_messages // len(route.members)
        )
        char_limit = min(
            self.policy.member_characters, self.policy.cache_characters // len(route.members)
        )
        text = text[:char_limit]
        own = [m for m in cache.messages if m.participant_id == member.participant_id]
        while own and (
            len(own) >= count_limit or sum(len(m.text) for m in own) + len(text) > char_limit
        ):
            cache.messages.remove(own.pop(0))
        cache.messages.append(
            DiscussionMessage(
                descriptor.external_message_id,
                member.participant_id,
                now,
                now + timedelta(seconds=self.policy.retention_seconds),
                text,
                len(descriptor.text) - len(text),
            )
        )
        cache.seen[descriptor.external_message_id] = now + timedelta(
            seconds=self.policy.retention_seconds
        )
        while len(cache.seen) > self.policy.cache_messages * 2:
            cache.seen.pop(next(iter(cache.seen)))
        return "collected"

    def snapshot(
        self,
        route: ChannelGroupRouteRecord,
        connection_revision: int,
        current_message_id: str,
        now: datetime,
        *,
        mention_only: bool = False,
    ) -> GroupDiscussionContext | None:
        cache = self._cache(route, connection_revision, now)
        if not mention_only and (cache is None or not cache.messages):
            return None
        return GroupDiscussionContext(
            route.connection_id,
            route.account_key,
            route.group_id,
            route.route_id,
            route.revision,
            route.scene_id,
            tuple(sorted(m.participant_id for m in route.members)),
            tuple(m for m in cache.messages if m.message_id != current_message_id)
            if cache is not None
            else (),
            self.policy,
            mention_only=mention_only,
        )
