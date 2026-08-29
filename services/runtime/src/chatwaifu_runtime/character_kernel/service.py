"""Deterministic Affect/Relationship reducers and semantic response planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.characters.service import CharacterProfile, CharacterService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database

USER_SCOPE = "local"
type RelationshipStage = Literal["acquaintance", "familiar", "trusted", "close"]


@dataclass(frozen=True, slots=True)
class TurnCharacterContext:
    snapshot: CharacterKernelSnapshot
    plan: ResponsePlan


class CharacterKernelService:
    def __init__(
        self,
        database: Database,
        characters: CharacterService,
        publisher: EventPublisher,
    ) -> None:
        self._database = database
        self._characters = characters
        self._publisher = publisher

    async def snapshot(self, character_id: str) -> CharacterKernelSnapshot:
        character = self._require_character(character_id)
        affect_row = await self._database.fetchone(
            "SELECT * FROM character_states WHERE character_id = ? AND user_scope = ?",
            (character_id, USER_SCOPE),
        )
        relationship_row = await self._database.fetchone(
            "SELECT * FROM relationship_states WHERE character_id = ? AND user_scope = ?",
            (character_id, USER_SCOPE),
        )
        now = datetime.now(UTC)
        if affect_row is None or relationship_row is None:
            return await self._initialize(character, now)
        affect = _decay_affect(
            AffectState(
                valence=float(affect_row["valence"]),
                arousal=float(affect_row["arousal"]),
                energy=float(affect_row["energy"]),
                attention=float(affect_row["attention"]),
                embarrassment=float(affect_row["embarrassment"]),
                tension=float(affect_row["tension"]),
                updated_at=datetime.fromisoformat(str(affect_row["updated_at"])),
            ),
            now,
            character,
        )
        relationship = _decay_relationship(
            RelationshipState(
                familiarity=float(relationship_row["familiarity"]),
                trust=float(relationship_row["trust"]),
                affinity=float(relationship_row["affinity"]),
                comfort=float(relationship_row["comfort"]),
                recent_tension=float(relationship_row["recent_tension"]),
                interaction_count=int(relationship_row["interaction_count"]),
                stage=cast(RelationshipStage, str(relationship_row["stage"])),
                preferred_address=relationship_row["preferred_address"],
                updated_at=datetime.fromisoformat(str(relationship_row["updated_at"])),
            ),
            now,
            character,
        )
        return CharacterKernelSnapshot(
            character_id=character_id,
            user_scope=USER_SCOPE,
            revision=max(int(affect_row["revision"]), int(relationship_row["revision"])),
            affect=affect,
            relationship=relationship,
        )

    async def observe_user_turn(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        character_id: str,
        text: str,
    ) -> TurnCharacterContext:
        character = self._require_character(character_id)
        previous = await self.snapshot(character_id)
        now = datetime.now(UTC)
        signal = _classify(text)
        affect = _reduce_affect(previous.affect, signal, now)
        relationship = _reduce_relationship(previous.relationship, signal, character, now)
        revision = previous.revision + 1
        await self._persist(character_id, affect, relationship, revision)
        snapshot = CharacterKernelSnapshot(
            character_id=character_id,
            user_scope=USER_SCOPE,
            revision=revision,
            affect=affect,
            relationship=relationship,
        )
        plan = _plan_response(text, signal, snapshot, character)
        await self._emit(
            "character.state_changed",
            session_id,
            turn_id,
            generation_id,
            {"revision": revision, "affect": affect.model_dump(mode="json")},
        )
        await self._emit(
            "relationship.state_changed",
            session_id,
            turn_id,
            generation_id,
            {"revision": revision, "relationship": relationship.model_dump(mode="json")},
        )
        await self._emit(
            "character.response_planned",
            session_id,
            turn_id,
            generation_id,
            {"plan": plan.model_dump(mode="json")},
        )
        return TurnCharacterContext(snapshot=snapshot, plan=plan)

    async def observe_interaction(
        self,
        *,
        session_id: UUID,
        character_id: str,
        kind: Literal["avatar_touch"],
        region: str,
    ) -> CharacterKernelSnapshot:
        previous = await self.snapshot(character_id)
        now = datetime.now(UTC)
        affect = previous.affect.model_copy(
            update={
                "valence": _clamp(previous.affect.valence + 0.015, -1, 1),
                "attention": _clamp(previous.affect.attention + 0.04),
                "embarrassment": _clamp(previous.affect.embarrassment + 0.025),
                "updated_at": now,
            }
        )
        relationship = previous.relationship.model_copy(
            update={
                "familiarity": _clamp(previous.relationship.familiarity + 0.004),
                "comfort": _clamp(previous.relationship.comfort + 0.003),
                "updated_at": now,
            }
        )
        revision = previous.revision + 1
        await self._persist(character_id, affect, relationship, revision)
        snapshot = CharacterKernelSnapshot(
            character_id=character_id,
            user_scope=USER_SCOPE,
            revision=revision,
            affect=affect,
            relationship=relationship,
        )
        await self._emit(
            "avatar.interaction_received",
            session_id,
            None,
            None,
            {"kind": kind, "region": region, "revision": revision},
        )
        await self._emit(
            "character.state_changed",
            session_id,
            None,
            None,
            {"revision": revision, "affect": affect.model_dump(mode="json")},
        )
        await self._emit(
            "relationship.state_changed",
            session_id,
            None,
            None,
            {"revision": revision, "relationship": relationship.model_dump(mode="json")},
        )
        return snapshot

    async def plan_proactive_turn(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        character_id: str,
    ) -> TurnCharacterContext:
        """Plan an ambient turn without treating it as user relationship evidence."""

        snapshot = await self.snapshot(character_id)
        plan = ResponsePlan(
            intent="curious",
            tone="gentle",
            expression="happy" if snapshot.affect.valence >= 0.2 else "neutral",
            motion="stare" if snapshot.relationship.stage != "acquaintance" else None,
            response_length="short",
            rationale="policy-approved proactive check-in",
        )
        await self._emit(
            "character.response_planned",
            session_id,
            turn_id,
            generation_id,
            {"plan": plan.model_dump(mode="json"), "trigger": "proactive"},
        )
        return TurnCharacterContext(snapshot=snapshot, plan=plan)

    async def clear_all(self) -> int:
        async with self._database.transaction() as connection:
            affect = await connection.execute("DELETE FROM character_states")
            relationship = await connection.execute("DELETE FROM relationship_states")
            changed = max(affect.rowcount, 0) + max(relationship.rowcount, 0)
            await affect.close()
            await relationship.close()
        return changed

    async def clear_scope(self, character_id: str, user_scope: str = USER_SCOPE) -> int:
        """Reset one character/user relationship without affecting other scopes."""

        async with self._database.transaction() as connection:
            affect = await connection.execute(
                "DELETE FROM character_states WHERE character_id = ? AND user_scope = ?",
                (character_id, user_scope),
            )
            relationship = await connection.execute(
                "DELETE FROM relationship_states WHERE character_id = ? AND user_scope = ?",
                (character_id, user_scope),
            )
            changed = max(affect.rowcount, 0) + max(relationship.rowcount, 0)
            await affect.close()
            await relationship.close()
        return changed

    async def _initialize(
        self, character: CharacterProfile, now: datetime
    ) -> CharacterKernelSnapshot:
        initial = character.relationship_policy.get("initial", {})
        affect = AffectState(updated_at=now)
        relationship = RelationshipState(
            familiarity=float(initial.get("familiarity", 0.2)),
            trust=float(initial.get("trust", 0.2)),
            affinity=float(initial.get("affinity", 0.25)),
            comfort=float(initial.get("comfort", 0.2)),
            updated_at=now,
        )
        await self._persist(character.character_id, affect, relationship, 0)
        return CharacterKernelSnapshot(
            character_id=character.character_id,
            user_scope=USER_SCOPE,
            revision=0,
            affect=affect,
            relationship=relationship,
        )

    async def _persist(
        self,
        character_id: str,
        affect: AffectState,
        relationship: RelationshipState,
        revision: int,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO character_states(
                    character_id, user_scope, valence, arousal, energy, attention,
                    embarrassment, tension, revision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id, user_scope) DO UPDATE SET
                    valence=excluded.valence, arousal=excluded.arousal,
                    energy=excluded.energy, attention=excluded.attention,
                    embarrassment=excluded.embarrassment, tension=excluded.tension,
                    revision=excluded.revision, updated_at=excluded.updated_at
                """,
                (
                    character_id,
                    USER_SCOPE,
                    affect.valence,
                    affect.arousal,
                    affect.energy,
                    affect.attention,
                    affect.embarrassment,
                    affect.tension,
                    revision,
                    affect.updated_at.isoformat(),
                ),
            )
            await connection.execute(
                """
                INSERT INTO relationship_states(
                    character_id, user_scope, familiarity, trust, affinity,
                    comfort, recent_tension, interaction_count, stage,
                    preferred_address, revision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id, user_scope) DO UPDATE SET
                    familiarity=excluded.familiarity, trust=excluded.trust,
                    affinity=excluded.affinity, comfort=excluded.comfort,
                    recent_tension=excluded.recent_tension,
                    interaction_count=excluded.interaction_count,
                    stage=excluded.stage, preferred_address=excluded.preferred_address,
                    revision=excluded.revision, updated_at=excluded.updated_at
                """,
                (
                    character_id,
                    USER_SCOPE,
                    relationship.familiarity,
                    relationship.trust,
                    relationship.affinity,
                    relationship.comfort,
                    relationship.recent_tension,
                    relationship.interaction_count,
                    relationship.stage,
                    relationship.preferred_address,
                    revision,
                    relationship.updated_at.isoformat(),
                ),
            )

    async def _emit(
        self,
        event_type: str,
        session_id: UUID,
        turn_id: UUID | None,
        generation_id: UUID | None,
        payload: dict[str, object],
    ) -> None:
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": event_type,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "generation_id": generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.character_kernel",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": payload,
                }
            )
        )

    def _require_character(self, character_id: str) -> CharacterProfile:
        character = self._characters.get(character_id)
        if character is None:
            raise KeyError(f"unknown character {character_id}")
        return character


