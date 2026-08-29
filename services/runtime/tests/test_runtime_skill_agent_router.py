"""Focused tests for the bounded Agent -> Runtime Skill projection."""

from __future__ import annotations

from chatwaifu_protocol.base import JsonObject, SideEffect
from chatwaifu_protocol.skills import SkillCapability, SkillDefinition
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.tool_names import allocate_tool_names


def test_router_selects_relevant_chinese_and_english_tools_with_opaque_names() -> None:
    definitions = [
        _skill(
            "runtime.status",
            "Runtime Status",
            "Report Runtime provider health and availability.",
            [_capability("read", "Read the current local Runtime status.")],
        ),
        _skill(
            "mcp.search.connection",
            "Web Search",
            "Connected public internet search service.",
            [
                _capability(
                    "search_web",
                    "Search the web for current news and sources.",
                    side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                    permission="mcp.search.call",
                    confirmation=True,
                    schema={
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                        "additionalProperties": False,
                    },
                )
            ],
            source="mcp_connection",
        ),
        _skill(
            "clock.local",
            "Local Time",
            "Read the current time and date.",
            [_capability("read_time", "Read the local clock and timezone.")],
        ),
    ]
    router = RuntimeSkillRouter(lambda: definitions)

    selected = router.select("请联网搜索 Python 的最新消息")

    assert [(tool.skill_id, tool.capability) for tool in selected] == [
        ("mcp.search.connection", "search_web")
    ]
    assert selected[0].name.startswith("cw_")
    assert len(selected[0].name) <= 64
    assert "search_web" not in selected[0].name
    assert selected[0].confirmation_required is True
    assert [tool.skill_id for tool in router.select("check the runtime health")] == [
        "runtime.status"
    ]


def test_router_excludes_disabled_non_invoke_unsafe_and_unrelated_capabilities() -> None:
    definitions = [
        _skill(
            "disabled.search",
            "Disabled Search",
            "Search the web.",
            [_capability("search", "Search the web.")],
            enabled=False,
        ),
        _skill(
            "mcp.resources",
            "MCP Resources",
            "Read web resources.",
            [_capability("read", "Read one resource.", adapter_operation="resource_read")],
            source="mcp_connection",
        ),
        _skill(
            "unsafe.notes",
            "Unsafe Notes",
            "Write a note.",
            [_capability("append", "Append a note.", side_effect=SideEffect.WRITE)],
            source="plugin",
        ),
        _skill(
            "local.echo",
            "Local Echo",
            "Repeat text through an installed local plugin.",
            [_capability("echo", "Return the provided text unchanged.")],
            source="plugin",
        ),
        _skill(
            "local.partially-gated-echo",
            "Partially Gated Echo",
            "Repeat text through an incompletely declared local plugin.",
            [
                _capability(
                    "permission_only",
                    "Return the provided text unchanged.",
                    permission="plugin.echo.read",
                ),
                _capability(
                    "confirmation_only",
                    "Return the provided text unchanged.",
                    confirmation=True,
                ),
            ],
            source="plugin",
        ),
        _skill(
            "mcp.unsafe-echo",
            "Unsafe Remote Echo",
            "Repeat text through a connected MCP server without host gates.",
            [_capability("echo", "Return the provided text unchanged.")],
            source="mcp_connection",
        ),
        _skill(
            "local.confirmed-echo",
            "Confirmed Local Echo",
            "Repeat text through an installed local plugin after confirmation.",
            [
                _capability(
                    "echo",
                    "Return the provided text unchanged.",
                    permission="plugin.echo.read",
                    confirmation=True,
                )
            ],
            source="plugin",
        ),
    ]
    router = RuntimeSkillRouter(lambda: definitions)

    selected = router.select("请把 hello 原样返回")

    assert [(tool.skill_id, tool.capability) for tool in selected] == [
        ("local.confirmed-echo", "echo")
    ]
    assert router.select("我们聊聊 Python") == ()


