"""Additional preset-catalog rejection regressions from the Phase 17 draft."""

import hashlib
import json
from pathlib import Path

from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog


def test_catalog_rejects_duplicate_missing_and_unsupported_entries(tmp_path: Path) -> None:
    """Malformed entries cannot replace a valid catalog item or authorize a different hash."""
    cat_dir = tmp_path / "catalog_test"
    cat_dir.mkdir()
    valid_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20
    valid_sha = hashlib.sha256(valid_bytes).hexdigest()
    (cat_dir / "valid.png").write_bytes(valid_bytes)

    # Asset for unknown MIME test
    (cat_dir / "bad_mime.gif").write_bytes(valid_bytes)

    manifest_data = {
        "version": 1,
        "stickers": [
            {
                "sticker_id": "sticker-valid",
                "filename": "valid.png",
                "sha256": valid_sha,
                "mime_type": "image/png",
                "expressions": ["happy"],
                "intents": ["greeting"],
                "attribution": "a1",
            },
            # Duplicate sticker_id
            {
                "sticker_id": "sticker-valid",
                "filename": "valid.png",
                "sha256": valid_sha,
                "mime_type": "image/png",
                "expressions": ["joy"],
                "intents": ["greeting"],
                "attribution": "a2",
            },
            # Missing asset file
            {
                "sticker_id": "sticker-missing",
                "filename": "missing.png",
                "sha256": valid_sha,
                "mime_type": "image/png",
                "expressions": ["sad"],
                "intents": ["apology"],
                "attribution": "a3",
            },
            # Unknown MIME type
            {
                "sticker_id": "sticker-gif",
                "filename": "bad_mime.gif",
                "sha256": valid_sha,
                "mime_type": "image/gif",
                "expressions": ["surprised"],
                "intents": ["reaction"],
                "attribution": "a4",
            },
        ],
    }
    (cat_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    catalog = PresetStickerCatalog(cat_dir)

    # Manifest should only load the 1 valid entry (rejecting duplicate, missing, unknown MIME)
    entries = catalog.load_manifest()
    assert len(entries) == 1
    assert entries[0].sticker_id == "sticker-valid"

    # Match test on load_sticker_bytes
    loaded = catalog.load_sticker_bytes("sticker-valid", valid_sha)
    assert loaded == valid_bytes

    # Hash mismatch test
    fake_sha = "f" * 64
    assert catalog.load_sticker_bytes("sticker-valid", fake_sha) is None
    assert catalog.load_sticker_bytes("unknown-sticker", valid_sha) is None
    assert entries[0].attribution == "a1"
    assert entries[0].expressions == ("happy",)
    assert catalog.load_sticker_bytes("sticker-valid", valid_sha) == valid_bytes
