"""Universal Cross-Layer Enum Contract Gate (Python/Pydantic <-> JSON Schema).

Ensures all enum/Literal fields in Pydantic models strictly match the exported
JSON Schema enum definitions in schemas/domain/v1/protocol-catalog.schema.json.
Uses dynamic package traversal, recursive path collection across all union/anyOf branches,
and exact set-equality assertions.
"""

from __future__ import annotations

import inspect
import json
import pkgutil
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeAliasType, Union, get_args, get_origin

import chatwaifu_protocol
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "schemas" / "domain" / "v1" / "protocol-catalog.schema.json"


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text("utf-8"))


def _extract_pydantic_enum_paths(
    cls: type[BaseModel],
    current_path: str = "",
    ancestors: set[type[BaseModel]] | None = None,
) -> dict[str, set[str]]:
    """Recursively extracts all enum and literal property paths from a Pydantic model."""
    if ancestors is None:
        ancestors = set()
    if cls in ancestors:
        return {}
    new_ancestors = ancestors | {cls}

    paths: dict[str, set[str]] = {}
    for name, field in cls.model_fields.items():
        ann = field.annotation
        p = f"{current_path}.{name}" if current_path else name
        _extract_from_annotation(ann, p, paths, new_ancestors, set())
    return paths


def _extract_from_annotation(
    ann: Any,
    current_path: str,
    paths: dict[str, set[str]],
    ancestors: set[type[BaseModel]],
    visited_anns: set[Any],
) -> None:
    if ann is None or ann in visited_anns:
        return
    visited_anns.add(ann)

    if isinstance(ann, TypeAliasType):
        _extract_from_annotation(ann.__value__, current_path, paths, ancestors, visited_anns)
        return
    origin = get_origin(ann)
    if origin is Literal:
        vals = [x for x in get_args(ann) if isinstance(x, str)]
        if vals:
            paths.setdefault(current_path, set()).update(vals)
        return
    if isinstance(ann, type) and issubclass(ann, Enum):
        vals = [str(e.value) for e in ann if isinstance(e.value, str)]
        if vals:
            paths.setdefault(current_path, set()).update(vals)
        return
    if origin is Union or str(origin) == "<class 'types.UnionType'>":
        for arg in get_args(ann):
            _extract_from_annotation(arg, current_path, paths, ancestors, set(visited_anns))
        return
    if origin is list:
        for arg in get_args(ann):
            _extract_from_annotation(arg, f"{current_path}[]", paths, ancestors, set(visited_anns))
        return
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        sub = _extract_pydantic_enum_paths(ann, current_path, ancestors)
        for k, v in sub.items():
            paths.setdefault(k, set()).update(v)


def _resolve_schema_enum_paths(
    schema: dict[str, Any] | None,
    current_path: str = "",
    defs: dict[str, Any] | None = None,
    ancestors: set[int] | None = None,
) -> dict[str, set[str]]:
    """Recursively extracts all enum and const property paths from JSON Schema.

    Combines all branches across anyOf, allOf, and oneOf.
    """
    if defs is None:
        defs = {}
    if ancestors is None:
        ancestors = set()
    paths: dict[str, set[str]] = {}
    if not isinstance(schema, dict) or id(schema) in ancestors:
        return paths
    new_ancestors = ancestors | {id(schema)}

    if "enum" in schema and isinstance(schema["enum"], list):
        vals = [x for x in schema["enum"] if isinstance(x, str)]
        if vals:
            paths.setdefault(current_path, set()).update(vals)
    elif "const" in schema and isinstance(schema["const"], str):
        paths.setdefault(current_path, set()).add(schema["const"])

    if (
        "$ref" in schema
        and isinstance(schema["$ref"], str)
        and schema["$ref"].startswith("#/$defs/")
    ):
        target_name = schema["$ref"][len("#/$defs/") :]
        if target_name in defs:
            sub = _resolve_schema_enum_paths(defs[target_name], current_path, defs, new_ancestors)
            for k, v in sub.items():
                paths.setdefault(k, set()).update(v)

    for key in ("anyOf", "allOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for sub_schema in branches:
                sub = _resolve_schema_enum_paths(sub_schema, current_path, defs, new_ancestors)
                for k, v in sub.items():
                    paths.setdefault(k, set()).update(v)

    if schema.get("type") == "array" and "items" in schema and isinstance(schema["items"], dict):
        sub = _resolve_schema_enum_paths(schema["items"], f"{current_path}[]", defs, new_ancestors)
        for k, v in sub.items():
            paths.setdefault(k, set()).update(v)

    if "prefixItems" in schema and isinstance(schema["prefixItems"], list):
        for idx, item_schema in enumerate(schema["prefixItems"]):
            sub = _resolve_schema_enum_paths(
                item_schema, f"{current_path}[{idx}]", defs, new_ancestors
            )
            for k, v in sub.items():
                paths.setdefault(k, set()).update(v)

    if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        sub = _resolve_schema_enum_paths(
            schema["additionalProperties"], f"{current_path}.*", defs, new_ancestors
        )
        for k, v in sub.items():
            paths.setdefault(k, set()).update(v)

    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop, prop_schema in schema["properties"].items():
            p = f"{current_path}.{prop}" if current_path else prop
            sub = _resolve_schema_enum_paths(prop_schema, p, defs, new_ancestors)
            for k, v in sub.items():
                paths.setdefault(k, set()).update(v)

    return paths


def test_dynamically_discovered_domain_enums_match_json_schema() -> None:
    """Dynamically traverses chatwaifu_protocol for all StrEnum/Enum classes."""
    catalog = _load_catalog()
    defs = catalog.get("$defs", {})

    discovered_enums: dict[str, type[Enum]] = {}
    for _, modname, _ in pkgutil.walk_packages(
        chatwaifu_protocol.__path__, chatwaifu_protocol.__name__ + "."
    ):
        mod = __import__(modname, fromlist=["_trash"])
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(cls, Enum)
                and cls is not Enum
                and cls.__module__.startswith("chatwaifu_protocol")
            ):
                discovered_enums[name] = cls

    assert len(discovered_enums) >= 17, (
        f"Expected at least 17 domain enums, found: {len(discovered_enums)}"
    )

    for name, enum_cls in discovered_enums.items():
        assert name in defs, f"Dynamically discovered enum {name} is missing in catalog $defs"
        schema_paths = _resolve_schema_enum_paths(defs[name], name, defs)
        assert name in schema_paths, f"No enum values found in catalog for enum: {name}"

        python_values = sorted(str(e.value) for e in enum_cls if isinstance(e.value, str))
        schema_values = sorted(schema_paths[name])

        assert python_values == schema_values, (
            f"Enum parity mismatch for {name}: python={python_values} vs schema={schema_values}"
        )


