"""Presentation and bubble planning for external messaging channels.

Decouples external instant-messaging presentation concerns (bubble splitting,
typing rhythm, and inter-part cadence) from canonical conversation turns.
The canonical assistant turn remains single, whole, and authoritative across
history, memory, and desktop presentation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

import regex
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTextDeliveryPartPayload,
)


@dataclass(frozen=True, slots=True)
class BubbleSplitResult:
    """Outcome of bubble planning containing planned parts and diagnostics."""

    parts: tuple[str, ...]
    fallback_reason: str | None = None

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def char_counts(self) -> tuple[int, ...]:
        return tuple(len(part) for part in self.parts)


class BubbleSplitter:
    """Lossless natural text splitter for instant messaging channels.

    Guarantees:
    1. Zero Information Loss: All content is preserved across parts;
       recombining parts restores the canonical reply with NFC equivalence.
    2. Atomic Protection: URLs, markdown links, code blocks, paired brackets,
       decimals/numbers, and emoji graphemes are never split internally.
    3. Long-Form / Technical Bypass: When bypass_long_form is enabled, code
       blocks, structured markdown, and technical explanations deliver as a
       single whole message rather than being truncated by max_parts.
    4. Casual Chat Bubble Planning: Casual conversation is segmented at natural
       sentence and line boundaries into 1 to max_parts bubbles.
    """

    # Fenced code block (```...```)
    _FENCED_CODE_PATTERN = regex.compile(r"```[\s\S]*?```")

    # Inline code (`...`)
    _INLINE_CODE_PATTERN = regex.compile(r"`[^`\n]+`")

    # Web URLs
    _URL_PATTERN = regex.compile(r"https?://[^\s<>\[\]\(\)\"\'`]+")

    # Markdown links: [text](url)
    _MARKDOWN_LINK_PATTERN = regex.compile(r"\[[^\]\n]+\]\([^\)\n]+\)")

    # Numbers, decimals, percentages, IP addresses, times (e.g. 3.14, 10:30, 50%)
    _NUMBER_PATTERN = regex.compile(r"(?<![\w\.])\d+(?:[\.,:]\d+)*%?(?![\w\.])")

    # Paired quotes and brackets (never split across bubbles)
    _PAIRED_BRACKETS_PATTERN = regex.compile(
        r"“[^”\n]*”"
        r"|‘[^’\n]*’"  # noqa: RUF001
        r"|「[^」\n]*」"
        r"|『[^』\n]*』"
        r"|《[^》\n]*》"
        r"|【[^】\n]*】"
        r"|\([^\)\n]*\)"
        r"|（[^）\n]*）"  # noqa: RUF001
        r"|\[[^\]\n]*\]"
    )

    # Multi-codepoint emoji / grapheme clusters
    _GRAPHEME_PATTERN = regex.compile(r"\X")

    # Markdown structure markers
    _MARKDOWN_HEADER_PATTERN = re.compile(r"(?m)^#{1,6}\s")
    _MARKDOWN_TABLE_PATTERN = re.compile(r"(?m)^\|.+\|\s*$")
    _MARKDOWN_LIST_PATTERN = re.compile(r"(?m)^(?:\s*[-*+]|\s*\d+\.)\s")

    # Safety disclaimer keywords
    _SAFETY_MARKERS = ("注意：", "Warning:", "Safety:", "【安全警告】", "免责声明")  # noqa: RUF001

    def _find_atomic_spans(self, text: str) -> list[tuple[int, int]]:
        """Identify spans that must never be bisected by a split boundary."""
        spans: list[tuple[int, int]] = []

        # 1. Fenced code blocks
        for m in self._FENCED_CODE_PATTERN.finditer(text):
            spans.append(m.span())

        # 2. Inline code
        for m in self._INLINE_CODE_PATTERN.finditer(text):
            spans.append(m.span())

        # 3. URLs
        for m in self._URL_PATTERN.finditer(text):
            spans.append(m.span())

        # 4. Markdown links
        for m in self._MARKDOWN_LINK_PATTERN.finditer(text):
            spans.append(m.span())

        # 5. Numbers and decimals
        for m in self._NUMBER_PATTERN.finditer(text):
            spans.append(m.span())

        # 6. Paired brackets and quotes
        for m in self._PAIRED_BRACKETS_PATTERN.finditer(text):
            spans.append(m.span())

        # 7. Multi-codepoint grapheme clusters (e.g. ZWJ emoji sequences)
        for m in self._GRAPHEME_PATTERN.finditer(text):
            if m.end() - m.start() > 1:
                spans.append(m.span())

        if not spans:
            return []

        # Merge overlapping or contiguous spans
        spans.sort(key=lambda s: s[0])
        merged: list[tuple[int, int]] = [spans[0]]
        for start, end in spans[1:]:
            prev_start, prev_end = merged[-1]
            if start < prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _should_bypass(
        self, text: str, policy: ChannelPresentationPolicy
    ) -> tuple[bool, str | None]:
        """Check whether the text qualifies for single-part long-form bypass."""
        if policy.profile is ChannelPresentationProfile.SINGLE_TEXT:
            return True, "single_text_profile"
        if policy.max_parts <= 1:
            return True, "max_parts_is_one"

        clean_text = text.strip()
        if not clean_text:
            return True, "empty_text"

        if len(clean_text) <= policy.preferred_chars_per_part:
            return True, "below_preferred_chars"

        if policy.bypass_long_form:
            # Code blocks
            if "```" in clean_text:
                return True, "code_block_detected"

            # Markdown headers
            if self._MARKDOWN_HEADER_PATTERN.search(clean_text):
                return True, "markdown_headers_detected"

            # Markdown tables
            if self._MARKDOWN_TABLE_PATTERN.search(clean_text):
                return True, "markdown_table_detected"

            # Markdown lists with 3 or more items
            if len(self._MARKDOWN_LIST_PATTERN.findall(clean_text)) >= 3:
                return True, "structured_list_detected"

            # Safety warnings / disclaimers
            if any(marker in clean_text for marker in self._SAFETY_MARKERS):
                return True, "safety_disclaimer_detected"

        return False, None

    def split(self, text: str, policy: ChannelPresentationPolicy) -> BubbleSplitResult:
        """Split canonical reply text into 1 to max_parts presentation bubbles."""
        normalized = unicodedata.normalize("NFC", text)
        bypass, reason = self._should_bypass(normalized, policy)
        if bypass:
            return BubbleSplitResult(parts=(normalized.strip(),), fallback_reason=reason)

        atomic_spans = self._find_atomic_spans(normalized)

        def is_index_protected(idx: int) -> bool:
            for start, end in atomic_spans:
                if start < idx < end:
                    return True
            return False

        # Find candidate split boundaries outside atomic spans
        # Boundaries represent cut positions: (cut_index, priority, whitespace_skip)
        boundaries: list[int] = []

        # Candidate pattern: double newline > single newline > strong CJK/Latin sentence end
        # Priority 1: line breaks
        line_break_pattern = regex.compile(r"\n+")
        for m in line_break_pattern.finditer(normalized):
            cut = m.start()
            if 0 < cut < len(normalized) and not is_index_protected(cut):
                boundaries.append(cut)

        # Priority 2: strong sentence-ending punctuation
        # CJK sentence-ending punctuation, or Latin followed by space or end
        strong_punct_pattern = regex.compile(
            r"[。！？～…]+|(?<=[a-zA-Z0-9])[.!?~]+(?=\s+|$)"  # noqa: RUF001
        )
        for m in strong_punct_pattern.finditer(normalized):
            cut = m.end()
            if 0 < cut < len(normalized) and not is_index_protected(cut):
                boundaries.append(cut)

        # Sort and deduplicate cut points
        boundaries = sorted(set(boundaries))

        if not boundaries:
            # Check weak clause punctuation if text is long
            if len(normalized) > policy.soft_max_chars_per_part:
                weak_punct_pattern = regex.compile(
                    r"[；，]+|(?<=[a-zA-Z0-9])[,;]+(?=\s+|$)"  # noqa: RUF001
                )
                for m in weak_punct_pattern.finditer(normalized):
                    cut = m.end()
                    if 0 < cut < len(normalized) and not is_index_protected(cut):
                        boundaries.append(cut)
                boundaries = sorted(set(boundaries))

        if not boundaries:
            return BubbleSplitResult(
                parts=(normalized.strip(),), fallback_reason="no_natural_boundaries"
            )

        # Slice text into raw segments at boundaries
        raw_segments: list[str] = []
        last_cut = 0
        for cut in boundaries:
            segment = normalized[last_cut:cut]
            if segment:
                raw_segments.append(segment)
            last_cut = cut
        tail = normalized[last_cut:]
        if tail:
            raw_segments.append(tail)

        # Filter out empty or whitespace-only segments while preserving non-whitespace
        segments: list[str] = []
        for seg in raw_segments:
            s = seg.strip()
            if s:
                segments.append(s)

        if not segments:
            return BubbleSplitResult(
                parts=(normalized.strip(),), fallback_reason="empty_after_split"
            )

        if len(segments) <= 1:
            return BubbleSplitResult(parts=(segments[0],), fallback_reason="single_segment")

        # Merge adjacent segments greedily up to preferred_chars_per_part / soft_max_chars_per_part
        merged_parts: list[str] = [segments[0]]
        for seg in segments[1:]:
            current = merged_parts[-1]
            combined_len = len(current) + len(seg)
            # Merge if current part is below preferred_chars and combined doesn't exceed soft_max
            if (
                len(current) < policy.preferred_chars_per_part
                and combined_len <= policy.soft_max_chars_per_part
            ):
                merged_parts[-1] = (
                    f"{current} {seg}"
                    if current[-1].isalnum() and seg[0].isalnum()
                    else f"{current}{seg}"
                )
            else:
                merged_parts.append(seg)

        # Enforce max_parts cap: merge smallest or adjacent parts until len <= max_parts
        # NO TEXT IS EVER DISCARDED!
        while len(merged_parts) > policy.max_parts:
            # Find the best merge pair (shortest combined length)
            best_idx = 0
            min_len = len(merged_parts[0]) + len(merged_parts[1])
            for i in range(1, len(merged_parts) - 1):
                comb = len(merged_parts[i]) + len(merged_parts[i + 1])
                if comb < min_len:
                    min_len = comb
                    best_idx = i
            p1 = merged_parts[best_idx]
            p2 = merged_parts[best_idx + 1]
            sep = " " if p1[-1].isalnum() and p2[0].isalnum() else ""
            merged_parts[best_idx] = f"{p1}{sep}{p2}"
            merged_parts.pop(best_idx + 1)

        return BubbleSplitResult(parts=tuple(merged_parts))


class CadenceCalculator:
    """Deterministic, testable inter-bubble typing cadence calculator.

    Computes delay_after_ms for each delivery part based on:
    - Base delay (policy.min_delay_ms)
    - Grapheme cluster count (simulating reading and typing duration)
    - Punctuation emotional weighting (natural pause after ? ! ~ …)
    - Min/max clamping
    - Hard cumulative delay ceiling (policy.total_cadence_delay_ceiling_ms)
    - The terminal part always has delay_after_ms = 0.
    """

    _GRAPHEME_PATTERN = regex.compile(r"\X")

    def __init__(self, ms_per_grapheme: int = 35) -> None:
        self._ms_per_grapheme = ms_per_grapheme

    def calculate_delays(
        self, parts: tuple[str, ...], policy: ChannelPresentationPolicy
    ) -> tuple[int, ...]:
        """Compute delay_after_ms for each part in order."""
        if not parts:
            return ()

        count = len(parts)
        if count == 1 or not policy.cadence_enabled:
            return tuple(0 for _ in parts)

        delays: list[int] = []
        total_delay_so_far = 0

        for i, part in enumerate(parts):
            # Terminal part has 0 delay after
            if i == count - 1:
                delays.append(0)
                continue

            # Count grapheme clusters
            graphemes = len(self._GRAPHEME_PATTERN.findall(part))
            char_delay = graphemes * self._ms_per_grapheme

            # Punctuation weight
            punct_weight = 0
            trimmed = part.rstrip()
            if trimmed:
                last_char = trimmed[-1]
                if last_char in "！？!?":
                    punct_weight = 250
                elif last_char in "…～~":  # noqa: RUF001
                    punct_weight = 300
                elif last_char in "。.":
                    punct_weight = 150
                elif last_char in "，,；;":  # noqa: RUF001
                    punct_weight = 100

            raw_delay = policy.min_delay_ms + char_delay + punct_weight

            # Clamp between min_delay_ms and max_delay_ms
            clamped_delay = max(
                policy.min_delay_ms,
                min(raw_delay, policy.max_delay_ms),
            )

            # Check remaining budget against total ceiling
            remaining_budget = max(
                0,
                policy.total_cadence_delay_ceiling_ms - total_delay_so_far,
            )
            final_delay = min(clamped_delay, remaining_budget)

            delays.append(final_delay)
            total_delay_so_far += final_delay

        return tuple(delays)


class DeliveryPlanFactory(Protocol):
    """Factory protocol creating delivery part drafts for a completed turn."""

    def create_parts(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
    ) -> tuple[ChannelDeliveryPartDraft, ...]: ...


class SingleTextDeliveryPlanFactory:
    """1:1 plan factory: emits canonical reply text as a single delivery part.

    Used when presentation policy selects single_text or as a feature-flag fallback.
    """

    def create_parts(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
    ) -> tuple[ChannelDeliveryPartDraft, ...]:
        safe_text = reply_text if reply_text else "(empty reply)"
        return (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text=safe_text,
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
        )


class InstantMessageDeliveryPlanFactory:
    """Instant-messaging delivery plan factory.

    Uses BubbleSplitter and CadenceCalculator to plan natural, short bubbles
    with durable inter-part cadence hints.
    """

    def __init__(
        self,
        splitter: BubbleSplitter | None = None,
        cadence_calculator: CadenceCalculator | None = None,
        default_policy: ChannelPresentationPolicy | None = None,
    ) -> None:
        self._splitter = splitter or BubbleSplitter()
        self._cadence_calculator = cadence_calculator or CadenceCalculator()
        self._default_policy = default_policy or ChannelPresentationPolicy()

    def create_parts(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
    ) -> tuple[ChannelDeliveryPartDraft, ...]:
        active_policy = policy or self._default_policy
        safe_text = reply_text if reply_text else "(empty reply)"

        if active_policy.profile is ChannelPresentationProfile.SINGLE_TEXT:
            return SingleTextDeliveryPlanFactory().create_parts(safe_text, active_policy)

        split_result = self._splitter.split(safe_text, active_policy)
        delays = self._cadence_calculator.calculate_delays(split_result.parts, active_policy)

        drafts: list[ChannelDeliveryPartDraft] = []
        for ordinal, (part_text, delay_ms) in enumerate(
            zip(split_result.parts, delays, strict=True)
        ):
            drafts.append(
                ChannelDeliveryPartDraft(
                    ordinal=ordinal,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(
                        kind=ChannelDeliveryPartKind.TEXT,
                        text=part_text,
                    ),
                    required=True,
                    delay_after_ms=delay_ms,
                    not_before_at=None,
                )
            )
        return tuple(drafts)
