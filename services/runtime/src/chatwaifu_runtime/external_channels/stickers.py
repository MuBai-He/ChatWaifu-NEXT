"""Preset sticker catalog for external messaging channels.

Provides safe, package-resource-backed preset stickers with deterministic matching
anchored in Character ResponsePlan, content hash verification, and strict path protection.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from chatwaifu_protocol.character import ResponsePlan
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

MAX_STICKER_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_MANIFEST_BYTES = 1024 * 1024  # 1 MB
SAFE_STICKER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
SAFE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})


class _ManifestItemModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sticker_id: str = Field(..., max_length=128)
    filename: str = Field(..., max_length=256)
    sha256: str = Field(..., max_length=64)
    mime_type: str = Field(..., max_length=64)
    expressions: list[str] = Field(default_factory=list[str])
    intents: list[str] = Field(default_factory=list[str])
    attribution: str = Field(default="", max_length=256)


class _ManifestFileModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    version: int
    stickers: list[_ManifestItemModel] = Field(default_factory=list[_ManifestItemModel])


@dataclass(frozen=True, slots=True)
class StickerEntry:
    sticker_id: str
    filename: str
    sha256: str
    mime_type: str
    expressions: tuple[str, ...]
    intents: tuple[str, ...]
    attribution: str


class PresetStickerCatalog:
    """Local preset sticker catalog using package resources or injected directory."""

    def __init__(self, root_path: Path | str | None = None) -> None:
        if root_path is not None:
            self._root_path = Path(root_path).resolve()
        else:
            try:
                ref = importlib.resources.files("chatwaifu_runtime.external_channels").joinpath(
                    "preset_stickers"
                )
                self._root_path = Path(str(ref)).resolve()
            except Exception:
                self._root_path = (Path(__file__).resolve().parent / "preset_stickers").resolve()

    @property
    def root_path(self) -> Path:
        return self._root_path

    def load_manifest(self) -> tuple[StickerEntry, ...]:
        manifest_path = self._root_path / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            logger.debug("preset sticker manifest not found or is symlink: %s", manifest_path)
            return ()

        try:
            resolved_manifest = manifest_path.resolve()
            resolved_manifest.relative_to(self._root_path)
        except ValueError:
            logger.warning("preset sticker manifest path escapes root: %s", manifest_path)
            return ()

        if resolved_manifest.is_symlink():
            logger.warning(
                "preset sticker manifest resolved path is symlink: %s", resolved_manifest
            )
            return ()

        try:
            with open(manifest_path, "rb") as f:
                raw_bytes = f.read(MAX_MANIFEST_BYTES + 1)
            if len(raw_bytes) > MAX_MANIFEST_BYTES or len(raw_bytes) == 0:
                logger.warning("preset sticker manifest size invalid: %d bytes", len(raw_bytes))
                return ()
            raw_text = raw_bytes.decode("utf-8")
            manifest_model = _ManifestFileModel.model_validate_json(raw_text)
        except Exception as exc:
            logger.warning("failed to read preset sticker manifest: %s", exc)
            return ()

        if manifest_model.version != 1:
            logger.warning(
                "unsupported preset sticker manifest version: %s (expected 1)",
                manifest_model.version,
            )
            return ()

        entries: list[StickerEntry] = []
        seen_ids: set[str] = set()
        for item in manifest_model.stickers:
            if not SAFE_STICKER_ID_PATTERN.match(item.sticker_id):
                logger.warning("invalid sticker_id in manifest: %r", item.sticker_id)
                continue

            if item.sticker_id in seen_ids:
                logger.warning("duplicate sticker_id in manifest rejected: %s", item.sticker_id)
                continue

            # Safe basename: must equal basename, no slashes, backslashes, or relative components
            if (
                not item.filename
                or os.path.basename(item.filename) != item.filename
                or "/" in item.filename
                or "\\" in item.filename
                or ".." in item.filename
                or "\x00" in item.filename
            ):
                logger.warning(
                    "unsafe filename detected for sticker %s: %r", item.sticker_id, item.filename
                )
                continue

            file_target = self._root_path / item.filename
            if file_target.is_symlink():
                logger.warning(
                    "symlink asset rejected for sticker %s: %s", item.sticker_id, item.filename
                )
                continue

            try:
                resolved_file = file_target.resolve()
                resolved_file.relative_to(self._root_path)
            except ValueError:
                logger.warning(
                    "path traversal detected for sticker %s: %s", item.sticker_id, item.filename
                )
                continue

            if resolved_file.is_symlink():
                logger.warning(
                    "resolved symlink asset rejected for sticker %s: %s",
                    item.sticker_id,
                    item.filename,
                )
                continue

            if not resolved_file.is_file():
                logger.warning(
                    "missing asset file for sticker %s: %s", item.sticker_id, resolved_file
                )
                continue

            if not SAFE_SHA256_PATTERN.match(item.sha256.lower()):
                logger.warning("invalid sha256 for sticker %s: %r", item.sticker_id, item.sha256)
                continue

            if item.mime_type not in SUPPORTED_MIME_TYPES:
                logger.warning(
                    "unsupported mime_type for sticker %s: %r", item.sticker_id, item.mime_type
                )
                continue

            # Validate content hash with bounded read
            try:
                with open(resolved_file, "rb") as f:
                    content = f.read(MAX_STICKER_BYTES + 1)
                if len(content) > MAX_STICKER_BYTES or len(content) == 0:
                    logger.warning(
                        "asset file bounded size check failed for %s: %d bytes",
                        item.sticker_id,
                        len(content),
                    )
                    continue
                computed = hashlib.sha256(content).hexdigest()
                if computed.lower() != item.sha256.lower():
                    logger.warning(
                        "asset file hash mismatch at load for %s: expected %s, got %s",
                        item.sticker_id,
                        item.sha256,
                        computed,
                    )
                    continue
            except Exception as exc:
                logger.warning(
                    "failed to verify asset file at load for %s: %s", item.sticker_id, exc
                )
                continue

            seen_ids.add(item.sticker_id)
            entries.append(
                StickerEntry(
                    sticker_id=item.sticker_id,
                    filename=item.filename,
                    sha256=item.sha256.lower(),
                    mime_type=item.mime_type,
                    expressions=tuple(item.expressions),
                    intents=tuple(item.intents),
                    attribution=item.attribution,
                )
            )

        return tuple(entries)

    def load_sticker_bytes(self, sticker_id: str, expected_sha256: str) -> bytes | None:
        """Load and strictly verify image bytes by immutable sticker ID and hash."""
        manifest = self.load_manifest()
        matched = next((e for e in manifest if e.sticker_id == sticker_id), None)
        if matched is None:
            logger.warning("sticker %s not found in manifest", sticker_id)
            return None

        if matched.sha256.lower() != expected_sha256.lower():
            logger.warning(
                "sticker %s hash mismatch against manifest: expected %s, got %s",
                sticker_id,
                matched.sha256,
                expected_sha256,
            )
            return None

        file_target = self._root_path / matched.filename
        if file_target.is_symlink():
            logger.warning("symlink asset rejected at execution for sticker %s", sticker_id)
            return None

        try:
            resolved = file_target.resolve()
            resolved.relative_to(self._root_path)
        except ValueError:
            logger.warning("path traversal detected at execution for sticker %s", sticker_id)
            return None

        if resolved.is_symlink() or not resolved.is_file():
            logger.warning(
                "sticker file missing or symlink at execution for %s: %s", sticker_id, resolved
            )
            return None

        try:
            with open(resolved, "rb") as f:
                data = f.read(MAX_STICKER_BYTES + 1)
            if len(data) > MAX_STICKER_BYTES or len(data) == 0:
                logger.warning(
                    "sticker size check failed at execution for %s: %d bytes", sticker_id, len(data)
                )
                return None
            computed = hashlib.sha256(data).hexdigest()
            if computed.lower() != expected_sha256.lower():
                logger.warning(
                    "sticker content hash mismatch at execution for %s: expected %s, got %s",
                    sticker_id,
                    expected_sha256,
                    computed,
                )
                return None
            return data
        except Exception as exc:
            logger.warning("failed to load sticker bytes at execution for %s: %s", sticker_id, exc)
            return None

    def match_sticker(self, plan: ResponsePlan | None) -> StickerEntry | None:
        """Deterministically match at most one preset sticker from Character ResponsePlan."""
        if plan is None:
            return None

        # Neutral expression defaults to no sticker
        if plan.expression == "neutral":
            return None

        manifest = self.load_manifest()
        if not manifest:
            return None

        candidates: list[tuple[int, int, StickerEntry]] = []
        for idx, entry in enumerate(manifest):
            # Only match stickers whose asset file is verified and exists on disk
            file_target = self._root_path / entry.filename
            if not file_target.is_file() or file_target.is_symlink():
                continue

            exp_match = plan.expression in entry.expressions
            intent_match = plan.intent in entry.intents

            if exp_match and intent_match:
                score = 3
            elif exp_match:
                score = 2
            elif intent_match:
                score = 1
            else:
                continue

            candidates.append((score, -idx, entry))

        if not candidates:
            return None

        # Sort descending by score, then preserving manifest position
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
