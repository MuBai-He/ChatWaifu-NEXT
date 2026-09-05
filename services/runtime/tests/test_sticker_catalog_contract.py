"""Preset catalog trust boundary and short conversational reply coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from chatwaifu_protocol.channels import (
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
)
from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_runtime.external_channels.presentation import InstantMessageDeliveryPlanFactory
from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog


def _catalog(root: Path, *, filename: str = "happy.png") -> PresetStickerCatalog:
    content = b"catalog fixture bytes; image decoding is covered by adapter tests"
    (root / "happy.png").write_bytes(content)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "stickers": [
                    {
                        "sticker_id": "happy",
                        "filename": filename,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "mime_type": "image/png",
                        "expressions": ["happy"],
                        "intents": [],
                        "attribution": "test fixture",
                    }
                ],
            }
        )
    )
    return PresetStickerCatalog(root)


@pytest.mark.parametrize("text", ["好呀", "今天特别开心，很想把这份开心分享给你。"])
def test_short_casual_reply_can_include_sticker(tmp_path: Path, text: str) -> None:
    factory = InstantMessageDeliveryPlanFactory(sticker_catalog=_catalog(tmp_path))
    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        stickers_enabled=True,
    )
    parts = factory.create_parts(
        text,
        policy,
        response_plan=ResponsePlan(
            intent="celebrate", tone="bright", expression="happy", rationale="test"
        ),
        can_send_sticker=True,
    )
    assert len(parts) == 2
    assert isinstance(parts[-1].payload, ChannelImageDeliveryPartPayload)
    assert not parts[-1].required


@pytest.mark.parametrize("text", ["```py\nx=1\n```", "# Technical heading\nText"])
def test_short_technical_reply_never_gets_sticker(tmp_path: Path, text: str) -> None:
    factory = InstantMessageDeliveryPlanFactory(sticker_catalog=_catalog(tmp_path))
    parts = factory.create_parts(
        text,
        ChannelPresentationPolicy(
            profile=ChannelPresentationProfile.INSTANT_MESSAGE,
            stickers_enabled=True,
        ),
        response_plan=ResponsePlan(
            intent="celebrate", tone="bright", expression="happy", rationale="test"
        ),
        can_send_sticker=True,
    )
    assert len(parts) == 1


@pytest.mark.parametrize(
    "enabled,capable,expression",
    [(False, True, "happy"), (True, False, "happy"), (True, True, "neutral")],
)
def test_opt_in_capability_and_matching_required(
    tmp_path: Path,
    enabled: bool,
    capable: bool,
    expression: str,
) -> None:
    factory = InstantMessageDeliveryPlanFactory(sticker_catalog=_catalog(tmp_path))
    parts = factory.create_parts(
        "好呀",
        ChannelPresentationPolicy(
            profile=ChannelPresentationProfile.INSTANT_MESSAGE,
            stickers_enabled=enabled,
        ),
        response_plan=ResponsePlan.model_validate(
            {"expression": expression, "intent": "celebrate", "tone": "bright", "rationale": "test"}
        ),
        can_send_sticker=capable,
    )
    assert len(parts) == 1


def test_catalog_rechecks_frozen_hash(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    entry = catalog.load_manifest()[0]
    assert catalog.load_sticker_bytes(entry.sticker_id, entry.sha256) is not None
    (tmp_path / entry.filename).write_bytes(b"changed asset")
    assert catalog.load_sticker_bytes(entry.sticker_id, entry.sha256) is None
    assert (
        catalog.match_sticker(
            ResponsePlan(intent="celebrate", tone="bright", expression="happy", rationale="test")
        )
        is None
    )


@pytest.mark.parametrize("filename", ["../happy.png", "/tmp/happy.png", "nested/happy.png"])
def test_catalog_does_not_resolve_untrusted_paths(tmp_path: Path, filename: str) -> None:
    assert not _catalog(tmp_path, filename=filename).load_manifest()


def test_catalog_rejects_symlink(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    (tmp_path / "happy.png").rename(tmp_path / "target.png")
    (tmp_path / "happy.png").symlink_to(tmp_path / "target.png")
    assert not catalog.load_manifest()