def test_router_allows_only_permissioned_confirmed_side_effects() -> None:
    definitions = [
        _skill(
            "notes",
            "Notes",
            "Manage local notes.",
            [
                _capability(
                    "safe_append",
                    "Append a note.",
                    side_effect=SideEffect.WRITE,
                    permission="notes.write",
                    confirmation=True,
                ),
                _capability(
                    "no_permission",
                    "Append an unsafe note.",
                    side_effect=SideEffect.WRITE,
                    confirmation=True,
                ),
                _capability(
                    "no_confirmation",
                    "Append an unsafe note.",
                    side_effect=SideEffect.WRITE,
                    permission="notes.write",
                ),
            ],
            source="plugin",
        )
    ]

    selected = RuntimeSkillRouter(lambda: definitions).select("追加一条笔记")

    assert [(tool.skill_id, tool.capability) for tool in selected] == [("notes", "safe_append")]
    assert "requires local user confirmation" in selected[0].description


def test_router_enforces_limit_schema_budget_and_rejects_external_refs() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "additionalProperties": False,
    }
    definitions = [
        _skill(
            f"search.{index}",
            f"Search {index}",
            "Search the web.",
            [_capability("search", "Search internet sources.", schema=schema)],
        )
        for index in range(5)
    ]
    definitions.append(
        _skill(
            "search.external-ref",
            "Unsafe Search",
            "Search the web.",
            [
                _capability(
                    "search",
                    "Search internet sources.",
                    schema={"type": "object", "$ref": "https://example.invalid/schema.json"},
                )
            ],
        )
    )
    router = RuntimeSkillRouter(lambda: definitions)

    selected = router.select("search the web", limit=2)

    assert len(selected) == 2
    assert all(tool.skill_id != "search.external-ref" for tool in selected)
    assert router.select("search the web", schema_budget_bytes=1) == ()


def test_projection_is_stable_and_maps_back_to_copied_skill_invocation() -> None:
    definition = _skill(
        "local.echo",
        "Local Echo",
        "Repeat text.",
        [
            _capability(
                "echo",
                "Return text unchanged.",
                permission="plugin.echo.read",
                confirmation=True,
                schema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
            )
        ],
        source="plugin",
    )
    router = RuntimeSkillRouter(lambda: [definition])

    first = router.select("echo hello")[0]
    second = router.select("echo something else")[0]
    arguments: JsonObject = {"text": "hello"}
    invocation = first.to_invocation(arguments)
    arguments["text"] = "changed"

    assert first.name == second.name
    assert invocation.skill_id == "local.echo"
    assert invocation.capability == "echo"
    assert invocation.arguments == {"text": "hello"}


def test_shared_name_allocator_preserves_mcp_names_and_has_opaque_mode() -> None:
    identities = [("same.name", "read"), ("same-name", "read")]

    readable = allocate_tool_names(identities, max_length=128)
    opaque = allocate_tool_names(identities, max_length=64, opaque_prefix="cw")

    assert len(readable) == len(set(readable))
    assert all(len(name) <= 128 for name in readable)
    assert len(opaque) == len(set(opaque))
    assert all(name.startswith("cw_") and len(name) <= 64 for name in opaque)


def _skill(
    skill_id: str,
    name: str,
    description: str,
    capabilities: list[SkillCapability],
    *,
    source: str = "builtin",
    enabled: bool = True,
) -> SkillDefinition:
    return SkillDefinition.model_validate(
        {
            "skill_id": skill_id,
            "version": "1.0.0",
            "name": name,
            "description": description,
            "capabilities": [capability.model_dump(mode="json") for capability in capabilities],
            "source": source,
            "enabled": enabled,
        }
    )


def _capability(
    name: str,
    description: str,
    *,
    side_effect: SideEffect = SideEffect.READ,
    permission: str | None = None,
    confirmation: bool = False,
    adapter_operation: str = "invoke",
    schema: JsonObject | None = None,
) -> SkillCapability:
    return SkillCapability.model_validate(
        {
            "name": name,
            "description": description,
            "input_schema": schema
            or {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object"},
            "side_effect": side_effect,
            "required_permissions": [permission] if permission else [],
            "confirmation_required": confirmation,
            "adapter_operation": adapter_operation,
        }
    )
