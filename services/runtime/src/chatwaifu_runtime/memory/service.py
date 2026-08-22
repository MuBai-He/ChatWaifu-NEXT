"""Conservative memory: only explicit remember/forget commands mutate durable state."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database

_REMEMBER_PATTERNS = (
    re.compile(r"^(?:请|帮我)?记住(?:一下)?[\s:\uff1a]*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?remember[\s:]+(.+)$", re.IGNORECASE),
)
_FORGET_PATTERNS = (
    re.compile(r"^(?:请|帮我)?忘记(?:掉)?[\s:\uff1a]*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?forget[\s:]+(.+)$", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class ExplicitMemoryCommand:
    operation: Literal["remember", "forget"]
    content: str


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: UUID
    content: str
    state: str
    source_session_id: UUID
    source_turn_id: UUID
    created_at: datetime
    updated_at: datetime
    tombstoned_at: datetime | None = None


class MemoryService:
    def __init__(self, database: Database, publisher: EventPublisher) -> None:
        self._database = database
        self._publisher = publisher

    def parse_explicit_command(self, text: str) -> ExplicitMemoryCommand | None:
        normalized = text.strip()
        for pattern in _REMEMBER_PATTERNS:
            if match := pattern.fullmatch(normalized):
                content = match.group(1).strip()
                if content:
                    return ExplicitMemoryCommand("remember", content)
        for pattern in _FORGET_PATTERNS:
            if match := pattern.fullmatch(normalized):
                content = match.group(1).strip()
                if content:
                    return ExplicitMemoryCommand("forget", content)
        return None

    async def apply_explicit_command(
        self, session_id: UUID, turn_id: UUID, text: str
    ) -> ExplicitMemoryCommand | None:
        command = self.parse_explicit_command(text)
        if command is None:
            return None
        if command.operation == "remember":
            await self.remember(session_id, turn_id, command.content)
        else:
            await self.forget_matching(session_id, turn_id, command.content)
        return command

    async def remember(self, session_id: UUID, turn_id: UUID, content: str) -> MemoryItem:
        normalized = _normalize(content)
        if not normalized:
            raise ValueError("memory content must not be blank")
        existing = await self._database.fetchone(
            """
            SELECT * FROM memory_items
            WHERE state = 'active' AND normalized_content = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (normalized,),
        )
        if existing is not None:
            return _item_from_row(dict(existing))
        now = datetime.now(UTC)
        item = MemoryItem(
            memory_id=uuid4(),
            content=content.strip(),
            state="active",
            source_session_id=session_id,
            source_turn_id=turn_id,
            created_at=now,
            updated_at=now,
        )
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO memory_items(
                    memory_id, content, normalized_content, state,
                    source_session_id, source_turn_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    str(item.memory_id),
                    item.content,
                    normalized,
                    str(session_id),
                    str(turn_id),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        await self._emit(
            session_id,
            turn_id,
            "memory.committed",
            {"memory_id": str(item.memory_id), "content": item.content, "policy": "explicit"},
        )
        return item

    async def forget(self, session_id: UUID, turn_id: UUID, memory_id: UUID) -> bool:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE memory_items
                SET state = 'tombstoned', updated_at = ?, tombstoned_at = ?
                WHERE memory_id = ? AND state = 'active'
                """,
                (now.isoformat(), now.isoformat(), str(memory_id)),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
        if changed:
            await self._emit(
                session_id,
                turn_id,
                "memory.tombstoned",
                {"memory_id": str(memory_id), "policy": "explicit"},
            )
        return changed

    async def forget_matching(self, session_id: UUID, turn_id: UUID, query: str) -> int:
        normalized = _normalize(query)
        rows = await self._database.fetchall(
            """
            SELECT memory_id FROM memory_items
            WHERE state = 'active' AND normalized_content LIKE ?
            ORDER BY created_at DESC LIMIT 20
            """,
            (f"%{normalized}%",),
        )
        count = 0
        for row in rows:
            if await self.forget(session_id, turn_id, UUID(str(row["memory_id"]))):
                count += 1
        return count

    async def recall(self, session_id: UUID, turn_id: UUID, limit: int = 8) -> list[MemoryItem]:
        rows = await self._database.fetchall(
            """
            SELECT * FROM memory_items WHERE state = 'active'
            ORDER BY created_at DESC LIMIT ?
            """,
            (min(max(limit, 1), 20),),
        )
        items = [_item_from_row(dict(row)) for row in rows]
        if items:
            await self._emit(
                session_id,
                turn_id,
                "memory.recalled",
                {"memory_ids": [str(item.memory_id) for item in items], "count": len(items)},
            )
        return items

    async def list(self, *, include_tombstoned: bool = False) -> list[MemoryItem]:
        where = "" if include_tombstoned else "WHERE state = 'active'"
        rows = await self._database.fetchall(
            f"SELECT * FROM memory_items {where} ORDER BY created_at DESC LIMIT 200"
        )
        return [_item_from_row(dict(row)) for row in rows]

    async def _emit(
        self,
        session_id: UUID,
        turn_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        event = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": session_id,
                "turn_id": turn_id,
                "occurred_at": datetime.now(UTC),
                "source": "runtime.memory",
                "privacy": PrivacyLevel.PRIVATE,
                "payload": payload,
            }
        )
        await self._publisher.emit(event)


def _normalize(content: str) -> str:
    return " ".join(content.casefold().split())


def _item_from_row(row: dict[str, object]) -> MemoryItem:
    tombstoned = row.get("tombstoned_at")
    return MemoryItem(
        memory_id=UUID(str(row["memory_id"])),
        content=str(row["content"]),
        state=str(row["state"]),
        source_session_id=UUID(str(row["source_session_id"])),
        source_turn_id=UUID(str(row["source_turn_id"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        tombstoned_at=datetime.fromisoformat(str(tombstoned)) if tombstoned else None,
    )
