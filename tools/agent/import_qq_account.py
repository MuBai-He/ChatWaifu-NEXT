"""Generate executable QQ account declarations from the pinned official schema."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from chatwaifu_runtime.external_channels.qq_account import HOST_ACTIONS

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "skills/builtin/qq-account"


def model_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [model_schema(v) for v in cast(list[Any], value)]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        return {"type": ["string", "array", "object"], "description": "QQ OneBot message content"}
    result = {k: model_schema(v) for k, v in cast(dict[str, Any], value).items()}
    if result.get("type") == "object":
        result.setdefault("additionalProperties", False)
    return result


def main() -> None:
    raw = (DESTINATION / "openapi.json").read_bytes()
    api = json.loads(raw)
    capabilities: list[dict[str, Any]] = []
    for path, methods in sorted(api["paths"].items()):
        action = path.removeprefix("/")
        method = methods.get("post")
        if method is None or method.get("deprecated") or action in HOST_ACTIONS:
            continue
        params = model_schema(
            method.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {"type": "object"})
        )
        if action in {"send_poke", "group_poke", "friend_poke"}:
            params["required"] = []
        readonly = action.startswith(("get_", "can_", "check_", "fetch_", "nc_get_")) or action in {
            "translate_en2zh",
            "ArkShareGroup",
            "ArkSharePeer",
            "ocr_image",
            ".ocr_image",
            "send_ark_share",
            "send_group_ark_share",
            "download_fileset",
        }
        capabilities.append(
            {
                "name": action,
                "adapter_tool": action,
                "description": method.get("summary", "")
                + " "
                + method.get("description", "")[:400]
                + (
                    " QQ账号级查询。"
                    if readonly
                    else " 实际执行QQ账号操作;必须调用工具并依据回执说明结果。"
                )
                + (
                    " 执行戳一戳 (poke/nudge)。参数留空默认戳当前发言者，群聊自动绑定当前群。"
                    if action in {"send_poke", "group_poke", "friend_poke"}
                    else ""
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "const": action}, "params": params},
                    "required": ["action", "params"],
                    "x-chatwaifu-discovery-only": action != "send_poke",
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "side_effect": "read"
                if readonly
                else "destructive"
                if action.startswith(("delete_", "del_", "_del_"))
                or action in {"set_group_kick", "set_group_leave", "set_group_kick_members"}
                else "external_communication",
                "required_permissions": ["qq.account.operate"],
                "confirmation_required": not readonly,
                "timeout_seconds": 30,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "instructions": "SKILL.md",
        "definition": {
            "skill_id": "qq.account",
            "version": "1.0.0",
            "name": "角色的 QQ 账号",
            "description": "账号级 QQ 能力，操作角色的 QQ 账号。",
            "capabilities": capabilities,
            "interruptible": True,
            "background_allowed": False,
        },
        "adapter": {"kind": "builtin", "handler": "qq_account"},
    }
    (DESTINATION / "chatwaifu.yaml").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (DESTINATION / "source.json").write_text(
        json.dumps(
            {
                "version": api["info"]["version"],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source": "https://github.com/NapNeko/NapCatDocs/blob/main/src/api/"
                + api["info"]["version"]
                + "/openapi.json",
                "capabilities": len(capabilities),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(capabilities)} QQ account capabilities")


if __name__ == "__main__":
    main()
