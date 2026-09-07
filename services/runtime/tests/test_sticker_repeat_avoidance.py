"""Recent delivered image parts break ties; transport outcomes are not preferences."""
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_protocol.sticker_library import (
    LearnedSticker,
    StickerLibrarySettings,
    StickerLibrarySnapshot,
    StickerUsageHistory,
    StickerUsageRecord,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.sticker_library.ranking import least_recently_delivered
from chatwaifu_runtime.sticker_library.selection import StickerSelectionHints
from chatwaifu_runtime.sticker_library.service import StickerLibraryService
from chatwaifu_runtime.sticker_library.usage import StickerUsagePreset

NOW = datetime(2026, 9, 7, tzinfo=UTC)
PLAN = ResponsePlan(intent="celebrate", tone="bright", expression="happy", rationale="test")


def sticker(number: int, *, related: bool = True) -> LearnedSticker:
    return LearnedSticker(
        sticker_id=f"learned_{number:032x}",
        sha256=f"{number:064x}",
        label="捏脸小猫" if related else "小狗",
        description="表情",
        expression="happy" if related else "sad",
        byte_size=1,
        learned_at=NOW,
        source_connection_id=uuid4(),
    )


def delivered(item: LearnedSticker, minutes: int = 0) -> StickerUsageRecord:
    return StickerUsageRecord(
        part_id=uuid4(),
        sticker_id=item.sticker_id,
        label=item.label,
        origin="learned",
        status="delivered",
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
        delivered_at=NOW + timedelta(minutes=minutes),
    )


def test_only_delivered_recency_breaks_stable_candidate_ties() -> None:
    a, b, c = sticker(1), sticker(2), sticker(3)
    assert least_recently_delivered([a, b, c], StickerUsageHistory()) == a
    first = delivered(a)
    assert least_recently_delivered([a, b, c], StickerUsageHistory(items=[first])) == b
    # Newest-created order does not imply newest delivery; aggregate MAX(delivered_at).
    records = [delivered(a, -10), delivered(b, -5), delivered(a, 5)]
    assert least_recently_delivered([a, b], StickerUsageHistory(items=records)) == b
    assert least_recently_delivered([a, b], StickerUsageHistory(items=[first, delivered(b)])) == a
    # Duplicate callbacks and retry counts cannot multiply usage.
    repeated = first.model_copy(update={"attempt": 7})
    assert least_recently_delivered([a, b], StickerUsageHistory(items=[first, repeated])) == b
    # Unrelated/preset entries have no influence on eligible learned candidates.
    assert least_recently_delivered([b], StickerUsageHistory(items=[first, delivered(c)])) == b


@pytest.mark.parametrize("status", ["pending", "sending", "failed", "cancelled", "skipped"])
def test_non_delivery_does_not_penalize_candidate(status: str) -> None:
    a, b = sticker(1), sticker(2)
    record = StickerUsageRecord.model_validate(
        {
            **delivered(a).model_dump(),
            "status": status,
            "delivered_at": None,
            "attempt": 5,
        }
    )
    assert least_recently_delivered([a, b], StickerUsageHistory(items=[record])) == a


class Usage:
    def __init__(self, history: StickerUsageHistory) -> None:
        self.value = history
        self.calls: list[tuple[str, str, int]] = []
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.hang = False
        self.fail = False

    async def history(
        self,
        scope: str,
        character_id: str,
        presets: Mapping[str, StickerUsagePreset],
        *,
        limit: int = 50,
    ) -> StickerUsageHistory:
        assert not presets
        self.calls.append((scope, character_id, limit))
        self.entered.set()
        if self.fail:
            raise OSError("unavailable")
        if self.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return self.value


@pytest.fixture
def selection_service(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[StickerLibraryService, Usage, list[LearnedSticker]]:
    container = RuntimeContainer(runtime_settings)
    items = [sticker(1), sticker(2), sticker(3, related=False)]

    async def snapshot(scope: str, character_id: str) -> StickerLibrarySnapshot:
        assert (scope, character_id) == ("owner", "character")
        return StickerLibrarySnapshot(settings=StickerLibrarySettings(), items=items, total_bytes=3)

    monkeypatch.setattr(container.sticker_repository, "snapshot", snapshot)
    usage = Usage(StickerUsageHistory(items=[delivered(items[0])]))
    container.sticker_library._usage = usage
    return container.sticker_library, usage, items


async def test_match_preserves_eligibility_and_scope(
    selection_service: tuple[StickerLibraryService, Usage, list[LearnedSticker]],
) -> None:
    service, usage, items = selection_service
    result = await service.match("owner", "character", PLAN)
    assert result is not None and result.sticker_id == items[1].sticker_id
    result = await service.match(
        "owner", "character", None, hints=StickerSelectionHints(interaction="face_pinch")
    )
    assert result is not None and result.sticker_id == items[1].sticker_id
    assert usage.calls == [("owner", "character", 50)] * 2
    for plan, hints in [
        (PLAN, StickerSelectionHints(blocked=True)),
        (None, StickerSelectionHints()),
        (PLAN.model_copy(update={"intent": "answer"}), StickerSelectionHints()),
        (PLAN.model_copy(update={"expression": "neutral"}), StickerSelectionHints()),
    ]:
        assert await service.match("owner", "character", plan, hints=hints) is None
    assert len(usage.calls) == 2
    items.pop(1)  # Only one eligible candidate: no optional history I/O.
    result = await service.match("owner", "character", PLAN)
    assert result is not None and result.sticker_id == items[0].sticker_id
    assert len(usage.calls) == 2


@pytest.mark.parametrize("hanging", [False, True])
async def test_history_unavailable_keeps_original_choice(
    selection_service: tuple[StickerLibraryService, Usage, list[LearnedSticker]],
    hanging: bool,
) -> None:
    service, usage, items = selection_service
    usage.hang = hanging
    usage.fail = not hanging
    async with asyncio.timeout(2):
        result = await service.match("owner", "character", PLAN)
    assert result is not None and result.sticker_id == items[0].sticker_id
    assert usage.cancelled.is_set() == hanging


async def test_cancellation_during_history_never_returns_a_sticker(
    selection_service: tuple[StickerLibraryService, Usage, list[LearnedSticker]],
) -> None:
    service, usage, _ = selection_service
    usage.hang = True
    task = asyncio.create_task(service.match("owner", "character", PLAN))
    async with asyncio.timeout(2):
        await usage.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert usage.cancelled.is_set()
