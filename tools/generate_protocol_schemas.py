"""Generate deterministic schemas and Python-owned golden fixtures."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SOURCE = ROOT / "packages" / "protocol-python" / "src"
SCHEMA_DIR = ROOT / "schemas" / "domain" / "v1"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "protocol" / "v1"
FIXED_TIME = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    # Keep generation runnable from a fresh monorepo checkout even when the local
    # Python installation ignores editable-install .pth files.
    sys.path.insert(0, str(PROTOCOL_SOURCE))
    from chatwaifu_protocol.events import (
        GENERIC_CORE_EVENT_TYPES,
        SessionCreatedEvent,
        SessionCreatedPayload,
    )
    from chatwaifu_protocol.media import AudioFrameHeader
    from chatwaifu_protocol.schema_export import export_schemas

    export_schemas(SCHEMA_DIR)
    write_json(
        FIXTURE_DIR / "python-session-created-event.json",
        SessionCreatedEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000101"),
            session_id=UUID("00000000-0000-4000-8000-000000000201"),
            sequence=1,
            occurred_at=FIXED_TIME,
            source="protocol-python",
            correlation_id=UUID("00000000-0000-4000-8000-000000000301"),
            payload=SessionCreatedPayload(character_id="default-character"),
        ),
    )
    write_json(
        FIXTURE_DIR / "audio-frame-header.json",
        AudioFrameHeader(
            stream_id=UUID("00000000-0000-4000-8000-000000000401"),
            generation_id=UUID("00000000-0000-4000-8000-000000000501"),
            sequence=7,
            pts_ms=140,
            duration_ms=20,
            codec="pcm_s16le",
            sample_rate=16_000,
            channels=1,
            byte_length=640,
        ),
    )
    write_json(
        FIXTURE_DIR / "python-generic-core-event-types.json",
        list(GENERIC_CORE_EVENT_TYPES),
    )


if __name__ == "__main__":
    main()
