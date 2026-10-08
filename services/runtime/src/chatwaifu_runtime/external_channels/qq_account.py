"""Owner-enabled QQ account operations, with live identity and schema checks."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from chatwaifu_protocol.base import JsonObject, JsonValue
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from chatwaifu_runtime.external_channels.qq_capabilities import QQSceneCapabilities
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.public_web import validate_public_url

# These operate the host/transport or expose login secrets rather than the QQ persona.
HOST_ACTIONS = frozenset(
    {
        ".handle_quick_operation",
        "send_packet",
        "get_clientkey",
        "get_cookies",
        "get_credentials",
        "get_csrf_token",
        "bot_exit",
        "set_restart",
        "clean_cache",
        "clean_stream_temp_file",
        "download_file",
        "download_file_stream",
        "download_file_image_stream",
        "download_file_record_stream",
        "test_download_stream",
        "upload_file_stream",
        "send_online_file",
        "send_online_folder",
    }
)
MEDIA_FIELDS = frozenset({"file", "file_path", "folder_path", "image", "files", "thumb_path"})

AccountCall = Callable[
    [UUID, str, str, JsonObject, Callable[[], Awaitable[bool]], str], Awaitable[JsonObject]
]


class QQAccountCapabilities:
    def __init__(
        self,
        scene: QQSceneCapabilities,
        enabled: Callable[[], bool],
        call: AccountCall,
        schema_path: Path,
    ) -> None:
        self.scene = scene
        self.enabled = enabled
        self.call = call
        api = json.loads(schema_path.read_text(encoding="utf-8"))
        self.version: str = api["info"]["version"]
        self.schemas: dict[str, Any] = {
            path.removeprefix("/"): {
                **method["post"]
                .get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {"type": "object"}),
                "components": api["components"],
            }
            for path, method in api["paths"].items()
            if "post" in method
            and not method["post"].get("deprecated")
            and path.removeprefix("/") not in HOST_ACTIONS
        }

    async def authorize(self, context: GenerationSkillContext) -> bool:
        return self.enabled() and await self.scene.authorize(context)

    async def __call__(self, context: GenerationSkillContext, arguments: JsonObject) -> JsonObject:
        if not await self.authorize(context):
            raise PermissionError("QQ account authority is disabled or request is stale")
        binding = await self.scene.binding(context)
        if binding is None:
            raise PermissionError("QQ account binding unavailable")
        action = str(arguments["action"])
        if action not in self.schemas:
            raise PermissionError("action is not a declared QQ account capability")
        raw = arguments.get("params", {})
        if not isinstance(raw, dict):
            raise ValueError("QQ params must be an object")
        params: JsonObject = dict(raw)
        group_id = binding.conversation_key.removeprefix("group:") if binding.route_id else None
        if action in {"friend_poke", "group_poke", "send_poke"}:
            params.setdefault("user_id", binding.sender_key)
            if action != "friend_poke" and group_id is not None:
                params.setdefault("group_id", group_id)
            if action == "group_poke" and "group_id" not in params:
                raise ValueError("group_poke requires a group")
        elif action in {"send_msg", "send_group_msg", "send_private_msg"}:
            if "group_id" not in params and "user_id" not in params:
                if action != "send_private_msg" and group_id is not None:
                    params["group_id"] = group_id
                else:
                    params["user_id"] = binding.sender_key
        schema = self.schemas[action]
        allowed = set(schema.get("properties", {}))
        if set(params) - allowed:
            raise ValueError("QQ params contain undeclared fields")
        validator = Draft202012Validator(schema)
        try:
            validator.validate(params)  # pyright: ignore[reportUnknownMemberType]
        except ValidationError as error:
            raise ValueError("invalid QQ params: " + error.message[:300]) from error
        if len(json.dumps(params).encode()) > 64_000:
            raise ValueError("QQ params exceed bounded request size")
        await self._validate_media(params)

        async def guard() -> bool:
            return self.enabled() and await self.scene.binding(context) == binding

        if not await guard():
            raise PermissionError("QQ account authority changed during preparation")
        data = await self.call(
            binding.connection_id, binding.account_key, action, params, guard, self.version
        )
        # A successful transport response is evidence, not inferred from generated prose.
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded.encode()) > 48_000:
            return {
                "action": action,
                "executed": True,
                "truncated": True,
                "result_preview": encoded[:12_000],
                "source_ref": binding.source_ref,
            }
        return {"action": action, "executed": True, "data": data, "source_ref": binding.source_ref}

    async def _validate_media(self, value: JsonValue, *, media: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                await self._validate_media(child, media=key in MEDIA_FIELDS)
        elif isinstance(value, list):
            for child in value:
                await self._validate_media(child, media=media)
        elif isinstance(value, str) and "[CQ:" in value:
            raise PermissionError("use structured QQ segments instead of embedded CQ codes")
        elif media and isinstance(value, str):
            if value.startswith("base64://"):
                return
            if value.startswith(("https://", "http://")):
                await validate_public_url(value)
                return
            raise PermissionError(
                "QQ media requires public URL or base64; host paths are not granted"
            )