@dataclass(frozen=True, slots=True)
class _Signal:
    positive: bool = False
    caring: bool = False
    negative: bool = False
    hostile: bool = False
    intimate: bool = False
    embarrassed: bool = False
    surprised: bool = False
    sad: bool = False
    question: bool = False
    sing: bool = False
    gaze: bool = False
    headpat: bool = False


def _classify(text: str) -> _Signal:
    value = text.casefold()

    def has(markers: tuple[str, ...]) -> bool:
        return any(marker in value for marker in markers)

    return _Signal(
        positive=has(("谢谢", "开心", "高兴", "喜欢", "可爱", "thanks", "happy")),
        caring=has(("辛苦", "没事", "陪你", "关心", "休息", "take care")),
        negative=has(("难过", "伤心", "失望", "sad", "upset")),
        hostile=has(("讨厌你", "闭嘴", "滚", "笨蛋", "hate you")),
        intimate=has(("喜欢你", "爱你", "约会", "love you")),
        embarrassed=has(("害羞", "脸红", "不好意思", "embarrass")),
        surprised=has(("居然", "震惊", "吓", "surprise")),
        sad=has(("难过", "伤心", "哭", "sad")),
        question="?" in value
        or "？" in value
        or has(("为什么", "怎么", "什么", "who", "why", "how")),
        sing=has(("唱歌", "唱一首", "sing")),
        gaze=has(("看着我", "看我", "盯着", "stare")),
        headpat=has(("摸摸头", "摸头", "headpat", "pat your head")),
    )


