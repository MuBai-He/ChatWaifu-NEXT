"""Repository port definitions for owner-scoped learned stickers."""

from typing import Protocol

from chatwaifu_protocol.sticker_library import (
    LearnedSticker,
    StickerLibraryDeleteResult,
    StickerLibrarySettings,
    StickerLibrarySnapshot,
)

from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate


class StickerLibraryRepository(Protocol):
    """Atomic persistence boundary for learned character stickers."""

    async def get_settings(
        self,
        scope: str,
        character_id: str,
    ) -> StickerLibrarySettings: ...

    async def update_settings(
        self,
        scope: str,
        character_id: str,
        *,
        learning_enabled: bool,
        expected_revision: int,
    ) -> StickerLibrarySettings: ...

    async def snapshot(
        self,
        scope: str,
        character_id: str,
    ) -> StickerLibrarySnapshot: ...

    async def save(
        self,
        scope: str,
        character_id: str,
        candidate: StickerSaveCandidate,
        *,
        expected_revision: int,
    ) -> LearnedSticker | None: ...

    async def get_image(
        self,
        scope: str,
        character_id: str,
        sticker_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> bytes | None: ...

    async def delete(
        self,
        scope: str,
        character_id: str,
        sticker_id: str,
    ) -> StickerLibraryDeleteResult: ...
