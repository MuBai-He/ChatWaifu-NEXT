"""Reproducibly import pinned official OpenAPI metadata; execution needs separate scope review."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

PINNED_VERSION = "4.18.33"
REVIEWED = {
    "get_msg",
    "get_group_info",
    "get_group_root_files",
    "get_group_file_system_info",
    "get_group_files_by_folder",
    "get_group_file_url",
    "get_group_msg_history",
}


def import_catalog(
    source: Path, destination: Path, compatible_sources: tuple[Path, ...] = ()
) -> None:
    raw = source.read_bytes()
    api = json.loads(raw)
    if api["info"]["version"] != PINNED_VERSION:
        raise ValueError("OpenAPI version does not match reviewed interface version")
    compatible_versions: list[dict[str, Any]] = []
    for compatible_source in compatible_sources:
        compatible_raw = compatible_source.read_bytes()
        compatible = json.loads(compatible_raw)
        for action in REVIEWED:
            original = api["paths"]["/" + action]["post"]
            candidate = compatible["paths"]["/" + action]["post"]
            if candidate.get("deprecated") or any(
                original.get(key) != candidate.get(key) for key in ("requestBody", "responses")
            ):
                raise ValueError("compatible catalog differs in reviewed action: " + action)
        compatible_version = compatible["info"]["version"]
        compatible_versions.append(
            {
                "version": compatible_version,
                "source": "https://github.com/NapNeko/NapCatDocs/blob/main/src/api/"
                + compatible_version
                + "/openapi.json",
                "sha256": hashlib.sha256(compatible_raw).hexdigest(),
                "reviewed_actions": sorted(REVIEWED),
            }
        )
    old = yaml.safe_load(destination.read_text(encoding="utf-8"))
    declarations = {c["name"]: c for c in old["definition"]["capabilities"]}
    capabilities: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    for path, methods in sorted(api["paths"].items()):
        action = path.removeprefix("/")
        method = methods.get("post")
        if method is None or method.get("deprecated"):
            continue
        input_schema = (
            method.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        metadata.append(
            {
                "action": action,
                "description": method.get("summary", "") + " " + method.get("description", ""),
                "parameters": input_schema,
                "reviewed": action in REVIEWED,
            }
        )
        if action in declarations:
            capabilities.append(declarations[action])
            continue
        properties = {"action": {"type": "string", "const": action}}
        required = ["action"]
        if action in {"get_group_file_url", "get_group_files_by_folder"}:
            properties["resource_ref"] = {"type": "string", "maxLength": 4096}
            required.append("resource_ref")
        capabilities.append(
            {
                "name": action,
                "adapter_tool": action,
                "description": metadata[-1]["description"]
                + (
                    " 当前场景受控查询。"
                    if action in REVIEWED
                    else " 需要作用域声明或专用适配;尚不能执行。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                    "x-chatwaifu-discovery-only": True,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "side_effect": "read" if action in REVIEWED else "external_communication",
                "required_permissions": ["qq.current_scene.read"]
                if action in REVIEWED
                else ["qq.api.adaptation_required"],
                "confirmation_required": action not in REVIEWED,
                "timeout_seconds": 30,
            }
        )
    capabilities.extend(c for name, c in declarations.items() if name == "read_file")
    old["definition"]["capabilities"] = capabilities
    destination.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    destination.with_name("api-catalog.json").write_text(
        json.dumps(
            {
                "version": PINNED_VERSION,
                "source": "https://github.com/NapNeko/NapCatDocs/blob/main/src/api/"
                + PINNED_VERSION
                + "/openapi.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "actions": metadata,
                "compatible_versions": compatible_versions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--compatible-source", type=Path, action="append", default=[])
    args = parser.parse_args()
    import_catalog(args.source, args.destination, tuple(args.compatible_source))
