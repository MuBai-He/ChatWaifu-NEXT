"""Offline chat-wire reference estimates, not native Claude/Gemini token counts.

The checksum-pinned cl100k vocabulary ships with Runtime. This module never uses
the tokenizer registry/download loader, and SDK/encoding objects stay private.
Image costs and per-message/tool reserves remain explicit estimates. Actual
usage must still come from the provider; this reference is not a universal bound.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from tiktoken import Encoding

from chatwaifu_runtime.providers.contracts import LlmRequest
from chatwaifu_runtime.providers.openai_compatible import (
    build_chat_completions_payload,
    build_messages,
)

REFERENCE_ESTIMATOR = "cl100k_chat_json_v1"
VOCABULARY_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
ESTIMATED_IMAGE_TOKENS = 1024
_MESSAGE_TOKENS = 16
_TOOL_TOKENS = 16


def _load_reference_encoder() -> Encoding:
    data = (Path(__file__).parent / "data" / "cl100k_base.tiktoken").read_bytes()
    if hashlib.sha256(data).hexdigest() != VOCABULARY_SHA256:
        raise RuntimeError("bundled input reference vocabulary checksum mismatch")
    ranks = {
        base64.b64decode(token, validate=True): int(rank)
        for line in data.splitlines()
        for token, rank in (line.split(),)
    }
    return Encoding(
        name="cw2_cl100k_reference",
        pat_str=(
            r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
            r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
        ),
        mergeable_ranks=ranks,
        special_tokens={
            "<|endoftext|>": 100257,
            "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259,
            "<|fim_suffix|>": 100260,
            "<|endofprompt|>": 100276,
        },
    )


@lru_cache(maxsize=1)
def _reference_encoder() -> Encoding:
    # Cache only immutable vocabulary, never user text or session state.
    return _load_reference_encoder()


def count_reference_tokens(text: str) -> int:
    """Count arbitrary text as ordinary input, including special-token strings."""
    return len(_reference_encoder().encode_ordinary(text))


def estimate_reference_input_tokens(request: LlmRequest) -> int:
    # Do not construct or tokenize base64 image data just to estimate text.
    text_request = replace(request, images=()) if request.images else request
    messages = build_messages(text_request)
    payload = build_chat_completions_payload("reference", text_request, messages)
    projection = {
        key: payload[key] for key in ("messages", "tools", "tool_choice") if key in payload
    }
    wire = json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
    return (
        count_reference_tokens(wire)
        + _MESSAGE_TOKENS * len(messages)
        + _TOOL_TOKENS * len(request.tools)
        + ESTIMATED_IMAGE_TOKENS * len(request.images)
    )
