"""Discovery must work without lexical matches and must preserve scope boundaries."""

from pathlib import Path

import pytest
from chatwaifu_protocol.base import SideEffect
from chatwaifu_protocol.skills import SkillCapability, SkillDefinition
from chatwaifu_runtime.agent.capabilities import (
    CapabilityCatalog,
    DiscoverySession,
    discovery_tools,
)
from chatwaifu_runtime.runtime_skills.registry import SkillRegistry


def test_browse_inspect_activate_existing_tools_without_intent_router() -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    catalog = CapabilityCatalog(registry.list, registry.instructions)
    assert catalog.search("zzzxxyy_unique_absence").items == []
    page = catalog.search()
    assert "calendar.read" in {item.skill_id for item in page.items}
    session = DiscoverySession(catalog, frozenset({"runtime.status"}))
    tools = {tool.operation: tool for tool in discovery_tools()}
    result = session.execute(tools["activate"], {"capability_ids": ["runtime.status/read"]})
    assert result["activated"]
    assert len(session.tools) == 1
    assert session.tools[0].to_invocation({}).skill_id == "runtime.status"
    with pytest.raises(KeyError):
        session.execute(tools["inspect"], {"capability_id": "calendar.read/read"})


def test_incremental_activation_keeps_previous_tools_and_can_narrow_explicitly() -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    catalog = CapabilityCatalog(registry.list, registry.instructions)
    session = DiscoverySession(catalog, frozenset({"workspace.files", "runtime.status"}))
    activation = discovery_tools()[2]
    session.execute(activation, {"capability_ids": ["workspace.files/read"]})
    session.execute(activation, {"capability_ids": ["runtime.status/read"]})
    assert {t.skill_id for t in session.tools} == {"workspace.files", "runtime.status"}
    session.execute(activation, {"capability_ids": ["runtime.status/read"], "replace": True})
    assert [t.skill_id for t in session.tools] == ["runtime.status"]


def test_registry_addition_is_discoverable_without_router_changes() -> None:
    definitions: list[SkillDefinition] = []
    catalog = CapabilityCatalog(lambda: definitions, lambda _: "Use a real result.")
    assert catalog.search().items == []
    definitions.append(
        SkillDefinition(
            skill_id="new.widget",
            version="1.0.0",
            name="Widget",
            description="A newly registered capability",
            capabilities=[
                SkillCapability(
                    name="read",
                    description="Observe a widget",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    side_effect=SideEffect.READ,
                )
            ],
        )
    )
    item = catalog.search().items[0]
    assert item.capability_id == "new.widget/read"
    session = DiscoverySession(catalog, None)
    session.execute(discovery_tools()[2], {"capability_ids": [item.capability_id]})
    assert session.tools[0].skill_id == "new.widget"
    definitions[0] = definitions[0].model_copy(update={"version": "2.0.0"})
    with pytest.raises(ValueError, match="changed"):
        session.validate(session.tools[0])


def test_pagination_binds_query_catalog_and_scope() -> None:
    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills" / "builtin")
    registry.reload([])
    catalog = CapabilityCatalog(registry.list, registry.instructions)
    first = catalog.search(limit=2)
    assert first.next_cursor
    second = catalog.search(limit=2, cursor=first.next_cursor)
    assert {i.capability_id for i in first.items}.isdisjoint(i.capability_id for i in second.items)
    with pytest.raises(ValueError, match="expired"):
        catalog.search(cursor=first.next_cursor, allowed_skill_ids=frozenset())
