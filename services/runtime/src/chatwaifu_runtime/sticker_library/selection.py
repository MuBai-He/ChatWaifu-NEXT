"""Conservative local interaction hints, independent of avatar emotion.

This is deliberately a small language rule set, not general semantic retrieval.
Only committed user input controls it; generated assistant prose is never parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from chatwaifu_runtime.conversation.models import ConversationUserInputContext

_FACE_REQUEST = re.compile(
    r"(?:捏|揉)(?:捏|揉|一下|一捏|一揉)?(?:我(?:的)?|你的)?(?:小)?(?:脸|脸颊)"
    r"|捏(?:捏|一下)我"
)
_FACE_CONTENT = re.compile(r"(?:捏|揉).{0,16}(?:脸|脸颊)")
_QUESTION = re.compile(r"为什么|怎么|如何|什么|工具|教程|意思|原理|\b(?:why|how)\b", re.I)
_REFUSAL = re.compile(
    r"(?:不要|别|不许|不准|不想|不可以|不能).{0,6}(?:捏|揉|摸|发|图|表情)"
    r"|停一下|停下|停手|住手|暂停|停止|不要了|够了|算了|\bstop\b"
    r"|(?:^|[，。！？!?\s])停(?:[，。！？!?\s]|$)",
    re.I,
)
_CONTINUATION = re.compile(r"(?:球球|求求)你(?:了)?(?:嘛|吧|呀|啦|哦|好不好)?")


@dataclass(frozen=True, slots=True)
class StickerSelectionHints:
    blocked: bool = False
    interaction: Literal["face_pinch"] | None = None


def _refuses(text: str) -> bool:
    # Negating a stop is not itself a stop. Other explicit refusals still win.
    text = re.sub(r"(?:别|不要|不用)停(?:下|止|一下)?", "", text)
    return _REFUSAL.search(text) is not None


def _face_request(text: str) -> bool:
    return not _refuses(text) and not _QUESTION.search(text) and bool(_FACE_REQUEST.search(text))


def selection_hints(context: ConversationUserInputContext | None) -> StickerSelectionHints:
    if context is None:
        return StickerSelectionHints(blocked=True)
    text = context.user_text
    if _refuses(text):
        return StickerSelectionHints(blocked=True)
    if _face_request(text):
        return StickerSelectionHints(interaction="face_pinch")
    compact = re.sub(r"[\s，。！？,.!?~\uFF5E…]", "", text)
    if _CONTINUATION.fullmatch(compact):
        if context.previous_user_text and _face_request(context.previous_user_text):
            return StickerSelectionHints(interaction="face_pinch")
        # An ungrounded plea must not fall through to an unrelated emotional image.
        return StickerSelectionHints(blocked=True)
    if _FACE_REQUEST.search(text) and _QUESTION.search(text):
        return StickerSelectionHints(blocked=True)
    return StickerSelectionHints()


def matches_interaction(label: str, description: str, hints: StickerSelectionHints) -> bool:
    content = f"{label} {description}"
    return (
        hints.interaction == "face_pinch"
        and not _refuses(content)
        and _FACE_CONTENT.search(content) is not None
    )
