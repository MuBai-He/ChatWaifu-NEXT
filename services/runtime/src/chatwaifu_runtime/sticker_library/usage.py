"""Read-only delivery history boundary; selection is not evidence of successful use."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from chatwaifu_protocol.sticker_library import StickerUsageHistory


@dataclass(frozen=True, slots=True)
class StickerUsagePreset:
    sha256: str
    label: str


class StickerUsageRepository(Protocol):
    async def history(
        self,
        scope: str,
        character_id: str,
        presets: Mapping[str, StickerUsagePreset],
        *,
        limit: int = 50,
    ) -> StickerUsageHistory: ...
