"""Privacy-minimal Runtime Skill audit payloads.

Runtime results are delivered to the caller separately. Durable audit rows keep a
bounded structural summary without a reusable content digest. Only a host-owned
builtin schema may opt an individual field into plaintext persistence with
``x-chatwaifu-audit-public``; ``writeOnly`` and sensitive annotations always win,
including through local ``$ref`` and ``allOf`` composition.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from typing import Final, cast

from chatwaifu_protocol.base import JsonObject, JsonValue

MAX_AUDIT_PAYLOAD_BYTES = 64 * 1024
_AUDIT_PUBLIC = "x-chatwaifu-audit-public"
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|"
    r"passwd|authorization|cookie|credential|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_FORMATS = {"password", "secret", "credential", "token"}
_PRIVATE_FORMATS = {
    "email",
    "idn-email",
    "hostname",
    "ipv4",
    "ipv6",
    "uri",
    "uri-reference",
    "iri",
    "iri-reference",
}
_PRIVATE_KEY = re.compile(
    r"(?:^|[_-])(?:file[_-]?(?:body|content|data)|body|content|text|message|prompt|"
    r"transcript|email|e[_-]?mail|phone|telephone|address|postal[_-]?code|ssn|"
    r"passport|national[_-]?id|date[_-]?of[_-]?birth|dob|uri|url|path|filename)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_COMPACT_SUFFIXES = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "clientsecret",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "credential",
    "privatekey",
}
_MISSING: Final = object()


def sanitize_audit_payload(
    value: JsonValue,
    schema: JsonObject | None = None,
    *,
    max_bytes: int = MAX_AUDIT_PAYLOAD_BYTES,
    allow_schema_public: bool = False,
) -> JsonValue:
    """Return a bounded summary with only trusted, explicitly public fields."""

    encoded = _encode(value)
    summary: JsonObject = {
        "_audit_summary": True,
        "kind": _kind(value),
        "encoded_bytes": len(encoded),
    }
    if isinstance(value, dict):
        summary["property_count"] = len(value)
    elif isinstance(value, list):
        summary["item_count"] = len(value)

    root = schema or {}
    public = _collect_public(
        value,
        [root],
        root=root,
        key=None,
        allow_schema_public=allow_schema_public,
    )
    if public is not _MISSING:
        summary["public"] = cast(JsonValue, public)
    if len(_encode(summary)) <= max_bytes:
        return summary
    # Even an explicitly public field must not make the audit row unbounded.
    summary.pop("public", None)
    summary["public_truncated"] = True
    if len(_encode(summary)) <= max_bytes:
        return summary
    minimal: JsonObject = {"_audit_summary": True, "kind": _kind(value)}
    return minimal if len(_encode(minimal)) <= max_bytes else {"_audit_summary": True}


def payload_digest(value: JsonValue, *, key: bytes) -> str:
    """Return a runtime-keyed digest without enabling offline value guessing."""

    if not key:
        raise ValueError("audit digest key must not be empty")
    return hmac.new(key, _encode(value), hashlib.sha256).hexdigest()


def _collect_public(
    value: JsonValue,
    schemas: list[JsonObject],
    *,
    root: JsonObject,
    key: str | None,
    allow_schema_public: bool,
) -> JsonValue | object:
    expanded = list(_expand_schemas(schemas, root))
    if (
        (key is not None and _key_is_sensitive(key))
        or any(_schema_is_sensitive(schema) for schema in expanded)
        or any(_has_unresolved_reference(schema, root) for schema in expanded)
    ):
        return _MISSING

    explicitly_public = allow_schema_public and any(
        schema.get(_AUDIT_PUBLIC) is True for schema in expanded
    )
    if isinstance(value, dict):
        result: JsonObject = {}
        property_names = _declared_property_names(expanded)
        for child_key in sorted(property_names & value.keys()):
            child_schemas = _property_schemas(expanded, child_key)
            child = _collect_public(
                value[child_key],
                child_schemas,
                root=root,
                key=child_key,
                allow_schema_public=allow_schema_public,
            )
            if child is not _MISSING:
                result[child_key] = cast(JsonValue, child)
        return result if result else _MISSING

    if isinstance(value, list):
        if not explicitly_public:
            return _MISSING
        item_schemas = _item_schemas(expanded)
        if not item_schemas:
            # Public arrays without an item schema are too broad to persist.
            return _MISSING
        public_items: list[JsonValue] = []
        for item in value:
            public = _collect_public(
                item,
                item_schemas,
                root=root,
                key=None,
                allow_schema_public=allow_schema_public,
            )
            if public is _MISSING:
                return _MISSING
            public_items.append(cast(JsonValue, public))
        return public_items

    return value if explicitly_public else _MISSING


def _expand_schemas(schemas: Iterable[JsonObject], root: JsonObject) -> Iterable[JsonObject]:
    pending = list(schemas)
    visited: set[int] = set()
    while pending:
        schema = pending.pop()
        identity = id(schema)
        if identity in visited:
            continue
        visited.add(identity)
        yield schema
        reference = schema.get("$ref")
        if isinstance(reference, str):
            resolved = _resolve_local_ref(root, reference)
            if resolved is not None:
                pending.append(resolved)
        for keyword in ("allOf", "anyOf", "oneOf"):
            composed = schema.get(keyword)
            if isinstance(composed, list):
                pending.extend(
                    cast(JsonObject, item) for item in composed if isinstance(item, dict)
                )
        for keyword in ("if", "then", "else", "not"):
            composed = schema.get(keyword)
            if isinstance(composed, dict):
                pending.append(cast(JsonObject, composed))


def _resolve_local_ref(root: JsonObject, reference: str) -> JsonObject | None:
    if not reference.startswith("#/"):
        return None
    current: object = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return cast(JsonObject, current) if isinstance(current, dict) else None


def _declared_property_names(schemas: list[JsonObject]) -> set[str]:
    result: set[str] = set()
    for schema in schemas:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            result.update(str(key) for key in properties)
    return result


def _property_schemas(schemas: list[JsonObject], key: str) -> list[JsonObject]:
    result: list[JsonObject] = []
    for schema in schemas:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            child = properties.get(key)
            if isinstance(child, dict):
                result.append(cast(JsonObject, child))
    return result


def _item_schemas(schemas: list[JsonObject]) -> list[JsonObject]:
    result: list[JsonObject] = []
    for schema in schemas:
        items = schema.get("items")
        if isinstance(items, dict):
            result.append(cast(JsonObject, items))
    return result


def _schema_is_sensitive(schema: JsonObject) -> bool:
    if schema.get("writeOnly") is True:
        return True
    if (
        schema.get("x-sensitive") is True
        or schema.get("x-chatwaifu-sensitive") is True
        or schema.get("x-chatwaifu-pii") is True
    ):
        return True
    if schema.get("contentEncoding") is not None or schema.get("contentMediaType") is not None:
        return True
    value_format = schema.get("format")
    return isinstance(value_format, str) and value_format.lower() in (
        _SECRET_FORMATS | _PRIVATE_FORMATS
    )


def _has_unresolved_reference(schema: JsonObject, root: JsonObject) -> bool:
    reference = schema.get("$ref")
    return isinstance(reference, str) and (
        not reference.startswith("#/") or _resolve_local_ref(root, reference) is None
    )


def _key_is_sensitive(key: str) -> bool:
    if _SECRET_KEY.search(key) or _PRIVATE_KEY.search(key):
        return True
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(compact.endswith(suffix) for suffix in _SECRET_COMPACT_SUFFIXES)


def _kind(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "number"


def _encode(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
