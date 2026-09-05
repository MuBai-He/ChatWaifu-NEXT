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
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTextDeliveryPartPayload,
)
from chatwaifu_protocol.character import ResponsePlan

from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog


def render_bubble_text(text: str, *, has_following_text_part: bool) -> str:
    """Use the next bubble's visual boundary in place of trailing line breaks.

    Persisted parts retain the exact canonical slices for reconstruction. Only
    the outbound rendering removes CR/LF separators between adjacent text
    bubbles; single-part replies, interior whitespace and indentation survive.
    """
    if not has_following_text_part:
        return text
    return text.rstrip("\r\n") or text


@dataclass(frozen=True, slots=True)
class DeliveryPlanCreationResult:
    """Detailed outcome of delivery planning including diagnostic metadata."""

    parts: tuple[ChannelDeliveryPartDraft, ...]
    profile: str
    fallback_reason: str | None = None

    @property
    def total_delay_ms(self) -> int:
        return sum(p.delay_after_ms for p in self.parts)


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

    # Blank lines are explicit paragraph boundaries, including CRLF and whitespace-only lines.
    _PARAGRAPH_BREAK_PATTERN = regex.compile(r"(?:\r?\n[ \t]*){2,}")

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
        if policy.profile is not ChannelPresentationProfile.INSTANT_MESSAGE:
            return True, "not_instant_message_profile"
        if policy.max_parts <= 1:
            return True, "max_parts_is_one"

        clean_text = text.strip()
        if not clean_text:
            return True, "empty_text"

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

        if len(clean_text) <= policy.preferred_chars_per_part and not (
            self._PARAGRAPH_BREAK_PATTERN.search(clean_text)
        ):
            return True, "below_preferred_chars"

        return False, None

    def split(self, text: str, policy: ChannelPresentationPolicy) -> BubbleSplitResult:
        """Split canonical reply text into 1 to max_parts presentation bubbles.

        Enforces strict lossless invariant:
        unicodedata.normalize("NFC", "".join(result.parts)) == unicodedata.normalize("NFC", text)
        """
        normalized = unicodedata.normalize("NFC", text)
        bypass, reason = self._should_bypass(normalized, policy)
        if bypass:
            return BubbleSplitResult(parts=(normalized,), fallback_reason=reason)

        atomic_spans = self._find_atomic_spans(normalized)

        def is_index_protected(idx: int) -> bool:
            for start, end in atomic_spans:
                if start < idx < end:
                    return True
            return False

        # Explicit paragraphs survive length-based merging unless the part cap requires it.
        paragraph_cuts = {
            match.end()
            for match in self._PARAGRAPH_BREAK_PATTERN.finditer(normalized)
            if 0 < match.end() < len(normalized) and not is_index_protected(match.end())
        }

        # Find candidate split boundaries outside atomic spans
        boundaries: list[int] = []

        # Priority 1: line breaks (with optional trailing horizontal whitespace)
        line_break_pattern = regex.compile(r"(?:\r?\n[ \t]*)+")
        for m in line_break_pattern.finditer(normalized):
            cut = m.end()
            if 0 < cut < len(normalized) and not is_index_protected(cut):
                boundaries.append(cut)

        # Priority 2: strong sentence punctuation (with trailing whitespace/newlines)
        strong_punct_pattern = regex.compile(
            r"(?:[。！？～…]+|(?<=[a-zA-Z0-9])[.!?~]+)(?:[ \t\r\n]*)"  # noqa: RUF001
        )
        for m in strong_punct_pattern.finditer(normalized):
            cut = m.end()
            if 0 < cut < len(normalized) and not is_index_protected(cut):
                boundaries.append(cut)

        boundaries = sorted(set(boundaries))

        if not boundaries:
            # Check weak clause punctuation if text is long
            if len(normalized) > policy.soft_max_chars_per_part:
                weak_punct_pattern = regex.compile(
                    r"(?:[；，]+|(?<=[a-zA-Z0-9])[,;]+)(?:[ \t\r\n]*)"  # noqa: RUF001
                )
                for m in weak_punct_pattern.finditer(normalized):
                    cut = m.end()
                    if 0 < cut < len(normalized) and not is_index_protected(cut):
                        boundaries.append(cut)
                boundaries = sorted(set(boundaries))

        if not boundaries:
            return BubbleSplitResult(parts=(normalized,), fallback_reason="no_natural_boundaries")

        # Filter candidate cuts so neither side is whitespace-only
        candidate_cuts = [
            c
            for c in boundaries
            if 0 < c < len(normalized)
            and not is_index_protected(c)
            and bool(normalized[:c].strip())
            and bool(normalized[c:].strip())
        ]
        if not candidate_cuts:
            return BubbleSplitResult(parts=(normalized,), fallback_reason="no_natural_boundaries")

        # Ensure every intermediate slice has substantive non-whitespace content
        valid_cuts: list[int] = []
        last_cut = 0
        for c in candidate_cuts:
            if normalized[last_cut:c].strip():
                valid_cuts.append(c)
                last_cut = c

        while valid_cuts and not normalized[valid_cuts[-1] :].strip():
            valid_cuts.pop()

        if not valid_cuts:
            return BubbleSplitResult(parts=(normalized,), fallback_reason="no_natural_boundaries")

        cuts = [0, *valid_cuts, len(normalized)]

        # Step 1: Merge adjacent segments greedily up to preferred / soft_max
        merged_cuts: list[int] = [0]
        i = 1
        while i < len(cuts) - 1:
            current_start = merged_cuts[-1]
            cand_cut = cuts[i]
            next_cut = cuts[i + 1]
            current_len = cand_cut - current_start
            combined_len = next_cut - current_start
            if (
                cand_cut not in paragraph_cuts
                and current_len < policy.preferred_chars_per_part
                and combined_len <= policy.soft_max_chars_per_part
            ):
                i += 1
            else:
                merged_cuts.append(cand_cut)
                i += 1
        merged_cuts.append(len(normalized))

        # Step 2: Merge within paragraphs first; cross paragraphs only when the cap requires it.
        while len(merged_cuts) - 1 > policy.max_parts:
            best_idx = min(
                range(1, len(merged_cuts) - 1),
                key=lambda j: (
                    merged_cuts[j] in paragraph_cuts,
                    merged_cuts[j + 1] - merged_cuts[j - 1],
                ),
            )
            merged_cuts.pop(best_idx)

        # Produce lossless slices from cut indices
        parts = tuple(
            normalized[merged_cuts[k] : merged_cuts[k + 1]] for k in range(len(merged_cuts) - 1)
        )

        if len(parts) <= 1:
            return BubbleSplitResult(parts=(normalized,), fallback_reason="single_segment")

        return BubbleSplitResult(parts=parts)


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

    def create_plan(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
    ) -> DeliveryPlanCreationResult:
        parts = self.create_parts(reply_text, policy)
        profile_val = (
            policy.profile.value
            if policy is not None
            else ChannelPresentationProfile.SINGLE_TEXT.value
        )
        return DeliveryPlanCreationResult(
            parts=parts,
            profile=profile_val,
            fallback_reason="single_text_factory",
        )

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
        sticker_catalog: PresetStickerCatalog | None = None,
    ) -> None:
        self._splitter = splitter or BubbleSplitter()
        self._cadence_calculator = cadence_calculator or CadenceCalculator()
        self._default_policy = default_policy or ChannelPresentationPolicy(
            profile=ChannelPresentationProfile.SINGLE_TEXT
        )
        self._sticker_catalog = sticker_catalog

    def create_plan(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
        *,
        response_plan: ResponsePlan | None = None,
        can_send_sticker: bool = False,
        sticker_catalog: PresetStickerCatalog | None = None,
        learned_sticker: ChannelImageDeliveryPartPayload | None = None,
    ) -> DeliveryPlanCreationResult:
        active_policy = policy if policy is not None else self._default_policy
        safe_text = reply_text if reply_text else "(empty reply)"

        if active_policy.profile is not ChannelPresentationProfile.INSTANT_MESSAGE:
            single_parts = SingleTextDeliveryPlanFactory().create_parts(safe_text, active_policy)
            return DeliveryPlanCreationResult(
                parts=single_parts,
                profile=active_policy.profile.value,
                fallback_reason="not_instant_message_profile",
            )

        split_result = self._splitter.split(safe_text, active_policy)

        catalog = sticker_catalog or self._sticker_catalog
        image_payload: ChannelImageDeliveryPartPayload | None = None
        if (
            can_send_sticker
            and active_policy.stickers_enabled
            and response_plan is not None
            and response_plan.intent != "answer"
            and response_plan.expression != "neutral"
            and split_result.fallback_reason
            in (None, "single_segment", "no_natural_boundaries", "below_preferred_chars")
        ):
            if learned_sticker is not None:
                image_payload = learned_sticker
            elif catalog is not None:
                matched_sticker = catalog.match_sticker(response_plan)
                if matched_sticker is not None:
                    image_payload = ChannelImageDeliveryPartPayload(
                        kind=ChannelDeliveryPartKind.IMAGE,
                        sticker_id=matched_sticker.sticker_id,
                        sha256=matched_sticker.sha256,
                        mime_type=matched_sticker.mime_type,
                    )

        drafts: list[ChannelDeliveryPartDraft] = []
        if image_payload is not None:
            delays = self._cadence_calculator.calculate_delays(
                (*split_result.parts, "[sticker]"), active_policy
            )
            for ordinal, (part_text, delay_ms) in enumerate(
                zip(split_result.parts, delays[: len(split_result.parts)], strict=True)
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
            drafts.append(
                ChannelDeliveryPartDraft(
                    ordinal=len(split_result.parts),
                    kind=ChannelDeliveryPartKind.IMAGE,
                    payload=image_payload,
                    required=False,
                    delay_after_ms=0,
                    not_before_at=None,
                )
            )
        else:
            delays = self._cadence_calculator.calculate_delays(split_result.parts, active_policy)
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

        return DeliveryPlanCreationResult(
            parts=tuple(drafts),
            profile=active_policy.profile.value,
            fallback_reason=split_result.fallback_reason,
        )

    def create_parts(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
        *,
        response_plan: ResponsePlan | None = None,
        can_send_sticker: bool = False,
        sticker_catalog: PresetStickerCatalog | None = None,
        learned_sticker: ChannelImageDeliveryPartPayload | None = None,
    ) -> tuple[ChannelDeliveryPartDraft, ...]:
        return self.create_plan(
            reply_text,
            policy,
            response_plan=response_plan,
            can_send_sticker=can_send_sticker,
            sticker_catalog=sticker_catalog,
            learned_sticker=learned_sticker,
        ).parts
