"""Immutable execution plans approved by the Runtime permission boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import SkillCapability
from pydantic import BaseModel, ConfigDict, Field

from chatwaifu_runtime.runtime_skills.audit import payload_digest, sanitize_audit_payload
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.registry import RegistryEntry

MAX_FINGERPRINT_FILES = 256
MAX_FINGERPRINT_BYTES = 16 * 1024 * 1024


class ExecutionPlan(BaseModel):
    """The exact capability and adapter identity authorized for one invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    skill_id: str
    skill_version: str
    capability: SkillCapability
    adapter_kind: Literal["builtin", "mcp", "mcp_connection"]
    adapter_target: str
    interruptible: bool
    background_allowed: bool
    background_requested: bool
    audit_public_fields_allowed: bool = False
    plugin_id: str | None = None
    plugin_fingerprint: str | None = None
    mcp_connection_id: UUID | None = None
    mcp_connection_revision: int | None = Field(default=None, ge=1)
    arguments_digest: str
    arguments_summary: JsonObject
    created_at: datetime

    def fingerprint(self) -> str:
        encoded = self.model_dump_json(exclude={"created_at"}).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def permission_subject_fingerprint(self) -> str:
        """Bind reusable grants to the exact executable capability identity."""

        subject = {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "capability": self.capability.model_dump(mode="json"),
            "adapter_kind": self.adapter_kind,
            "adapter_target": self.adapter_target,
            "plugin_id": self.plugin_id,
            "plugin_fingerprint": self.plugin_fingerprint,
            "mcp_connection_id": (
                str(self.mcp_connection_id) if self.mcp_connection_id is not None else None
            ),
            "mcp_connection_revision": self.mcp_connection_revision,
        }
        encoded = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def build_execution_plan(
    entry: RegistryEntry,
    capability: SkillCapability,
    arguments: JsonObject,
    *,
    audit_digest_key: bytes,
    mcp_connection_revision: int | None = None,
    background_requested: bool = False,
) -> ExecutionPlan:
    plugin_fingerprint = None
    if entry.plugin is not None:
        if entry.plugin_root is None:
            raise SkillExecutionError(
                "plugin_package_missing", "Plugin package metadata is incomplete"
            )
        plugin_fingerprint = _plugin_package_fingerprint(entry.plugin_root)
    summary = sanitize_audit_payload(
        arguments,
        capability.input_schema,
        allow_schema_public=entry.audit_public_fields_allowed,
    )
    assert isinstance(summary, dict)
    return ExecutionPlan(
        skill_id=entry.definition.skill_id,
        skill_version=entry.definition.version,
        capability=capability,
        adapter_kind=entry.adapter.kind,
        adapter_target=capability.adapter_tool or entry.adapter.target,
        interruptible=entry.definition.interruptible,
        background_allowed=entry.definition.background_allowed,
        background_requested=background_requested,
        audit_public_fields_allowed=entry.audit_public_fields_allowed,
        plugin_id=entry.definition.plugin_id,
        plugin_fingerprint=plugin_fingerprint,
        mcp_connection_id=entry.definition.mcp_connection_id,
        mcp_connection_revision=mcp_connection_revision,
        arguments_digest=payload_digest(arguments, key=audit_digest_key),
        arguments_summary=summary,
        created_at=datetime.now(UTC),
    )


def plan_matches_entry(
    plan: ExecutionPlan,
    entry: RegistryEntry,
    capability: SkillCapability,
    *,
    audit_digest_key: bytes,
    mcp_connection_revision: int | None = None,
) -> bool:
    candidate = build_execution_plan(
        entry,
        capability,
        {},
        audit_digest_key=audit_digest_key,
        mcp_connection_revision=mcp_connection_revision,
        background_requested=plan.background_requested,
    )
    return (
        plan.skill_id == candidate.skill_id
        and plan.skill_version == candidate.skill_version
        and plan.capability == candidate.capability
        and plan.adapter_kind == candidate.adapter_kind
        and plan.adapter_target == candidate.adapter_target
        and plan.interruptible == candidate.interruptible
        and plan.background_allowed == candidate.background_allowed
        and plan.audit_public_fields_allowed == candidate.audit_public_fields_allowed
        and plan.plugin_id == candidate.plugin_id
        and plan.plugin_fingerprint == candidate.plugin_fingerprint
        and plan.mcp_connection_id == candidate.mcp_connection_id
        and plan.mcp_connection_revision == candidate.mcp_connection_revision
    )


def _plugin_package_fingerprint(root: Path) -> str:
    """Digest the executable package, not only its manifest.

    Installed packages are chmod read-only, but the fingerprint is re-evaluated
    at approval and execution so an owner-level mutation still invalidates the
    approved plan before any plugin process starts.
    """

    resolved = root.resolve()
    digest = hashlib.sha256()
    count = 0
    total = 0
    try:
        paths = sorted(resolved.rglob("*"), key=lambda path: path.relative_to(resolved).as_posix())
        for path in paths:
            if path.is_symlink():
                raise SkillExecutionError(
                    "plugin_package_changed", "Plugin package contains a symbolic link"
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise SkillExecutionError(
                    "plugin_package_changed", "Plugin package contains a special file"
                )
            count += 1
            size = path.stat().st_size
            total += size
            if count > MAX_FINGERPRINT_FILES or total > MAX_FINGERPRINT_BYTES:
                raise SkillExecutionError(
                    "plugin_package_changed", "Plugin package exceeds the approved size limits"
                )
            relative = path.relative_to(resolved).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(size.to_bytes(8, "big"))
            with path.open("rb") as package_file:
                while chunk := package_file.read(64 * 1024):
                    digest.update(chunk)
    except SkillExecutionError:
        raise
    except OSError as error:
        raise SkillExecutionError(
            "plugin_package_changed", "Plugin package could not be verified"
        ) from error
    return digest.hexdigest()
