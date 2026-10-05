"""Pinned native face rendering; never interpret prose or CQ-code commands."""

import re

from chatwaifu_protocol.base import JsonObject

# Names verified against NapCat v4.18.28's QQ face_config.json, not Unicode IDs.
FACE_LABELS = {
    "0": "惊讶",
    "4": "得意",
    "9": "大哭",
    "14": "微笑",
    "15": "难过",
    "31": "咒骂",
    "32": "疑问",
    "66": "爱心",
    "178": "斜眼笑",
}
UNICODE_FACES = {"🙂": "14", "😎": "4", "😭": "9", "😔": "15", "😮": "0", "❤️": "66"}
_EMOJI = re.compile("(" + "|".join(map(re.escape, UNICODE_FACES)) + ")")
FAVORITE_JOURNAL_PREFIX = "qq-favorite:"


def face_context(data: JsonObject) -> str | None:
    value = data.get("id")
    if type(value) not in {int, str} or not re.fullmatch(r"0|[1-9][0-9]{0,5}", str(value)):
        return None
    identity = str(value)
    label = FACE_LABELS.get(identity)
    return f"[QQ表情:{label}]" if label is not None else f"[QQ表情#{identity}]"


def native_text_segments(text: str) -> list[JsonObject]:
    """Render only explicitly present Unicode faces; no emotion-derived additions."""
    if "`" in text:
        return [{"type": "text", "data": {"text": text}}]
    parts: list[JsonObject] = []
    start = 0
    for match in _EMOJI.finditer(text):
        if start < match.start():
            parts.append({"type": "text", "data": {"text": text[start : match.start()]}})
        parts.append({"type": "face", "data": {"id": UNICODE_FACES[match.group()]}})
        start = match.end()
    if start < len(text):
        parts.append({"type": "text", "data": {"text": text[start:]}})
    # Native segments share the incoming bound. Keep large emoji-rich text intact.
    return parts if 1 <= len(parts) <= 128 else [{"type": "text", "data": {"text": text}}]


def send_journal_size(journal: dict[str, str]) -> int:
    return sum(not key.startswith(FAVORITE_JOURNAL_PREFIX) for key in journal)
