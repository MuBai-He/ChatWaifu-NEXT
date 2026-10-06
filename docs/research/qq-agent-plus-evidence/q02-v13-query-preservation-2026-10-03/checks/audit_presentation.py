"""Exercise real presentation components on frozen synthetic model replies.

No channel delivery or audio synthesis occurs. Voice comparison only checks
non-whitespace text preservation, not naturalness or actual audible quality.
"""

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

from chatwaifu_protocol.channels import ChannelPresentationPolicy, ChannelPresentationProfile
from chatwaifu_runtime.conversation.text_segmenter import StreamingTextSegmenter
from chatwaifu_runtime.external_channels.presentation import BubbleSplitter, render_bubble_text

root = Path(sys.argv[1])
rows = [json.loads(s) for s in (root / "results.jsonl").read_text().splitlines()]
records = []
for row in rows:
    text = row["raw_reply"]
    profile = row["presentation_profile"]
    r = {
        "sample_key": row["sample_key"],
        "profile": profile,
        "raw_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    if profile == "default_voice":
        checks = []
        for width in [1, 7, 31]:
            segmenter = StreamingTextSegmenter()
            segments = []
            for start in range(0, len(text), width):
                segments.extend(segmenter.feed(text[start : start + width]))
            segments.extend(segmenter.flush())
            checks.append(
                {
                    "input_chunk_characters": width,
                    "segment_count": len(segments),
                    "non_whitespace_preserved": re.sub(r"\s", "", text)
                    == re.sub(r"\s", "", "".join(segments)),
                    "pending_after_flush": segmenter.pending_characters,
                }
            )
        r["voice_segmentation"] = checks
        r["audible_quality_verified"] = False
    else:
        policy = ChannelPresentationPolicy(profile=ChannelPresentationProfile(profile))
        split = BubbleSplitter().split(text, policy)
        normalized = unicodedata.normalize("NFC", text)
        rendered = [
            render_bubble_text(p, has_following_text_part=i < len(split.parts) - 1)
            for i, p in enumerate(split.parts)
        ]
        protected = re.findall(
            r"```[\s\S]*?```|\[[^\]\n]+\]\([^\)\n]+\)|https?://[^\s<>]+", normalized
        )
        r.update(
            part_count=split.part_count,
            fallback_reason=split.fallback_reason,
            exact_nfc_reconstruction="".join(split.parts) == normalized,
            atomic_spans_preserved=all(any(span in p for p in split.parts) for span in protected),
            rendering_only_removes_interpart_crlf=all(
                s == p or (i < len(split.parts) - 1 and s == p.rstrip("\r\n"))
                for i, (p, s) in enumerate(zip(split.parts, rendered, strict=False))
            ),
            single_text_one_part=split.part_count == 1 if profile == "single_text" else None,
        )
    records.append(r)
(root / "presentation-audit.json").write_text(
    json.dumps(
        {
            "records": records,
            "quality_approved": False,
            "scope": (
                "Actual splitter/renderer "
                "and voice segmenter only;"
                " no new channel sends, no"
                " TTS or physical playback"
                "."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
failures = [
    r["sample_key"]
    for r in records
    if any(
        r.get(k) is False
        for k in [
            "exact_nfc_reconstruction",
            "atomic_spans_preserved",
            "rendering_only_removes_interpart_crlf",
            "single_text_one_part",
        ]
    )
    or any(
        not c["non_whitespace_preserved"] or c["pending_after_flush"]
        for c in r.get("voice_segmentation", [])
    )
]
print(json.dumps({"rows": len(rows), "mechanical_failures": failures, "quality_approved": False}))
raise SystemExit(bool(failures))
