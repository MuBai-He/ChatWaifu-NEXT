"""Persistent permission grants and per-invocation confirmation policy."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from chatwaifu_protocol.base import SideEffect
from chatwaifu_protocol.skills import SkillCapability

from chatwaifu_runtime.runtime_skills.execution_plan import ExecutionPlan
from chatwaifu_runtime.runtime_skills.repository import PermissionRepository

CONFIRMATION_TTL_SECONDS = 5 * 60


class PermissionBroker:
    def __init__(self, repository: PermissionRepository) -> None:
        self._repository = repository

    async def missing_permissions(
        self,
        *,
        principal: str,
        session_id: UUID,
        plan: ExecutionPlan,
    ) -> list[str]:
        capability = plan.capability
        missing: list[str] = []
        for permission in capability.required_permissions:
            granted = await self._repository.has_permission_grant(
                principal=principal,
                session_id=session_id,
                skill_id=plan.skill_id,
                capability=capability.name,
                permission=permission,
                subject_fingerprint=plan.permission_subject_fingerprint(),
                now=_now().isoformat(),
            )
            if not granted:
                missing.append(permission)
        return missing

    async def create_request(
        self,
        *,
        skill_run_id: UUID,
        principal: str,
        plan: ExecutionPlan,
        missing_permissions: list[str],
    ) -> UUID:
        capability = plan.capability
        request_id = uuid4()
        requested_at = _now()
        now = requested_at.isoformat()
        expires_at = (requested_at + timedelta(seconds=CONFIRMATION_TTL_SECONDS)).isoformat()
        await self._repository.create_permission_request(
            {
                "request_id": str(request_id),
                "skill_run_id": str(skill_run_id),
                "principal": principal,
                "skill_id": plan.skill_id,
                "skill_version": plan.skill_version,
                "capability": capability.name,
                "subject_fingerprint": plan.permission_subject_fingerprint(),
                "plugin_id": plan.plugin_id,
                "plugin_fingerprint": plan.plugin_fingerprint,
                "mcp_connection_id": (
                    str(plan.mcp_connection_id) if plan.mcp_connection_id is not None else None
                ),
                "mcp_connection_revision": plan.mcp_connection_revision,
                "permissions_json": json.dumps(missing_permissions),
                "side_effect": capability.side_effect.value,
                "reason": _reason(capability, missing_permissions),
                "requested_at": now,
                "expires_at": expires_at,
            }
        )
        return request_id

    async def decide(
        self,
        *,
        request_id: UUID,
        decision: str,
        decided_by: str,
        session_id: UUID,
    ) -> tuple[UUID, bool]:
        row = await self._repository.permission_request(request_id)
        if row is None:
            raise KeyError("confirmation request not found")
        if str(row["state"]) != "pending":
            raise ValueError("confirmation request is no longer pending")
        if datetime.fromisoformat(str(row["expires_at"])) <= _now():
            await self._expire_request(request_id, UUID(str(row["skill_run_id"])))
            raise ValueError("confirmation request has expired")
        side_effect = SideEffect(str(row["side_effect"]))
        if decision == "allow_always" and side_effect is not SideEffect.READ:
            raise ValueError("persistent grants are only allowed for read-only capabilities")
        if decision == "allow_session" and side_effect in {
            SideEffect.DESTRUCTIVE,
            SideEffect.EXTERNAL_COMMUNICATION,
            SideEffect.DEVICE_CONTROL,
        }:
            raise ValueError("this side effect requires confirmation on every invocation")
        if decision not in {"allow_once", "allow_session", "allow_always", "deny"}:
            raise ValueError("invalid confirmation decision")
        now = _now().isoformat()
        permissions = json.loads(str(row["permissions_json"]))
        decided = await self._repository.decide_permission_request(
            request_id=request_id,
            decision=decision,
            decided_by=decided_by,
            decided_at=now,
            session_id=session_id,
            grants=[str(permission) for permission in permissions],
        )
        if not decided:
            raise ValueError("confirmation request is no longer pending")
        return UUID(str(row["skill_run_id"])), decision != "deny"

    async def list_pending(self, session_id: UUID) -> tuple[list[dict[str, object]], list[UUID]]:
        expired_run_ids = await self.expire_pending()
        rows = await self._repository.pending_permission_requests(session_id, _now().isoformat())
        return (
            [
                {
                    "request_id": str(row["request_id"]),
                    "skill_run_id": str(row["skill_run_id"]),
                    "skill_id": str(row["skill_id"]),
                    "capability": str(row["capability"]),
                    "permissions": json.loads(str(row["permissions_json"])),
                    "side_effect": str(row["side_effect"]),
                    "reason": str(row["reason"]),
                    "requested_at": str(row["requested_at"]),
                    "expires_at": str(row["expires_at"]),
                    "allowed_decisions": _allowed_decisions(SideEffect(str(row["side_effect"]))),
                }
                for row in rows
            ],
            expired_run_ids,
        )

    async def expire_for_run(self, skill_run_id: UUID) -> bool:
        now = _now().isoformat()
        return await self._repository.expire_permission_for_run(skill_run_id, now)

    async def expire_pending(self) -> list[UUID]:
        now = _now().isoformat()
        return await self._repository.expire_pending_permissions(now)

    async def _expire_request(self, request_id: UUID, skill_run_id: UUID) -> None:
        now = _now().isoformat()
        await self._repository.expire_permission_for_run(skill_run_id, now)


def _reason(capability: SkillCapability, missing_permissions: list[str]) -> str:
    if missing_permissions:
        return f"{capability.description} 需要权限: {', '.join(missing_permissions)}"
    return f"{capability.description} 会产生 {capability.side_effect.value} 副作用"


def _allowed_decisions(side_effect: SideEffect) -> list[str]:
    decisions = ["deny", "allow_once"]
    if side_effect not in {
        SideEffect.DESTRUCTIVE,
        SideEffect.EXTERNAL_COMMUNICATION,
        SideEffect.DEVICE_CONTROL,
    }:
        decisions.insert(1, "allow_session")
    if side_effect is SideEffect.READ:
        decisions.insert(-1, "allow_always")
    return decisions


def _now() -> datetime:
    return datetime.now(UTC)
