"""Domain models and exceptions for owner-scoped learned stickers."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

type StickerExpression = Literal["neutral", "happy", "sad", "angry", "surprised", "shy", "curious"]


class StickerLibraryRevisionConflict(ValueError):
    """Raised when an operation's expected revision does not match current state."""


@dataclass(frozen=True, slots=True)
class StickerSaveCandidate:
    """Candidate asset and metadata proposed for learning."""

    data: bytes
    label: str
    description: str
    expression: StickerExpression
    source_connection_id: UUID
    generation_id: UUID

    def __repr__(self) -> str:
        return (
            f"StickerSaveCandidate(data=<{len(self.data)} bytes>, "
            f"label={self.label!r}, description={self.description!r}, "
            f"expression={self.expression!r}, "
            f"source_connection_id={self.source_connection_id!r}, "
            f"generation_id={self.generation_id!r})"
        )
