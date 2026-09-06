"""Typed contracts for inbound media, temporal storyboards, and animated assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from chatwaifu_runtime.providers.contracts import LlmInputImage


@dataclass(frozen=True, slots=True)
class StoryboardFrame:
    """Metadata for one frame sampled into the storyboard."""

    frame_index: int
    pts_ms: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class StoryboardMetadata:
    """Explicit temporal and layout metadata for an animation storyboard."""

    total_frames: int
    duration_ms: int
    sampled_frames: tuple[StoryboardFrame, ...]
    layout: str = "2x2 grid (reading order: top-left, top-right, bottom-left, bottom-right)"

    @property
    def frames(self) -> tuple[StoryboardFrame, ...]:
        """Convenience alias for sampled_frames."""
        return self.sampled_frames

    def format_vision_description(
        self, image_index: int | None = None, total_images: int = 1
    ) -> str:
        """Format a clear, model-neutral description of reading order and frame times."""
        duration_sec = f"{self.duration_ms / 1000.0:.2f}s"
        sampled_desc = ", ".join(
            f"Frame {f.frame_index + 1} at {f.pts_ms / 1000.0:.2f}s (dur {f.duration_ms}ms)"
            for f in self.sampled_frames
        )
        tile_count = len(self.sampled_frames)
        if total_images == 1:
            prefix = (
                "An animated media storyboard is attached to the current user turn depicting an "
                f"animation sequence (total {self.total_frames} frames, {duration_sec} duration)."
            )
        else:
            idx_str = f"Image {image_index}" if image_index is not None else "This image"
            prefix = (
                f"{idx_str} is a {tile_count}-frame storyboard depicting an animation sequence "
                f"(total {self.total_frames} frames, {duration_sec} duration)."
            )
        return f"{prefix}\nLayout: {self.layout}.\nReading order: {sampled_desc}."


@dataclass(frozen=True, slots=True)
class InboundMediaItem:
    """Small typed neutral inbound media contract for loader and local observers.

    Preserves the original encoded bytes and animation timing for local observers
    while carrying a provider-ready LlmInputImage (cleaned storyboard for animations
    or sanitized image for static photos) for vision model dispatch.
    """

    raw_data: bytes = field(repr=False)
    original_mime_type: str
    raster_image: LlmInputImage
    is_animated: bool = False
    storyboard: StoryboardMetadata | None = None
    width: int = 0
    height: int = 0
    frame_count: int = 1
    duration_ms: int = 0

    @property
    def data(self) -> bytes:
        """Static compatibility: access provider-ready raster bytes."""
        return self.raster_image.data

    @property
    def mime_type(self) -> Literal["image/png", "image/jpeg"]:
        """Static compatibility: access provider-ready raster MIME type."""
        return self.raster_image.mime_type
