"""Stable tie-breaking from bounded delivery evidence, not inferred preferences."""

from collections.abc import Sequence
from datetime import UTC, datetime

from chatwaifu_protocol.sticker_library import LearnedSticker, StickerUsageHistory


def least_recently_delivered(
    candidates: Sequence[LearnedSticker], history: StickerUsageHistory
) -> LearnedSticker:
    """Candidates are already eligible; absence means unseen only in this window."""
    last_delivery: dict[str, datetime] = {}
    for record in history.items:
        if (
            record.origin == "learned"
            and record.status == "delivered"
            and record.delivered_at is not None
            and record.attempt > 0
        ):
            previous = last_delivery.get(record.sticker_id)
            if previous is None or record.delivered_at > previous:
                last_delivery[record.sticker_id] = record.delivered_at
    # MAX is idempotent under repeated part facts; attempts never multiply use.
    # min preserves candidate order on equal keys, including an empty history.
    return min(
        candidates,
        key=lambda item: (
            item.sticker_id in last_delivery,
            last_delivery.get(item.sticker_id, datetime.min.replace(tzinfo=UTC)),
        ),
    )
