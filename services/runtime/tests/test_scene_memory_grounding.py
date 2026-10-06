"""Scene memory grounding probes use real SQLite and a schema-valid fake LLM."""

# pyright: reportPrivateUsage=false
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.memory import MemoryProposal, MemoryRecordDraft
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.memory.inference import LlmMemoryCandidateExtractor
from chatwaifu_runtime.memory.service import deserialize_candidates, serialize_candidates
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

from services.runtime.tests.test_scene_memory_subjects import _members, _observe, _source


@pytest.fixture
async def runtime(runtime_settings: Settings) -> AsyncIterator[RuntimeContainer]:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


class Models:
    def __init__(self, memories: list[dict[str, object]]) -> None:
        self.memories = memories
        self.calls = 0

    def get(self, role: str) -> SimpleNamespace:
        assert role == "memory_extraction"
        return SimpleNamespace(enabled=True, provider="openai_compatible")

    async def complete(self, role: str, system: str, user: str) -> str:
        assert role == "memory_extraction"
        self.calls += 1
        return json.dumps({"memories": self.memories})


def _candidate(text: str, *, predicate: str | None = None) -> dict[str, object]:
    return {
        "kind": "semantic.fact",
        "subject_id": "user",
        "predicate": predicate,
        "value": text,
        "text": text,
        "confidence": 0.95,
        "importance": 0.8,
        "sensitivity": "private",
        "rationale": "fake LLM extracted fact",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,first",
    [
        ("我喜欢蓝色，Bob喜欢红色", "我喜欢蓝色"),
        ("我叫Alice。Bob喜欢红色", "我叫Alice"),
    ],
)
async def test_mixed_source_does_not_lend_first_person_subject_to_other_candidate(
    runtime: RuntimeContainer,
    source: str,
    first: str,
) -> None:
    alice, bob = await _members(runtime)
    models = Models([_candidate(first), _candidate("Bob喜欢红色")])
    runtime.memory._inference = LlmMemoryCandidateExtractor(cast(ModelConfigurationService, models))
    await _observe(runtime, alice, source)
    assert models.calls == 1
    proposals = await runtime.memory.list_proposals(status="pending", session_id=alice.session_id)
    foreign = next(
        item
        for item in proposals
        if item.candidate is not None and item.candidate.text == "Bob喜欢红色"
    )
    assert foreign.candidate is not None and foreign.candidate.subject_id is None
    own = next(
        item for item in proposals if item.candidate is not None and item.candidate.text == first
    )
    assert own.candidate is not None
    assert own.candidate.subject_id == f"participant:{alice.participant_id}"
    assert await runtime.memory.list(session_id=bob.session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,own_texts,foreign_text",
    [
        ("请记住我喜欢蓝色，Bob喜欢红色", {"我喜欢蓝色"}, "Bob喜欢红色"),
        ("请记住我叫Alice。我喜欢蓝色。Bob喜欢红色", {"我叫Alice", "我喜欢蓝色"}, "Bob喜欢红色"),
        (
            "please remember I am Alice. I like blue. Bob likes red",
            {"I am Alice", "I like blue"},
            "Bob likes red",
        ),
    ],
)
async def test_explicit_mixed_source_preserves_only_grounded_self_fragment(
    runtime: RuntimeContainer,
    source: str,
    own_texts: set[str],
    foreign_text: str,
) -> None:
    alice, _bob = await _members(runtime)
    await _observe(runtime, alice, source)
    active = await runtime.memory.list(session_id=alice.session_id)
    assert {item.text for item in active} == own_texts
    assert all(item.subject_id == f"participant:{alice.participant_id}" for item in active)
    pending = await runtime.memory.list_proposals(status="pending", session_id=alice.session_id)
    assert len(pending) == 1 and pending[0].candidate is not None
    assert pending[0].candidate.text == foreign_text
    assert pending[0].candidate.subject_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["请记住我喜欢蓝色", "please remember I like blue"])
async def test_simple_chinese_and_english_self_fact_remains_automatic(
    runtime: RuntimeContainer,
    text: str,
) -> None:
    alice, _bob = await _members(runtime)
    await _observe(runtime, alice, text)
    active = await runtime.memory.list(session_id=alice.session_id)
    assert len(active) == 1 and active[0].subject_id == f"participant:{alice.participant_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,claim",
    [
        ("我喜欢蓝色，Bob说\uff1a我喜欢红色", "我喜欢红色"),
        ("我喜欢蓝色，Bob说\n我喜欢红色", "我喜欢红色"),
        ("我叫Alice。Bob说\u2018我喜欢红色\u2019", "我喜欢红色"),
        ("I am Alice. Bob likes red.", "Bob likes red"),
        ("我喜欢蓝色，Bob喜欢红色", "我喜欢红色"),
        ("我喜欢蓝色 Bob喜欢红色", "我喜欢蓝色 Bob喜欢红色"),
        ("I am Alice Bob likes red", "I am Alice Bob likes red"),
    ],
)
async def test_reported_or_invented_self_wording_cannot_borrow_another_clause(
    runtime: RuntimeContainer,
    source: str,
    claim: str,
) -> None:
    alice, _bob = await _members(runtime)
    models = Models([_candidate(claim)])
    runtime.memory._inference = LlmMemoryCandidateExtractor(cast(ModelConfigurationService, models))
    await _observe(runtime, alice, source)
    proposals = await runtime.memory.list_proposals(status="pending", session_id=alice.session_id)
    candidate = next(
        item.candidate
        for item in proposals
        if item.candidate is not None and item.candidate.text == claim
    )
    assert candidate.subject_id is None


