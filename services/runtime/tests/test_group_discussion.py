"""Volatile context, extractive provenance and complete-wire budgets."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast
from uuid import uuid4

import pytest
from chatwaifu_runtime.config.group_discussion import GroupDiscussionConfig
from chatwaifu_runtime.conversation.discussion_models import (
    DiscussionMessage,
    GroupDiscussionContext,
)
from chatwaifu_runtime.conversation.group_discussion import project_group_discussion
from chatwaifu_runtime.external_channels.group_discussion import GroupDiscussionCache
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteMember,
    ChannelGroupRouteRecord,
)
from chatwaifu_runtime.providers.contracts import LlmInputBudget, LlmRequest
from chatwaifu_runtime.providers.input_estimation import estimate_reference_input_tokens
from chatwaifu_runtime.providers.model_config import ModelConfigurationService, ModelRoleConfig
from chatwaifu_runtime.providers.openai_compatible import build_messages


def _route() -> ChannelGroupRouteRecord:
    now = datetime.now(UTC)
    return ChannelGroupRouteRecord(
        uuid4(),
        uuid4(),
        "999",
        "500",
        "default",
        "scene-fixture",
        "fixture",
        1,
        True,
        None,
        uuid4(),
        (
            ChannelGroupRouteMember(uuid4(), "111", "alice", True),
            ChannelGroupRouteMember(uuid4(), "222", "bob", False),
        ),
        now,
        now,
    )


def _message(
    route: ChannelGroupRouteRecord, mid: int, text: str, now: datetime, sender: str = "111"
) -> ChannelGroupInboundDescriptor:
    return ChannelGroupInboundDescriptor(
        route.connection_id, route.account_key, route.group_id, sender, str(mid), text, now
    )


def test_collection_fairness_frequency_duplicates_ttl_and_scope() -> None:
    route = _route()
    now = datetime.now(UTC)
    cache = GroupDiscussionCache(GroupDiscussionConfig())
    assert (
        cache.observe(_message(route, 1, "不同意，预算有限", now, "222"), route, 1, now)
        == "collected"
    )
    long = "长" * 20000
    assert cache.observe(_message(route, 2, long, now), route, 1, now) == "collected"
    snapshot = cache.snapshot(route, 1, "99", now)
    assert snapshot is not None and snapshot.messages[-1].omitted_characters == 19200
    for mid in range(3, 8):
        assert cache.observe(_message(route, mid, str(mid), now), route, 1, now) == "collected"
    assert cache.observe(_message(route, 8, "flood", now), route, 1, now) == "frequency"
    assert cache.observe(_message(route, 2, "changed id", now), route, 1, now) == "duplicate_id"
    assert cache.observe(_message(route, 9, long, now), route, 1, now) == "duplicate_content"
    moment = now
    for mid in range(10, 150):
        moment = now + timedelta(seconds=mid * 2)
        cache.observe(_message(route, mid, str(mid) * 350, moment), route, 1, moment)
    snapshot = cache.snapshot(route, 1, "999", moment)
    assert snapshot is not None
    assert any(m.participant_id == "bob" and m.message_id == "1" for m in snapshot.messages)
    assert sum(len(m.text) for m in snapshot.messages) <= 24000
    assert sum(len(m.text) for m in snapshot.messages if m.participant_id == "alice") <= 6000
    assert len(snapshot.messages) <= 96
    assert len(cache._groups[(route.connection_id, route.group_id)].seen) <= 192
    assert cache.snapshot(route, 1, "99", now + timedelta(seconds=1201)) is None


@pytest.mark.parametrize("scope_change", ["connection", "account", "route", "scene", "audience"])
def test_scope_change_never_reuses_old_material(scope_change: str) -> None:
    route = _route()
    now = datetime.now(UTC)
    cache = GroupDiscussionCache(GroupDiscussionConfig())
    cache.observe(_message(route, 1, "old audience sentinel", now), route, 1, now)
    changed = route
    connection_revision = 1
    if scope_change == "connection":
        connection_revision = 2
    elif scope_change == "account":
        changed = replace(route, account_key="888")
    elif scope_change == "route":
        changed = replace(route, revision=2)
    elif scope_change == "scene":
        changed = replace(route, scene_id="new-scene")
    else:
        changed = replace(
            route, members=(route.members[0], replace(route.members[1], participant_id="new"))
        )
    assert cache.snapshot(changed, connection_revision, "99", now) is None


@pytest.mark.parametrize("fence", ["connection", "group", "route", "scene", "link"])
def test_lifecycle_fences_clear_raw_material(fence: str) -> None:
    route = _route()
    now = datetime.now(UTC)
    cache = GroupDiscussionCache(GroupDiscussionConfig())
    cache.observe(_message(route, 1, "old", now), route, 1, now)
    if fence == "connection":
        cache.clear(route.connection_id)
    elif fence == "group":
        cache.clear(route.connection_id, route.group_id)
    elif fence == "route":
        cache.clear_route(route.route_id)
    elif fence == "scene":
        cache.clear_scene(route.scene_id)
    else:
        cache.clear_link(route.members[0].link_id)
    assert not cache._groups


def _context(
    *, long: bool = False, policy: GroupDiscussionConfig | None = None
) -> GroupDiscussionContext:
    route = _route()
    now = datetime.now(UTC)
    lines = [
        ("alice", "方案 A 每月 20 元，快，但会上传数据"),
        ("bob", "我反对上传数据，方案 B 免费且本地，谁负责维护还没回答"),
    ]
    if long:
        lines += [
            ("alice" if i % 2 == 0 else "bob", f"讨论补充 {i}: " + "ordinary discussion " * 10)
            for i in range(30)
        ]
    lines += [("bob", "刚才的 A 和 B，你怎么看这个选择？")]
    return GroupDiscussionContext(
        route.connection_id,
        route.account_key,
        route.group_id,
        route.route_id,
        route.revision,
        route.scene_id,
        ("alice", "bob"),
        tuple(
            DiscussionMessage(
                str(i + 1),
                speaker,
                now + timedelta(milliseconds=i),
                now + timedelta(seconds=900),
                text,
            )
            for i, (speaker, text) in enumerate(lines)
        ),
        policy or GroupDiscussionConfig(summary_input_tokens=8192),
    )


class _Summary:
    def __init__(self, mode: str = "valid") -> None:
        self.calls: list[tuple[str, str, ModelRoleConfig]] = []
        self.mode = mode

    async def complete(self, _role: str, system: str, user: str, *, config: ModelRoleConfig) -> str:
        self.calls.append((system, user, config))
        if self.mode == "error":
            raise RuntimeError("fixture failure")
        if self.mode == "timeout":
            await asyncio.Event().wait()
        if self.mode == "cancel":
            raise asyncio.CancelledError()
        if self.mode == "unknown":
            return '{"message_ids":["private-secret"]}'
        if self.mode == "one_speaker":
            return '{"message_ids":["1"]}'
        return '{"message_ids":["2","1"]}'  # Runtime retains actual chronological order


def _request(limit: int = 8192) -> LlmRequest:
    return LlmRequest(
        generation_id=uuid4(),
        user_text="你怎么看？",
        system_prompt="Rules stay authoritative.",
        input_budget=LlmInputBudget(limit),
    )


class _Record(TypedDict):
    message_id: str
    participant_id: str
    text: str


class _Packet(TypedDict):
    recent_originals: list[_Record]
    older_extractive_summary: list[_Record]


def _packet(request: LlmRequest) -> _Packet:
    return cast(_Packet, json.loads(request.current_turn_evidence[-1]))


async def test_short_discussion_keeps_speakers_refs_without_summary_call() -> None:
    summary = _Summary()
    request = _request()
    result = await project_group_discussion(
        request,
        _context(),
        cast(ModelConfigurationService, summary),
        ModelRoleConfig(
            role="memory_summary",
            provider="demo",
            model="fixture",
            updated_at=datetime.now(UTC),
            context_window=32768,
        ),
    )
    assert (
        not summary.calls
        and result.user_text == request.user_text
        and result.history == request.history
    )
    assert result.context == request.context
    assert [m["participant_id"] for m in _packet(result)["recent_originals"]] == [
        "alice",
        "bob",
        "bob",
    ]
    assert estimate_reference_input_tokens(result) <= 8192


async def test_current_discussion_follows_old_photo_history_and_precedes_current_question() -> None:
    request = replace(
        _request(),
        history=(
            ("user", "你看看图片"),
            ("assistant", "明明连图片都没有发……"),
            ("user", "你看看"),
            ("assistant", "可这里真的什么都没有啊……"),
        ),
        pre_user_system_prompt="Trusted text-only output policy.",
    )
    result = await project_group_discussion(
        request,
        _context(),
        cast(ModelConfigurationService, _Summary()),
        ModelRoleConfig(
            role="memory_summary", provider="demo", model="fixture", updated_at=datetime.now(UTC)
        ),
    )
    wire = build_messages(result)
    assert result.history == request.history and result.context == request.context
    assert wire[-3] == {"role": "user", "content": result.current_turn_evidence[0]}
    assert wire[-2] == {"role": "system", "content": request.pre_user_system_prompt}
    assert wire[-1] == {"role": "user", "content": "你怎么看？"}
    assert "可这里真的什么都没有" in str(wire[-4]["content"])
    assert "反对上传数据" in str(wire[-3]["content"])


@pytest.mark.parametrize("available", [True, False])
async def test_bare_mention_uses_only_available_current_discussion(available: bool) -> None:
    context = replace(_context(), mention_only=True)
    if not available:
        context = replace(context, messages=())
    request = replace(_request(), user_text="[仅 @ 角色]")
    result = await project_group_discussion(
        request,
        context,
        cast(ModelConfigurationService, _Summary()),
        ModelRoleConfig(
            role="memory_summary", provider="demo", model="fixture", updated_at=datetime.now(UTC)
        ),
    )
    if available:
        assert "接着群里最近的话题" in result.user_text
        assert result.current_turn_evidence
    else:
        assert "没有可用的群聊讨论" in result.user_text and "问我想聊什么" in result.user_text
        assert not result.current_turn_evidence
    assert request.user_text == "[仅 @ 角色]"  # projection never changes the formal turn


@pytest.mark.parametrize("missing", ["expired", "no_headroom", "no_input_budget"])
async def test_bare_mention_without_admitted_evidence_never_guesses_from_old_history(
    missing: str,
) -> None:
    context = replace(_context(), mention_only=True)
    request = replace(_request(), user_text="[仅 @ 角色]", history=(("assistant", "没有图片"),))
    if missing == "expired":
        now = datetime.now(UTC)
        context = replace(
            context,
            messages=tuple(
                replace(
                    m,
                    received_at=now - timedelta(minutes=20),
                    expires_at=now - timedelta(minutes=5),
                )
                for m in context.messages
            ),
        )
    elif missing == "no_headroom":
        request = replace(
            request, input_budget=LlmInputBudget(estimate_reference_input_tokens(request))
        )
    else:
        request = replace(request, input_budget=None)
    summary = _Summary()
    result = await project_group_discussion(
        request,
        context,
        cast(ModelConfigurationService, summary),
        ModelRoleConfig(
            role="memory_summary", provider="demo", model="fixture", updated_at=datetime.now(UTC)
        ),
    )
    assert not result.current_turn_evidence and not summary.calls
    assert "没有可用的群聊讨论" in result.user_text and "问我想聊什么" in result.user_text


def test_bare_mention_does_not_become_cached_dialogue_or_collide_with_literal_marker() -> None:
    from chatwaifu_runtime.external_channels.group_models import GROUP_MENTION_ONLY_TEXT

    route = _route()
    now = datetime.now(UTC)
    literal = _message(route, 500, GROUP_MENTION_ONLY_TEXT, now)
    bare = replace(literal, mention_only=True)
    assert literal.content_sha256 != bare.content_sha256
    cache = GroupDiscussionCache(GroupDiscussionConfig())
    assert cache.observe(bare, route, 1, now) == "mention_only"
    assert not cache._groups


def test_disabled_listening_still_preserves_bare_mention_trigger_without_any_evidence() -> None:
    route = _route()
    cache = GroupDiscussionCache(GroupDiscussionConfig(enabled=False))
    context = cache.snapshot(route, 1, "600", datetime.now(UTC), mention_only=True)
    assert context is not None and context.mention_only and not context.messages
    assert not cache._groups


async def test_compression_quotes_facts_disagreement_and_open_question_with_traceability() -> None:
    summary = _Summary()
    context = _context(long=True)
    result = await project_group_discussion(
        _request(),
        context,
        cast(ModelConfigurationService, summary),
        ModelRoleConfig(
            role="memory_summary",
            provider="demo",
            model="fixture",
            updated_at=datetime.now(UTC),
            context_window=32768,
        ),
    )
    assert len(summary.calls) == 1
    packet = _packet(result)
    selected = packet["older_extractive_summary"]
    assert [m["message_id"] for m in selected] == ["1", "2"]
    assert [m["participant_id"] for m in selected] == ["alice", "bob"]
    assert "20 元" in selected[0]["text"] and "反对" in selected[1]["text"]
    assert "谁负责维护还没回答" in selected[1]["text"]
    assert packet["recent_originals"][-1]["message_id"] == "33"
    assert summary.calls[0][2].budget.max_output_tokens == 256
    assert (
        estimate_reference_input_tokens(result) - estimate_reference_input_tokens(_request())
        <= 1536
    )


@pytest.mark.parametrize("mode", ["error", "timeout", "unknown", "one_speaker"])
async def test_summary_failure_degrades_to_budgeted_recent_originals(mode: str) -> None:
    summary = _Summary(mode)
    context = _context(
        long=True,
        policy=GroupDiscussionConfig(summary_input_tokens=8192, summary_timeout_seconds=0.1),
    )
    result = await project_group_discussion(
        _request(),
        context,
        cast(ModelConfigurationService, summary),
        ModelRoleConfig(
            role="memory_summary",
            provider="demo",
            model="fixture",
            updated_at=datetime.now(UTC),
            context_window=32768,
        ),
    )
    assert len(summary.calls) == 1
    assert not _packet(result)["older_extractive_summary"]
    assert _packet(result)["recent_originals"][-1]["message_id"] == "33"
    assert estimate_reference_input_tokens(result) <= 8192


async def test_cancellation_is_not_swallowed_by_summary_fallback() -> None:
    with pytest.raises(asyncio.CancelledError):
        await project_group_discussion(
            _request(),
            _context(long=True),
            cast(ModelConfigurationService, _Summary("cancel")),
            ModelRoleConfig(
                role="memory_summary",
                provider="demo",
                model="fixture",
                updated_at=datetime.now(UTC),
                context_window=32768,
            ),
        )


async def test_no_headroom_no_calls_and_expired_no_evidence() -> None:
    summary = _Summary()
    request = replace(_request(1024), system_prompt="mandatory " * 1000)
    result = await project_group_discussion(
        request,
        _context(long=True),
        cast(ModelConfigurationService, summary),
        ModelRoleConfig(
            role="memory_summary",
            provider="demo",
            model="fixture",
            updated_at=datetime.now(UTC),
            context_window=32768,
        ),
    )
    assert result is request and not summary.calls
    expired = replace(
        _context(),
        messages=tuple(
            replace(
                m,
                received_at=m.received_at - timedelta(days=1),
                expires_at=m.expires_at - timedelta(days=1),
            )
            for m in _context().messages
        ),
    )
    fresh = _request()
    assert (
        await project_group_discussion(
            fresh,
            expired,
            cast(ModelConfigurationService, summary),
            ModelRoleConfig(
                role="memory_summary",
                provider="demo",
                model="fixture",
                updated_at=datetime.now(UTC),
                context_window=32768,
            ),
        )
        is fresh
    )


@pytest.mark.parametrize("reserve", [0, 64])
async def test_summary_respects_its_own_output_reservation_and_provider_limit(reserve: int) -> None:
    from chatwaifu_protocol.character import ModelContextBudget

    summary = _Summary()
    config = ModelRoleConfig(
        role="memory_summary",
        provider="demo",
        model="fixture",
        context_window=32768,
        updated_at=datetime.now(UTC),
        budget=ModelContextBudget(output_reserve_tokens=reserve, output_token_limit=64),
    )
    result = await project_group_discussion(
        _request(), _context(long=True), cast(ModelConfigurationService, summary), config
    )
    if reserve == 0:
        assert not summary.calls and not _packet(result)["older_extractive_summary"]
    else:
        assert len(summary.calls) == 1 and summary.calls[0][2].budget.max_output_tokens == 64
