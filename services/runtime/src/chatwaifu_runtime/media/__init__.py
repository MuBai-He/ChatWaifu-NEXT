"""Media inspection, decoding, bounds enforcement, and storyboard synthesis."""

from chatwaifu_runtime.media.contracts import (
    InboundMediaItem,
    StoryboardFrame,
    StoryboardMetadata,
)
from chatwaifu_runtime.media.executor import (
    MediaCancellationToken,
    MediaExecutionPool,
    get_default_media_pool,
)
from chatwaifu_runtime.media.image import (
    MediaInvalidError,
    async_decode_and_sanitize_inbound_media,
    async_extract_static_poster,
    async_normalize_animated_sticker,
    async_validate_image_bounds,
    decode_and_sanitize_inbound_media,
    extract_static_poster,
    normalize_animated_sticker,
    sniff_image_mime_type,
    strip_static_image_exif,
    synthesize_storyboard,
    validate_image_bounds,
)

__all__ = [
    "InboundMediaItem",
    "MediaCancellationToken",
    "MediaExecutionPool",
    "MediaInvalidError",
    "StoryboardFrame",
    "StoryboardMetadata",
    "async_decode_and_sanitize_inbound_media",
    "async_extract_static_poster",
    "async_normalize_animated_sticker",
    "async_validate_image_bounds",
    "decode_and_sanitize_inbound_media",
    "extract_static_poster",
    "get_default_media_pool",
    "normalize_animated_sticker",
    "sniff_image_mime_type",
    "strip_static_image_exif",
    "synthesize_storyboard",
    "validate_image_bounds",
]
