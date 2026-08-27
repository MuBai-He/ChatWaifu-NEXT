"""Incremental, provider-agnostic sentence segmentation for low-latency TTS."""

from __future__ import annotations

_STRONG_ENDINGS = frozenset("\u3002\uff01\uff1f!?\uff1b;\n")
_SOFT_BREAKS = frozenset("\uff0c,\u3001\uff1a: \t")
_CLOSING_MARKS = frozenset("\u201d\u2019\"'\u300d\u300f\u300b\uff09\u3011\u3015\u3009")
_ENGLISH_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "vs.",
    "etc.",
)


class StreamingTextSegmenter:
    """Split arbitrary LLM deltas without assuming one delta equals one sentence."""

    def __init__(self, *, min_characters: int = 4, max_characters: int = 90) -> None:
        if min_characters < 1:
            raise ValueError("min_characters must be positive")
        if max_characters < min_characters:
            raise ValueError("max_characters must not be smaller than min_characters")
        self._min_characters = min_characters
        self._max_characters = max_characters
        self._buffer = ""

    @property
    def pending_characters(self) -> int:
        return len(self._buffer)

    def feed(self, delta: str) -> tuple[str, ...]:
        if delta:
            self._buffer += delta
        return self._drain(final=False)

    def flush(self) -> tuple[str, ...]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> tuple[str, ...]:
        segments: list[str] = []
        while self._buffer:
            boundary = _first_sentence_boundary(
                self._buffer,
                min_characters=self._min_characters,
                final=final,
            )
            if boundary is not None and boundary <= self._max_characters:
                self._append_segment(segments, boundary)
                continue
            if len(self._buffer) >= self._max_characters:
                self._append_segment(
                    segments,
                    _bounded_break(
                        self._buffer,
                        min_characters=self._min_characters,
                        max_characters=self._max_characters,
                    ),
                )
                continue
            break
        if final and self._buffer:
            if self._buffer.strip():
                segments.append(self._buffer)
            self._buffer = ""
        return tuple(segments)

    def _append_segment(self, segments: list[str], boundary: int) -> None:
        segment = self._buffer[:boundary]
        self._buffer = self._buffer[boundary:].lstrip()
        if segment.strip():
            segments.append(segment.rstrip())


def _first_sentence_boundary(text: str, *, min_characters: int, final: bool) -> int | None:
    for index in range(len(text)):
        boundary = _sentence_boundary_end(text, index, final=final)
        if boundary is not None and boundary >= min_characters:
            return boundary
    return None


def _sentence_boundary_end(text: str, index: int, *, final: bool) -> int | None:
    character = text[index]
    if character in _STRONG_ENDINGS:
        return _include_trailing_marks(text, index + 1)
    if character == "…":
        if index == 0 or text[index - 1] != "…":
            return None
        return _include_trailing_marks(text, index + 1)
    if character != ".":
        return None

    run_start = index
    while run_start > 0 and text[run_start - 1] == ".":
        run_start -= 1
    run_end = index + 1
    while run_end < len(text) and text[run_end] == ".":
        run_end += 1
    if run_end - run_start >= 3:
        if index != run_end - 1:
            return None
        return _include_trailing_marks(text, run_end)

    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous.isdigit() and following.isdigit():
        return None
    prefix = text[: index + 1].casefold().rstrip()
    if prefix.endswith(_ENGLISH_ABBREVIATIONS):
        return None
    if not following and not final:
        return None
    if following and (following.isascii() and (following.isalnum() or following in "/_-")):
        return None
    return _include_trailing_marks(text, index + 1)


def _include_trailing_marks(text: str, boundary: int) -> int:
    while boundary < len(text):
        character = text[boundary]
        if character in _STRONG_ENDINGS or character in _CLOSING_MARKS or character == "…":
            boundary += 1
            continue
        break
    return boundary


def _bounded_break(text: str, *, min_characters: int, max_characters: int) -> int:
    candidate: int | None = None
    for index, character in enumerate(text[:max_characters]):
        if index + 1 >= min_characters and character in _SOFT_BREAKS:
            candidate = index + 1
    return candidate or max_characters
