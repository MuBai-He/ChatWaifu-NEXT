import json
from pathlib import Path
from typing import cast

from chatwaifu_protocol.schema_export import export_schemas


def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def test_schema_export_is_deterministic_and_self_describing(tmp_path: Path) -> None:
    output = tmp_path / "schemas"
    export_schemas(output)
    first = snapshot(output)
    export_schemas(output)

    assert first == snapshot(output)
    assert first
    for content in first.values():
        schema: object = json.loads(content)
        assert isinstance(schema, dict)
        typed_schema = cast(dict[str, object], schema)
        schema_id = typed_schema["$id"]
        title = typed_schema["title"]
        assert isinstance(schema_id, str)
        assert isinstance(title, str)
        assert schema_id.startswith("https://chatwaifu.local/schemas/domain/v1/")
        assert title
        assert typed_schema["x-schema-version"] == "1.0"
