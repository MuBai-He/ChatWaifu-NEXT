"""Conversation-owned bounded ephemeral coverage from completed generations."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

from chatwaifu_protocol.character import PromptContextIdentity

from chatwaifu_runtime.agent.source_answer_frame import SourceAnswerFrame, SourceAnswerGap
from chatwaifu_runtime.conversation.source_context import MAX_SOURCE_GENERATIONS


def coverage_context_key(identity: PromptContextIdentity, user_scope: str) -> str:
    route = identity.chat_route
    # Presentation, tool projection and budget are deliberately not identity.
    payload = {
        "character": identity.character_id,
        "package": identity.character_package_hash,
        "principal": user_scope,
        "provider": route.provider,
        "model": route.model,
        "endpoint": route.endpoint_digest,
        "version": "1.0",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _CompletedCoverage:
    context_key: str
    gaps: tuple[SourceAnswerGap, ...]


class SourceAnswerCoverageStore:
    """Caller owns completion/eligibility; no database, bodies, prose parsing or IO."""

    def __init__(self, max_entries: int = 128) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= 128:
            raise ValueError("invalid source coverage store bound")
        self._maximum = max_entries
        self._records: OrderedDict[tuple[UUID, UUID], _CompletedCoverage] = OrderedDict()

    def put_completed(
        self, session_id: UUID, generation_id: UUID, context_key: str, frame: SourceAnswerFrame
    ) -> None:
        key = (session_id, generation_id)
        self._records[key] = _CompletedCoverage(context_key, frame.gaps)
        self._records.move_to_end(key)
        own = [record for record in self._records if record[0] == session_id]
        for record in own[:-MAX_SOURCE_GENERATIONS]:
            del self._records[record]
        while len(self._records) > self._maximum:
            self._records.popitem(last=False)

    def load(
        self, session_id: UUID, eligible: tuple[UUID, ...], context_key: str
    ) -> tuple[SourceAnswerGap, ...]:
        allowed = set(eligible[:MAX_SOURCE_GENERATIONS])
        for key, value in tuple(self._records.items()):
            if key[0] == session_id and (key[1] not in allowed or value.context_key != context_key):
                del self._records[key]
        selected: dict[str, SourceAnswerGap] = {}
        for generation_id in eligible[:MAX_SOURCE_GENERATIONS]:
            record = self._records.get((session_id, generation_id))
            if record is not None:
                for gap in record.gaps:
                    selected.setdefault(gap.gap_id, gap)
        return tuple(selected.values())

    def clear(self, session_id: UUID | None = None) -> None:
        if session_id is None:
            self._records.clear()
        else:
            for key in tuple(self._records):
                if key[0] == session_id:
                    del self._records[key]
