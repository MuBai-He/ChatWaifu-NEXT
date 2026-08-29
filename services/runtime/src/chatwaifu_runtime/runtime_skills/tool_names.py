"""Stable, provider-safe names for projected Runtime Skill tools."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

_INVALID_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def allocate_tool_names(
    identities: Sequence[tuple[str, str]],
    *,
    max_length: int,
    opaque_prefix: str | None = None,
) -> list[str]:
    """Allocate deterministic names without normalization/truncation collisions.

    ``identities`` are stable ``(skill_id, capability)`` pairs.  Human-readable
    names preserve the historical MCP naming scheme.  Supplying
    ``opaque_prefix`` produces short hash-based names suitable for an LLM tool
    surface, where arbitrary third-party identifiers must not become provider
    protocol identifiers.
    """

    if max_length < 16:
        raise ValueError("max_length must be at least 16")
    if len(set(identities)) != len(identities):
        raise ValueError("tool identities must be unique")
    if opaque_prefix is not None:
        prefix = _normalize_component(opaque_prefix).strip("_") or "tool"
        if len(prefix) + 18 > max_length:
            raise ValueError("opaque_prefix leaves insufficient space for a stable digest")
        return _allocate_opaque(identities, prefix=prefix, max_length=max_length)

    bases = [_readable_base(identity, max_length=max_length) for identity in identities]
    grouped: dict[str, list[int]] = {}
    for index, base in enumerate(bases):
        grouped.setdefault(base, []).append(index)
    reserved = set(grouped)
    used: set[str] = set()
    names = [""] * len(identities)
    for base, indices in grouped.items():
        if len(indices) == 1 and base not in used:
            index = indices[0]
            names[index] = base
            used.add(base)
            continue
        for index in indices:
            digest = _identity_digest(identities[index])
            suffix_length = 12
            while True:
                suffix = digest[:suffix_length]
                stem_length = max_length - suffix_length - 2
                candidate = f"{base[:stem_length]}__{suffix}"
                if candidate not in used and candidate not in reserved:
                    break
                suffix_length += 2
                if suffix_length > len(digest):
                    raise RuntimeError("tool-name collision could not be resolved")
            names[index] = candidate
            used.add(candidate)
    return names


def _allocate_opaque(
    identities: Sequence[tuple[str, str]], *, prefix: str, max_length: int
) -> list[str]:
    digests = [_identity_digest(identity) for identity in identities]
    digest_lengths = [16] * len(digests)
    while True:
        grouped: dict[str, list[int]] = {}
        for index, digest in enumerate(digests):
            grouped.setdefault(digest[: digest_lengths[index]], []).append(index)
        collisions = [indices for indices in grouped.values() if len(indices) > 1]
        if not collisions:
            break
        for indices in collisions:
            for index in indices:
                digest_lengths[index] += 4
                if len(prefix) + 1 + digest_lengths[index] > max_length:
                    raise RuntimeError("opaque tool-name collision could not be resolved")
    return [
        f"{prefix}_{digest[:length]}"
        for digest, length in zip(digests, digest_lengths, strict=True)
    ]


def _readable_base(identity: tuple[str, str], *, max_length: int) -> str:
    normalized = _normalize_component(f"{identity[0]}__{identity[1]}").strip("_")
    return (normalized or "tool")[:max_length]


def _normalize_component(value: str) -> str:
    return _INVALID_TOOL_NAME.sub("_", value)


def _identity_digest(identity: tuple[str, str]) -> str:
    return hashlib.sha256(f"{identity[0]}\0{identity[1]}".encode()).hexdigest()