def _reduce_affect(previous: AffectState, signal: _Signal, now: datetime) -> AffectState:
    return AffectState(
        valence=_clamp(
            previous.valence
            + (0.08 if signal.positive else 0)
            - (0.1 if signal.hostile else 0)
            - (0.05 if signal.negative else 0),
            -1,
            1,
        ),
        arousal=_clamp(previous.arousal + (0.08 if signal.surprised or signal.intimate else -0.01)),
        energy=_clamp(previous.energy - 0.005),
        attention=_clamp(previous.attention + (0.03 if signal.question else 0.01)),
        embarrassment=_clamp(
            previous.embarrassment + (0.12 if signal.intimate or signal.embarrassed else -0.02)
        ),
        tension=_clamp(previous.tension + (0.14 if signal.hostile else -0.02)),
        updated_at=now,
    )


def _reduce_relationship(
    previous: RelationshipState,
    signal: _Signal,
    character: CharacterProfile,
    now: datetime,
) -> RelationshipState:
    max_delta = float(character.relationship_policy.get("maximum_turn_delta", 0.08))
    familiarity = _clamp(
        previous.familiarity + min(max_delta, 0.012 + (0.018 if signal.positive else 0))
    )
    trust = _clamp(
        previous.trust
        + min(max_delta, 0.025 if signal.caring else 0.004)
        - (0.06 if signal.hostile else 0)
    )
    affinity = _clamp(
        previous.affinity
        + min(max_delta, 0.025 if signal.positive else 0.004)
        - (0.07 if signal.hostile else 0)
    )
    comfort = _clamp(
        previous.comfort
        + min(max_delta, 0.02 if signal.caring else 0.006)
        - (0.05 if signal.hostile else 0)
    )
    tension = _clamp(previous.recent_tension + (0.12 if signal.hostile else -0.025))
    count = previous.interaction_count + 1
    return RelationshipState(
        familiarity=familiarity,
        trust=trust,
        affinity=affinity,
        comfort=comfort,
        recent_tension=tension,
        interaction_count=count,
        stage=_relationship_stage(count, familiarity, trust, affinity, character),
        preferred_address=previous.preferred_address,
        updated_at=now,
    )


