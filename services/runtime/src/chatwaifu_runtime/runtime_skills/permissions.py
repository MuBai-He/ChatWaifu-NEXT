"""Persistent permission grants and per-invocation confirmation policy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from chatwaifu_protocol.base import SideEffect
from chatwaifu_protocol.skills import SkillCapability

from chatwaifu_runtime.persistence.database import Database


class PermissionBroker:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def missing_permissions(
        self,
        *,
        principal: str,
        session_id: UUID,
        skill_id: str,
        capability: SkillCapability,
    ) -> list[str]:
        missing: list[str] = []
        for permission in capability.required_permissions:
            row = await self._database.fetchone(
                """
                SELECT 1 FROM permission_grants
                WHERE principal = ? AND skill_id = ? AND capability = ? AND permission = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (scope = 'always' OR (scope = 'session' AND session_id = ?))
                LIMIT 1
                """,
                (
                    principal,
                    skill_id,
                    capability.name,
                    permission,
                    _now().isoformat(),
                    str(session_id),
                ),
            )
            if row is None:
                missing.append(permission)
        return missing

    async def create_request(
        self,
        *,
        skill_run_id: UUID,
        principal: str,
        skill_id: str,
        capability: SkillCapability,
        missing_permissions: list[str],
    ) -> UUID:
        request_id = uuid4()
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO permission_requests(
                    request_id, skill_run_id, principal, skill_id, capability,
                    permissions_json, side_effect, reason, state, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    str(request_id),
                    str(skill_run_id),
                    principal,
                    skill_id,
                    capability.name,
                    json.dumps(missing_permissions),
                    capability.side_effect.value,
                    _reason(capability, missing_permissions),
                    now,
                ),
            )
            await connection.execute(
                """
                UPDATE skill_runs
                SET state = 'waiting_for_confirmation', confirmation_request_id = ?,
                    updated_at = ?
                WHERE skill_run_id = ?
                """,
                (str(request_id), now, str(skill_run_id)),
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
        row = await self._database.fetchone(
            "SELECT * FROM permission_requests WHERE request_id = ?", (str(request_id),)
        )
        if row is None:
            raise KeyError("confirmation request not found")
        if str(row["state"]) != "pending":
            raise ValueError("confirmation request is no longer pending")
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
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE permission_requests
                SET state = 'decided', decision = ?, decided_by = ?, decided_at = ?
                WHERE request_id = ?
                """,
                (decision, decided_by, now, str(request_id)),
            )
            if decision in {"allow_session", "allow_always"}:
                scope = "session" if decision == "allow_session" else "always"
                for permission in permissions:
                    await connection.execute(
                        """
                        INSERT INTO permission_grants(
                            grant_id, principal, skill_id, capability, permission,
                            scope, session_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            str(row["principal"]),
                            str(row["skill_id"]),
                            str(row["capability"]),
                            str(permission),
                            scope,
                            str(session_id) if scope == "session" else None,
                            now,
                        ),
                    )
        return UUID(str(row["skill_run_id"])), decision != "deny"

    async def list_pending(self, session_id: UUID) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT pr.* FROM permission_requests pr
            JOIN skill_runs sr ON sr.skill_run_id = pr.skill_run_id
            WHERE sr.session_id = ? AND pr.state = 'pending'
            ORDER BY pr.requested_at
            """,
            (str(session_id),),
        )
        return [
            {
                "request_id": str(row["request_id"]),
                "skill_run_id": str(row["skill_run_id"]),
                "skill_id": str(row["skill_id"]),
                "capability": str(row["capability"]),
                "permissions": json.loads(str(row["permissions_json"])),
                "side_effect": str(row["side_effect"]),
                "reason": str(row["reason"]),
                "requested_at": str(row["requested_at"]),
            }
            for row in rows
        ]


def _reason(capability: SkillCapability, missing_permissions: list[str]) -> str:
    if missing_permissions:
        return f"{capability.description} 需要权限: {', '.join(missing_permissions)}"
    return f"{capability.description} 会产生 {capability.side_effect.value} 副作用"


def _now() -> datetime:
    return datetime.now(UTC)