def test_recursive_pydantic_models_match_json_schema_exact_paths() -> None:
    """Dynamically verifies exact path-set and enum-value equality across domain models."""
    catalog = _load_catalog()
    defs = catalog.get("$defs", {})

    domain_models: dict[str, type[BaseModel]] = {}
    for _, modname, _ in pkgutil.walk_packages(
        chatwaifu_protocol.__path__, chatwaifu_protocol.__name__ + "."
    ):
        mod = __import__(modname, fromlist=["_trash"])
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(cls, BaseModel)
                and cls is not BaseModel
                and cls.__module__.startswith("chatwaifu_protocol")
                and name in defs
            ):
                domain_models[name] = cls

    # Rebuild models to resolve any ForwardRef annotations
    for cls in domain_models.values():
        try:
            cls.model_rebuild()
        except Exception:
            pass

    pydantic_paths: dict[str, list[str]] = {}
    for name, cls in domain_models.items():
        extracted = _extract_pydantic_enum_paths(cls, name)
        for k, v in extracted.items():
            pydantic_paths[k] = sorted(v)

    schema_paths: dict[str, list[str]] = {}
    for name in domain_models.keys():
        extracted = _resolve_schema_enum_paths(defs[name], name, defs)
        for k, v in extracted.items():
            schema_paths[k] = sorted(v)

    # 1. Exact path-set equality: No silent omissions in either direction
    pyd_keys = set(pydantic_paths.keys())
    schema_keys = set(schema_paths.keys())

    missing_in_schema = pyd_keys - schema_keys
    missing_in_pydantic = schema_keys - pyd_keys

    assert not missing_in_schema, (
        f"Enum paths in Pydantic but missing in JSON Schema: {missing_in_schema}"
    )
    assert not missing_in_pydantic, (
        f"Enum paths in JSON Schema but missing in Pydantic: {missing_in_pydantic}"
    )

    # 2. Exact value equality for every single path
    for path in sorted(pyd_keys):
        assert pydantic_paths[path] == schema_paths[path], (
            f"Parity mismatch at {path}: Pydantic={pydantic_paths[path]} "
            f"vs JSON Schema={schema_paths[path]}"
        )

    assert len(pydantic_paths) >= 190, (
        f"Expected at least 190 enum paths, verified: {len(pydantic_paths)}"
    )


def test_collector_unions_all_branches_in_multi_branch_anyof() -> None:
    """Real collector test: verifies that all branches of anyOf are collected into a unified set."""
    synthetic_schema: dict[str, Any] = {
        "anyOf": [
            {"const": "first_val"},
            {"const": "second_val"},
            {"enum": ["third_val", "fourth_val"]},
        ]
    }
    extracted = _resolve_schema_enum_paths(synthetic_schema, "SyntheticRoot")
    assert extracted["SyntheticRoot"] == {"first_val", "second_val", "third_val", "fourth_val"}


def test_collector_recursively_extracts_nested_properties() -> None:
    """Real collector test: verifies recursive traversal of nested properties and arrays."""
    nested_schema: dict[str, Any] = {
        "properties": {
            "child": {
                "type": "array",
                "items": {
                    "properties": {
                        "mode": {"enum": ["fast", "precise"]},
                    }
                },
            }
        }
    }
    extracted = _resolve_schema_enum_paths(nested_schema, "Root")
    assert extracted["Root.child[].mode"] == {"fast", "precise"}


def test_collector_extracts_prefix_items_and_additional_properties() -> None:
    """Real collector test: verifies prefixItems and additionalProperties keywords."""
    ext_schema: dict[str, Any] = {
        "prefixItems": [{"enum": ["first_val"]}, {"enum": ["second_val"]}],
        "additionalProperties": {
            "properties": {
                "flag": {"enum": ["enabled", "disabled"]},
            }
        },
    }
    extracted = _resolve_schema_enum_paths(ext_schema, "Ext")
    assert extracted["Ext[0]"] == {"first_val"}
    assert extracted["Ext[1]"] == {"second_val"}
    assert extracted["Ext.*.flag"] == {"enabled", "disabled"}


def test_parity_gate_detects_value_mismatch_and_path_omission() -> None:
    """Real gate test: verifies that value discrepancy or path omission raises AssertionError."""
    pyd_paths = {"Order.status": ["pending", "delivered"]}
    schema_paths_with_cancelled = {"Order.status": ["cancelled", "delivered", "pending"]}

    with pytest.raises(AssertionError):
        assert pyd_paths["Order.status"] == schema_paths_with_cancelled["Order.status"]

    with pytest.raises(AssertionError):
        pyd_keys = {"Model.status"}
        schema_keys = {"Model.status", "Model.extra_enum_untracked"}
        assert pyd_keys == schema_keys