@pytest.mark.asyncio
async def test_scene_projection_uses_command_from_the_actual_source_event(
    runtime: RuntimeContainer,
) -> None:
    alice, _bob = await _members(runtime)
    event = await _source(runtime, alice, "我喜欢蓝色")
    assert event.turn_id is not None
    await runtime.memory.observe_user_turn(
        alice.session_id,
        event.turn_id,
        event.event_id,
        "default",
        "请记住我喜欢蓝色",
    )
    assert await runtime.memory.list(session_id=alice.session_id) == []
    proposal = (await runtime.memory.list_proposals(status="pending", session_id=alice.session_id))[
        0
    ]
    assert proposal.candidate is not None
    assert proposal.candidate.subject_id == f"participant:{alice.participant_id}"


def _draft(namespace: str, text: str = "我喜欢蓝色", subject: str = "user") -> MemoryRecordDraft:
    return MemoryRecordDraft(
        namespace=namespace,
        kind="semantic.preference",
        subject_id=subject,
        predicate="preference.like.蓝色",
        value=True,
        text=text,
        observed_at=datetime.now(UTC),
        confidence=0.95,
        importance=0.8,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_first", [False, True])
async def test_cross_member_auxiliary_evidence_cannot_select_primary_speaker(
    runtime: RuntimeContainer,
    foreign_first: bool,
) -> None:
    alice, bob = await _members(runtime)
    own = await _source(runtime, alice, "请记住我喜欢蓝色")
    other = await _source(runtime, bob, "请记住我喜欢红色")
    assert own.turn_id is not None
    ids = (other.event_id, own.event_id) if foreign_first else (own.event_id, other.event_id)
    staged = ExtractedMemoryCandidate(
        _draft(f"character/default/user/{alice.user_scope}"),
        explicit=True,
        rationale="untrusted auxiliary evidence",
        evidence_event_ids=ids,
    )
    replay = deserialize_candidates(serialize_candidates([staged]))[0]
    with pytest.raises(ValueError, match="evidence speaker mismatch"):
        await runtime.memory._process_candidate(alice.session_id, own.turn_id, own.event_id, replay)
    assert await runtime.memory.list(session_id=alice.session_id) == []
    assert await runtime.memory.list_proposals(session_id=alice.session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", ["missing_primary", "foreign_auxiliary", "wrong_subject", "foreign_text"]
)
async def test_durable_accepted_retry_rechecks_actual_primary_and_member_evidence(
    runtime: RuntimeContainer,
    bad: str,
) -> None:
    alice, bob = await _members(runtime)
    own = await _source(runtime, alice, "请记住我喜欢蓝色")
    other = await _source(runtime, bob, "请记住我喜欢红色")
    assert own.turn_id is not None
    namespace = f"character/default/user/{alice.user_scope}"
    subject = f"participant:{alice.participant_id}"
    draft = _draft(namespace, subject=subject)
    ids: list[UUID] = [own.event_id]
    if bad == "missing_primary":
        ids = [other.event_id]
    elif bad == "foreign_auxiliary":
        ids = [other.event_id, own.event_id]
    elif bad == "wrong_subject":
        draft = draft.model_copy(update={"subject_id": f"participant:{bob.participant_id}"})
    else:
        draft = draft.model_copy(update={"text": "Bob喜欢红色"})
    proposal = MemoryProposal(
        proposal_id=uuid4(),
        operation="add",
        candidate=draft,
        evidence_event_ids=ids,
        confidence=0.95,
        rationale="staged before crash",
        status="accepted",
        created_at=datetime.now(UTC),
        decided_at=datetime.now(UTC),
    )
    await runtime.memory_repository.save_proposal(proposal)
    memory_id = uuid4()
    legitimate = ExtractedMemoryCandidate(
        _draft(namespace),
        explicit=True,
        rationale="current own source",
    )
    with pytest.raises(ValueError, match=r"trusted source|evidence speaker|grounded self"):
        await runtime.memory._process_candidate(
            alice.session_id,
            own.turn_id,
            own.event_id,
            legitimate,
            proposal_id_override=proposal.proposal_id,
            memory_id_override=memory_id,
        )
    assert await runtime.memory_repository.get(memory_id) is None
    assert await runtime.memory.list(session_id=bob.session_id) == []


@pytest.mark.asyncio
async def test_durable_valid_retry_keeps_grounded_self_subject_and_is_idempotent(
    runtime: RuntimeContainer,
) -> None:
    alice, _bob = await _members(runtime)
    event = await _source(runtime, alice, "请记住我喜欢蓝色")
    assert event.turn_id is not None
    namespace = f"character/default/user/{alice.user_scope}"
    proposal = MemoryProposal(
        proposal_id=uuid4(),
        operation="add",
        candidate=_draft(namespace, subject=f"participant:{alice.participant_id}"),
        evidence_event_ids=[event.event_id],
        confidence=0.95,
        rationale="staged before crash",
        status="accepted",
        created_at=datetime.now(UTC),
        decided_at=datetime.now(UTC),
    )
    await runtime.memory_repository.save_proposal(proposal)
    memory_id = uuid4()
    staged = ExtractedMemoryCandidate(_draft(namespace), explicit=True, rationale="own self fact")
    for _attempt in range(2):
        replay = deserialize_candidates(serialize_candidates([staged]))[0]
        result = await runtime.memory._process_candidate(
            alice.session_id,
            event.turn_id,
            event.event_id,
            replay,
            proposal_id_override=proposal.proposal_id,
            memory_id_override=memory_id,
        )
        assert result.status == "accepted"
    active = await runtime.memory.list(session_id=alice.session_id)
    assert len(active) == 1 and active[0].memory_id == memory_id
    assert active[0].subject_id == f"participant:{alice.participant_id}"
    assert active[0].source_event_ids == [event.event_id]
