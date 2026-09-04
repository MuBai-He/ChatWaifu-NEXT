"""Universal Cross-Layer Enum Contract Gate (Python/Pydantic <-> JSON Schema).

Ensures all enum/Literal fields in Pydantic models strictly match the exported
JSON Schema enum definitions in schemas/domain/v1/protocol-catalog.schema.json.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeAliasType, Union, get_args, get_origin

import pytest
from chatwaifu_protocol.base import PrivacyLevel, SideEffect
from chatwaifu_protocol.channels import (
    ChannelAuthorizationMethod,
    ChannelAuthorizationStatus,
    ChannelChatType,
    ChannelConnectionStatus,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelGatewayStatus,
    ChannelMessageKind,
    ChannelTurnStatus,
)
from chatwaifu_protocol.conversation import InterruptionInitiator
from chatwaifu_protocol.schema_export import ProtocolCatalog
from chatwaifu_protocol.session import ConversationState, GenerationState, SessionState
from chatwaifu_protocol.skills import SkillRunState
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "schemas" / "domain" / "v1" / "protocol-catalog.schema.json"


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text("utf-8"))


def _resolve_schema_enum(
    schema: dict[str, Any] | None,
    defs: dict[str, Any],
    visited: set[int] | None = None,
) -> list[str] | None:
    if visited is None:
        visited = set()
    if not isinstance(schema, dict) or id(schema) in visited:
        return None
    visited.add(id(schema))

    if "enum" in schema and isinstance(schema["enum"], list):
        return [x for x in schema["enum"] if isinstance(x, str)]
    if "const" in schema and isinstance(schema["const"], str):
        return [schema["const"]]
    if (
        "$ref" in schema
        and isinstance(schema["$ref"], str)
        and schema["$ref"].startswith("#/$defs/")
    ):
        target_name = schema["$ref"][len("#/$defs/") :]
        if target_name in defs:
            return _resolve_schema_enum(defs[target_name], defs, visited)
    for key in ("anyOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            for sub in schema[key]:
                res = _resolve_schema_enum(sub, defs, visited)
                if res:
                    return res
    if schema.get("type") == "array" and "items" in schema and isinstance(schema["items"], dict):
        return _resolve_schema_enum(schema["items"], defs, visited)
    return None


def _extract_field_enums(ann: Any, visited: set[int] | None = None) -> list[str] | None:
    if ann is None:
        return None
    if visited is None:
        visited = set()
    if id(ann) in visited:
        return None
    visited.add(id(ann))

    if isinstance(ann, TypeAliasType):
        return _extract_field_enums(ann.__value__, visited)
    origin = get_origin(ann)
    if origin is Literal:
        return [x for x in get_args(ann) if isinstance(x, str)]
    if isinstance(ann, type) and issubclass(ann, Enum):
        return [str(e.value) for e in ann if isinstance(e.value, str)]
    if origin is Union or str(origin) == "<class 'types.UnionType'>":
        for arg in get_args(ann):
            res = _extract_field_enums(arg, visited)
            if res:
                return res
    if origin is list:
        for arg in get_args(ann):
            res = _extract_field_enums(arg, visited)
            if res:
                return res
    return None


def test_pydantic_standalone_enums_match_json_schema() -> None:
    """Verifies that core domain StrEnum definitions match JSON Schema $defs."""
    catalog = _load_catalog()
    defs = catalog.get("$defs", {})

    standalone_enums: dict[str, type[Enum]] = {
        "PrivacyLevel": PrivacyLevel,
        "SideEffect": SideEffect,
        "ChannelChatType": ChannelChatType,
        "ChannelMessageKind": ChannelMessageKind,
        "ChannelConnectionStatus": ChannelConnectionStatus,
        "ChannelGatewayStatus": ChannelGatewayStatus,
        "ChannelAuthorizationMethod": ChannelAuthorizationMethod,
        "ChannelAuthorizationStatus": ChannelAuthorizationStatus,
        "ChannelTurnStatus": ChannelTurnStatus,
        "ChannelDeliveryStatus": ChannelDeliveryStatus,
        "ChannelDeliveryPartKind": ChannelDeliveryPartKind,
        "ChannelDeliveryPartStatus": ChannelDeliveryPartStatus,
        "InterruptionInitiator": InterruptionInitiator,
        "SessionState": SessionState,
        "ConversationState": ConversationState,
        "GenerationState": GenerationState,
        "SkillRunState": SkillRunState,
    }

    for name, enum_cls in standalone_enums.items():
        assert name in defs, f"Missing definition in catalog for enum: {name}"
        schema_values = _resolve_schema_enum(defs[name], defs)
        assert schema_values is not None, f"No enum values in catalog definition for: {name}"

        python_values = [str(e.value) for e in enum_cls]
        assert sorted(python_values) == sorted(schema_values), (
            f"Enum parity mismatch for {name}: python={python_values} vs schema={schema_values}"
        )


def test_pydantic_model_properties_match_json_schema() -> None:
    """Verifies that all Pydantic model properties with enum/Literal types match JSON Schema."""
    catalog = _load_catalog()
    defs = catalog.get("$defs", {})

    checked_properties = 0

    for _cat_field_name, field_info in ProtocolCatalog.model_fields.items():
        model_cls = field_info.annotation
        if isinstance(model_cls, type) and issubclass(model_cls, BaseModel):
            model_name = model_cls.__name__
            model_def = defs.get(model_name)
            if not model_def or "properties" not in model_def:
                continue

            for prop_name, pyd_field in model_cls.model_fields.items():
                prop_def = model_def["properties"].get(prop_name)
                if not prop_def:
                    continue

                schema_enum = _resolve_schema_enum(prop_def, defs)
                pyd_enum = _extract_field_enums(pyd_field.annotation)

                if schema_enum is not None and len(schema_enum) > 0:
                    assert pyd_enum is not None, (
                        f"Pydantic model {model_name}.{prop_name} is missing enum annotation "
                        f"(schema defines: {schema_enum})"
                    )
                    assert sorted(pyd_enum) == sorted(schema_enum), (
                        f"Parity mismatch on {model_name}.{prop_name}: "
                        f"Pydantic has {sorted(pyd_enum)}, JSON Schema has {sorted(schema_enum)}"
                    )
                    checked_properties += 1

    assert checked_properties > 20, (
        f"Expected at least 20 enum properties, checked: {checked_properties}"
    )


def test_enum_parity_catches_discrepancy() -> None:
    """Verifies that enum value mismatch raises assertion error."""
    pydantic_values = ["delivered", "failed"]
    schema_values = ["delivered", "failed", "cancelled"]

    with pytest.raises(AssertionError):
        assert sorted(pydantic_values) == sorted(schema_values)
