"""Focused tests for the bounded Agent -> Runtime Skill projection."""

from __future__ import annotations

from pathlib import Path

import pytest
from chatwaifu_protocol.base import JsonObject, SideEffect
from chatwaifu_protocol.skills import SkillCapability, SkillDefinition
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.registry import SkillRegistry
from chatwaifu_runtime.runtime_skills.tool_names import allocate_tool_names


def test_host_context_projects_only_safe_enabled_builtin_within_existing_limits() -> None:
    capability = _capability(
        "reply",
        "Present the current reply.",
        side_effect=SideEffect.EXTERNAL_COMMUNICATION,
        permission="channel.reply",
        confirmation=True,
    )
    definitions = [
        _skill("safe.reply", "Reply", "Current reply surface.", [capability]),
        _skill("disabled.reply", "Reply", "Current reply.", [capability], enabled=False),
        _skill("plugin.reply", "Reply", "Current reply.", [capability], source="plugin"),
        _skill(
            "unsafe.reply",
            "Reply",
            "Current reply.",
            [_capability("reply", "Current reply.", side_effect=SideEffect.WRITE)],
        ),
    ]
    router = RuntimeSkillRouter(lambda: definitions)
    contextual = frozenset(skill.skill_id for skill in definitions)
    assert router.select("🙂") == ()
    selected = router.select("🙂", contextual_skill_ids=contextual)
    assert [tool.skill_id for tool in selected] == ["safe.reply"]
    assert router.select("🙂", contextual_skill_ids=contextual, schema_budget_bytes=1) == ()
    assert router.select("🙂", contextual_skill_ids=contextual, limit=0) == ()


def test_current_reply_context_does_not_make_voice_a_global_desktop_tool() -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    router = RuntimeSkillRouter(registry.list)
    assert not any(tool.skill_id == "channel.voice" for tool in router.select("🙂"))
    selected = router.select("🙂", contextual_skill_ids=frozenset({"channel.voice"}))
    assert [tool.skill_id for tool in selected] == ["channel.voice"]
    assert selected[0].completes_channel_reply
    assert "no separate desktop confirmation" in selected[0].description


@pytest.mark.parametrize(
    "query",
    [
        "乘坐国内航班时有哪些具体的民航携带规定？",
        "核对当前安全规章和法规来源",
        "关于分布式一致性算法的选举机制，领导者心跳超时是怎么计算的？",
        "What timing assumptions does this consensus algorithm require?",
        "这个协议的状态转换有哪些规范要求？",
        "这能直接证明截至2026年10月4日所有中国境内航班都实施相同数量限制吗？"
        "请区分国际规则公告和中国境内实际执行\uff1b无法核实的部分要明确说明。",
    ],
)
def test_source_tools_route_external_documentation_from_metadata(query: str) -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    selected = RuntimeSkillRouter(registry.list).select(query)
    assert {tool.skill_id for tool in selected} >= {"web.search", "web.read"}


@pytest.mark.parametrize(
    "unavailable", ["available", "disabled", "missing", "plugin", "unsafe_schema"]
)
def test_search_companion_must_be_available_safe_host_builtin(unavailable: str) -> None:
    search = _skill("web.search", "Discovery", "findmarker", [_capability("search", "findmarker")])
    reader = _skill(
        "web.read",
        "Unmatched reader",
        "Unmatched reader",
        [
            _capability(
                "read",
                "Unmatched reader",
                permission="public_web.read",
                confirmation=True,
                schema={"type": "string"} if unavailable == "unsafe_schema" else None,
            )
        ],
        enabled=unavailable != "disabled",
        source="plugin" if unavailable == "plugin" else "builtin",
    )
    definitions = [search] if unavailable == "missing" else [search, reader]
    tools = RuntimeSkillRouter(lambda: definitions).select("findmarker")
    assert "web.search" in {tool.skill_id for tool in tools}
    assert ("web.read" in {tool.skill_id for tool in tools}) == (unavailable == "available")


def test_search_companion_respects_original_schema_and_count_limits() -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    router = RuntimeSkillRouter(registry.list)
    tools = router.select("搜索", limit=1)
    assert len(tools) == 1
    assert router.select("搜索", schema_budget_bytes=1) == ()


@pytest.mark.parametrize(
    "query",
    [
        "展示 return_exceptions=True 并区分正常结果与异常",
        "异常对象是直接作为元素返回，还是会封装在特定类型里？",
        "早上好，今天过得怎么样？",
        "只回复 17 乘以 23 的结果。",
    ],
)
def test_generic_code_result_language_does_not_recall_public_sources(query: str) -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    selected = RuntimeSkillRouter(registry.list).select(query)
    assert not {tool.skill_id for tool in selected} & {"web.search", "web.read"}


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


