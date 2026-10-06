"""Stable speaker attribution that stays attached to memory text through budgeting."""

import json


def subject_text(subject_id: str | None, text: str) -> str:
    if subject_id is None or not subject_id.startswith("participant:"):
        return text
    return f"[subject={json.dumps(subject_id, ensure_ascii=False)}] {text}"