def _relationship_stage(
    count: int,
    familiarity: float,
    trust: float,
    affinity: float,
    character: CharacterProfile,
) -> RelationshipStage:
    stages = character.relationship_policy.get("stages", {})
    close = stages.get("close", {})
    if (
        count >= int(close.get("minimum_interactions", 30))
        and familiarity >= float(close.get("familiarity", 0.75))
        and trust >= float(close.get("trust", 0.72))
        and affinity >= float(close.get("affinity", 0.72))
    ):
        return "close"
    trusted = stages.get("trusted", {})
    if (
        count >= int(trusted.get("minimum_interactions", 12))
        and familiarity >= float(trusted.get("familiarity", 0.55))
        and trust >= float(trusted.get("trust", 0.52))
    ):
        return "trusted"
    familiar = stages.get("familiar", {})
    if count >= int(familiar.get("minimum_interactions", 4)) and familiarity >= float(
        familiar.get("familiarity", 0.32)
    ):
        return "familiar"
    return "acquaintance"


def _plan_response(
    text: str,
    signal: _Signal,
    snapshot: CharacterKernelSnapshot,
    character: CharacterProfile,
) -> ResponsePlan:
    del character
    if signal.sing:
        return ResponsePlan(
            intent="celebrate",
            tone="bright",
            expression="happy",
            motion="sing",
            rationale="user requested singing",
        )
    if signal.headpat:
        return ResponsePlan(
            intent="reassure",
            tone="shy",
            expression="shy",
            motion="headpat",
            response_length="short",
            rationale="explicit affectionate headpat interaction",
        )
    if signal.intimate:
        return ResponsePlan(
            intent="reassure",
            tone="shy",
            expression="shy",
            motion="flustered",
            response_length="short",
            rationale="affectionate interaction with paced relationship",
        )
    if signal.sad or signal.negative:
        return ResponsePlan(
            intent="comfort",
            tone="concerned",
            expression="sad",
            motion="stare",
            rationale="user expressed distress",
        )
    if signal.hostile:
        return ResponsePlan(
            intent="reassure",
            tone="serious",
            expression="angry",
            rationale="protect relationship boundary",
        )
    if signal.surprised:
        return ResponsePlan(
            intent="answer",
            tone="bright",
            expression="surprised",
            rationale="surprising user signal",
        )
    if signal.gaze:
        return ResponsePlan(
            intent="tease",
            tone="playful",
            expression="shy",
            motion="stare",
            rationale="explicit gaze interaction",
        )
    if signal.question:
        return ResponsePlan(
            intent="curious",
            tone="gentle",
            expression="curious",
            motion="stare" if snapshot.relationship.stage != "acquaintance" else None,
            rationale="answering a question attentively",
        )
    expression = "happy" if snapshot.affect.valence >= 0.25 else "neutral"
    tone = "playful" if snapshot.relationship.stage in {"trusted", "close"} else "gentle"
    return ResponsePlan(
        intent="answer",
        tone=tone,
        expression=expression,
        rationale=f"default plan for relationship stage {snapshot.relationship.stage}",
    )


def _decay_affect(state: AffectState, now: datetime, character: CharacterProfile) -> AffectState:
    elapsed_hours = max(0.0, (now - state.updated_at).total_seconds() / 3600)
    half_life = float(
        character.relationship_policy.get("decay", {}).get("affect_half_life_hours", 6)
    )
    factor = math.pow(0.5, elapsed_hours / max(half_life, 0.1))
    return AffectState(
        valence=0.15 + (state.valence - 0.15) * factor,
        arousal=0.25 + (state.arousal - 0.25) * factor,
        energy=0.65 + (state.energy - 0.65) * factor,
        attention=0.7 + (state.attention - 0.7) * factor,
        embarrassment=0.1 + (state.embarrassment - 0.1) * factor,
        tension=0.05 + (state.tension - 0.05) * factor,
        updated_at=now,
    )


def _decay_relationship(
    state: RelationshipState, now: datetime, character: CharacterProfile
) -> RelationshipState:
    elapsed_hours = max(0.0, (now - state.updated_at).total_seconds() / 3600)
    half_life = float(
        character.relationship_policy.get("decay", {}).get("tension_half_life_hours", 18)
    )
    factor = math.pow(0.5, elapsed_hours / max(half_life, 0.1))
    return state.model_copy(
        update={"recent_tension": state.recent_tension * factor, "updated_at": now}
    )


def _clamp(value: float, low: float = 0, high: float = 1) -> float:
    return max(low, min(high, value))
