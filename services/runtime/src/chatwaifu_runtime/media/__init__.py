"""Media inspection, decoding, bounds enforcement, and storyboard synthesis."""

from chatwaifu_runtime.media.contracts import (
    InboundMediaItem,
    StoryboardFrame,
    StoryboardMetadata,
)
from chatwaifu_runtime.media.image import (
    MediaInvalidError,
    async_decode_and_sanitize_inbound_media,
    decode_and_sanitize_inbound_media,
    extract_static_poster,
    normalize_animated_sticker,
    sniff_image_mime_type,
    synthesize_storyboard,
    validate_image_bounds,
)

__all__ = [
    "InboundMediaItem",
    "MediaInvalidError",
    "StoryboardFrame",
    "StoryboardMetadata",
    "async_decode_and_sanitize_inbound_media",
    "decode_and_sanitize_inbound_media",
    "extract_static_poster",
    "normalize_animated_sticker",
    "sniff_image_mime_type",
    "synthesize_storyboard",
    "validate_image_bounds",
]