def _home_assistant_skill(*, enabled: bool = True) -> SkillDefinition:
    return _skill(
        "mcp.tool.ha_connection",
        "Home Assistant",
        "Discovered tools from this MCP server. Server: homeassistant.",
        [
            _capability(
                "intent__HassTurnOn",
                (
                    "Turns on/opens/presses a device or entity. For locks, this performs a lock "
                    "action. Use for requests like turn on, activate, enable, or lock."
                ),
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=True,
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
            _capability(
                "intent__HassTurnOff",
                (
                    "Turns off/closes/presses a device or entity. For locks, this performs an "
                    "unlock action. Use for requests like turn off, deactivate, disable, or unlock."
                ),
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=True,
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
            _capability(
                "light__HassLightSet",
                "Sets the brightness percentage or color of a light",
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=True,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "brightness": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            ),
            _capability(
                "climate__HassClimateSetTemperature",
                "Sets the target temperature of a climate device or entity",
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=True,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "temperature": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            ),
            _capability(
                "homeassistant__GetLiveContext",
                (
                    "Provides real-time information about the CURRENT state, value, or mode of "
                    "devices, sensors, entities, or areas. Use this tool for: 1. Answering "
                    "questions about current conditions (e.g., 'Is the light on?'). 2. As the "
                    "first step in conditional actions (e.g., 'If the weather is rainy, turn "
                    "off sprinklers' requires checking the weather first). You may filter for "
                    "devices by name, domain, and area, including combining those filters. "
                    "Prefer filtering by domain when searching for multiple devices of the "
                    "same type."
                ),
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=True,
                schema={
                    "type": "object",
                    "properties": {"area": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
        ],
        source="mcp_connection",
        enabled=enabled,
    )


@pytest.mark.parametrize(
    "query",
    ["帮我开一下客厅的灯", "把灯打开", "打开空调", "turn on the light", "turn the light on"],
)
def test_router_selects_ha_turn_on_and_avoids_turn_off(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select(query)
    capabilities = {tool.capability for tool in selected}
    assert "intent__HassTurnOn" in capabilities
    assert "intent__HassTurnOff" not in capabilities


@pytest.mark.parametrize(
    "query",
    ["把灯关掉", "关闭客厅的灯", "关掉空调", "turn off the light", "turn the light off"],
)
def test_router_selects_ha_turn_off_and_avoids_turn_on(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select(query)
    capabilities = {tool.capability for tool in selected}
    assert "intent__HassTurnOff" in capabilities
    assert "intent__HassTurnOn" not in capabilities


@pytest.mark.parametrize(
    "query",
    ["把客厅灯调亮一点", "调整灯光亮度", "调暗台灯"],
)
def test_router_selects_ha_brightness_for_dimmer_queries(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select(query)
    capabilities = {tool.capability for tool in selected}
    assert "light__HassLightSet" in capabilities
    assert "intent__HassTurnOn" not in capabilities
    assert "intent__HassTurnOff" not in capabilities


@pytest.mark.parametrize(
    "query",
    ["把房间温度调到24度", "调整空调温度", "调高室内温度"],
)
def test_router_selects_ha_temperature_for_climate_queries(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select(query)
    capabilities = {tool.capability for tool in selected}
    assert "climate__HassClimateSetTemperature" in capabilities
    assert "light__HassLightSet" not in capabilities


@pytest.mark.parametrize(
    "query",
    ["客厅的灯开着吗", "看看现在的状态", "现在的室温是多少", "设备状态", "传感器数值"],
)
def test_router_selects_ha_live_context_for_status_queries(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select(query)
    capabilities = {tool.capability for tool in selected}
    assert "homeassistant__GetLiveContext" in capabilities
    if query == "客厅的灯开着吗":
        assert "intent__HassTurnOn" not in capabilities
        assert "intent__HassTurnOff" not in capabilities


@pytest.mark.parametrize(
    "query",
    [
        "今天吃了什么",
        "讲个笑话",
        "你好",
        "陪我聊天",
        "什么情况",
        "今天情况怎么样",
        "最近情况如何",
        "早上好，今天过得怎么样？",
        "只回复 17 乘以 23 的结果。",
    ],
)
def test_router_excludes_ha_tools_for_unrelated_queries(query: str) -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    assert router.select(query) == ()


def test_router_excludes_disabled_ha_skill() -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill(enabled=False)])
    assert router.select("帮我开一下客厅的灯") == ()
    assert router.select("客厅的灯开着吗") == ()


def test_router_preserves_ha_permissions_confirmation_and_opaque_names() -> None:
    router = RuntimeSkillRouter(lambda: [_home_assistant_skill()])
    selected = router.select("把客厅灯调亮一点")
    assert len(selected) > 0
    light_tool = next(tool for tool in selected if tool.capability == "light__HassLightSet")

    assert light_tool.confirmation_required is True
    assert light_tool.side_effect == SideEffect.EXTERNAL_COMMUNICATION
    assert light_tool.name.startswith("cw_")
    assert "light__HassLightSet" not in light_tool.name
    assert "requires local user confirmation" in light_tool.description

    # Capabilities without confirmation or without required_permissions must not be exposed
    unconfirmed = _skill(
        "mcp.tool.ha_connection",
        "Home Assistant",
        "Discovered tools.",
        [
            _capability(
                "intent__HassTurnOn",
                "Turns on a device.",
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission="mcp.connection.ha.tool.call",
                confirmation=False,  # unconfirmed third-party capability
            )
        ],
        source="mcp_connection",
    )
    assert RuntimeSkillRouter(lambda: [unconfirmed]).select("打开客厅的灯") == ()

    unpermissioned = _skill(
        "mcp.tool.ha_connection",
        "Home Assistant",
        "Discovered tools.",
        [
            _capability(
                "intent__HassTurnOn",
                "Turns on a device.",
                side_effect=SideEffect.EXTERNAL_COMMUNICATION,
                permission=None,  # unpermissioned third-party capability
                confirmation=True,
            )
        ],
        source="mcp_connection",
    )
    assert RuntimeSkillRouter(lambda: [unpermissioned]).select("打开客厅的灯") == ()
