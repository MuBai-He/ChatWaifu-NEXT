"""Deterministic text, audio, and split policy for the Qwen3-TTS training bundle."""

from __future__ import annotations

import hashlib
import math
import re
import sys
from array import array
from dataclasses import dataclass
from typing import Literal

Split = Literal["train", "validation", "test"]

_CONTROL_CHARACTERS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")
_SPACES = re.compile(r"[ \t\u3000]+")
_SCENE = re.compile(r"^(?P<scene>[A-Za-z]+\d{3})_")
_ASCII_WORD = re.compile(r"[A-Za-z]{4,}")
_VOCALIZATION = re.compile(
    r"(?:^|[、。！？…―\s])(?:んっ|んん|んふ|あっ|あぁ|ぁぁ|はぁ|ふぅ|くぅ|ひゃ)"
    r"(?=$|[、。！？…―\s])|喘|吐息",
)
_ADULT_TERMS = (
    "えっち",
    "エッチ",
    "おっぱい",
    "オナニー",
    "セックス",
    "とびっこ",
    "バイブ",
    "乳首",
    "処女",
    "童貞",
    "射精",
    "精液",
    "膣",
)


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    """Metrics calculated from decoded mono PCM16 audio."""

    duration_seconds: float
    peak_dbfs: float
    rms_dbfs: float
    clipping_ratio: float
    silence_ratio: float
    dc_offset: float


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Whether a sample is safe for the conservative automatic training set."""

    status: Literal["selected", "review", "rejected"]
    reasons: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Remove layout artifacts without erasing Japanese prosody punctuation."""

    normalized = re.sub(r"\\+[rn](?:\\+[rn])?", "", text)
    normalized = normalized.replace("\r", "").replace("\n", "")
    normalized = normalized.replace("\ufeff", "").replace("\u200b", "")
    normalized = _CONTROL_CHARACTERS.sub("", normalized)
    normalized = _SPACES.sub(" ", normalized)
    return normalized.strip()


def scene_key(stem: str) -> str:
    """Return a stable source-scene key so adjacent dialogue stays in one split."""

    match = _SCENE.match(stem)
    if match is None:
        return stem
    return match.group("scene").lower()


def split_for_scene(scene: str, seed: str = "chatwaifu-nene-v1") -> Split:
    """Assign a whole source scene to a deterministic 90/5/5 split."""

    digest = hashlib.sha256(f"{seed}:{scene}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 1000
    if bucket < 900:
        return "train"
    if bucket < 950:
        return "validation"
    return "test"


def text_policy(
    text: str, *, minimum_characters: int = 4, maximum_characters: int = 120
) -> PolicyDecision:
    """Classify transcript problems conservatively.

    Explicit adult terms and invalid lengths are rejected. Vocalizations and other
    ambiguous content are routed to review and excluded from the automatic bundle.
    """

    reasons: list[str] = []
    reject = False
    if len(text) < minimum_characters:
        reasons.append("text_too_short")
        reject = True
    if len(text) > maximum_characters:
        reasons.append("text_too_long")
        reject = True
    if any(term in text for term in _ADULT_TERMS):
        reasons.append("adult_term")
        reject = True
    if "♪" in text or "♬" in text:
        reasons.append("song_or_music")
    if _VOCALIZATION.search(text):
        reasons.append("vocalization")
    if _ASCII_WORD.search(text):
        reasons.append("mixed_language")
    if text.count("…") >= 8 or text.count("―") >= 8:
        reasons.append("excessive_pause")

    if reject:
        return PolicyDecision("rejected", tuple(reasons))
    if reasons:
        return PolicyDecision("review", tuple(reasons))
    return PolicyDecision("selected", ())


def audio_policy(metrics: AudioMetrics) -> PolicyDecision:
    """Reject unusable audio and route borderline audio to manual review."""

    reasons: list[str] = []
    reject = False
    if metrics.duration_seconds < 1.0:
        reasons.append("audio_too_short")
        reject = True
    if metrics.duration_seconds > 12.0:
        reasons.append("audio_too_long")
        reject = True
    if metrics.rms_dbfs < -50.0 or metrics.peak_dbfs < -35.0:
        reasons.append("audio_near_silent")
        reject = True
    if metrics.clipping_ratio > 0.005:
        reasons.append("audio_clipped")
        reject = True
    if metrics.silence_ratio > 0.65:
        reasons.append("excessive_silence")
        reject = True

    if not reject and metrics.clipping_ratio > 0.0005:
        reasons.append("possible_clipping")
    if not reject and metrics.silence_ratio > 0.45:
        reasons.append("possible_excessive_silence")
    if not reject and (metrics.rms_dbfs < -36.0 or metrics.rms_dbfs > -8.0):
        reasons.append("unusual_loudness")
    if not reject and abs(metrics.dc_offset) > 0.02:
        reasons.append("dc_offset")

    if reject:
        return PolicyDecision("rejected", tuple(reasons))
    if reasons:
        return PolicyDecision("review", tuple(reasons))
    return PolicyDecision("selected", ())


def combine_policy(*decisions: PolicyDecision) -> PolicyDecision:
    """Combine independent policy stages without losing their reasons."""

    reasons = tuple(reason for decision in decisions for reason in decision.reasons)
    statuses = {decision.status for decision in decisions}
    if "rejected" in statuses:
        return PolicyDecision("rejected", reasons)
    if "review" in statuses:
        return PolicyDecision("review", reasons)
    return PolicyDecision("selected", reasons)


def pcm16_metrics(
    pcm: bytes,
    *,
    sample_rate: int = 24_000,
    silence_dbfs: float = -45.0,
) -> AudioMetrics:
    """Calculate bounded metrics from little-endian mono signed PCM16."""

    if len(pcm) == 0 or len(pcm) % 2:
        raise ValueError("PCM16 payload must contain a non-zero even number of bytes")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    count = len(samples)
    absolute_peak = max(abs(int(sample)) for sample in samples)
    square_sum = sum(int(sample) * int(sample) for sample in samples)
    signed_sum = sum(int(sample) for sample in samples)
    rms = math.sqrt(square_sum / count)
    silence_threshold = 32768.0 * 10 ** (silence_dbfs / 20.0)
    silent = sum(abs(int(sample)) <= silence_threshold for sample in samples)
    clipped = sum(abs(int(sample)) >= 32760 for sample in samples)

    return AudioMetrics(
        duration_seconds=count / sample_rate,
        peak_dbfs=_amplitude_dbfs(absolute_peak),
        rms_dbfs=_amplitude_dbfs(rms),
        clipping_ratio=clipped / count,
        silence_ratio=silent / count,
        dc_offset=(signed_sum / count) / 32768.0,
    )


def reference_score(text: str, metrics: AudioMetrics, stem: str) -> tuple[float, str]:
    """Rank clean training samples for one fixed speaker-reference clip."""

    duration_penalty = abs(metrics.duration_seconds - 5.5) * 4.0
    loudness_penalty = abs(metrics.rms_dbfs - (-20.0)) * 0.4
    length_penalty = abs(len(text) - 28) * 0.12
    question_penalty = 3.0 if text.endswith(("？", "?")) else 0.0
    expression_penalty = sum(text.count(mark) for mark in ("！", "!", "…", "―", "っ")) * 1.5
    return (
        duration_penalty
        + loudness_penalty
        + length_penalty
        + question_penalty
        + expression_penalty,
        stem,
    )


def _amplitude_dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return -120.0
    return 20.0 * math.log10(amplitude / 32768.0)
