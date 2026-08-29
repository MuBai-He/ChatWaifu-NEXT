"""SQLite adapter for the Runtime Skills persistence port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.runtime_skills.repository import Record


class SQLiteRuntimeSkillRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def expire_nonterminal_runs(self, error: StructuredError, now: str) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs SET state = 'expired', error_json = ?, updated_at = ?,
                    completed_at = ?
                WHERE state NOT IN ('succeeded', 'failed', 'cancelled', 'expired')
                """,
                (error.model_dump_json(), now, now),
            )

    async def has_active_reference(self, column: str, value: str) -> bool:
        if column not in {"plugin_id", "mcp_connection_id"}:
            raise ValueError("invalid active-run reference")
        row = await self._database.fetchone(
            f"SELECT 1 FROM skill_runs WHERE {column} = ? "
            "AND state NOT IN ('succeeded', 'failed', 'cancelled', 'expired') LIMIT 1",
            (value,),
        )
        return row is not None

    async def create_run(self, values: Mapping[str, object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO skill_runs(
                    skill_run_id, session_id, skill_id, skill_version, capability,
                    plugin_id, mcp_connection_id, state, arguments_json,
                    execution_plan_json, execution_plan_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?)
                """,
                tuple(
                    values[key]
                    for key in (
                        "skill_run_id",
                        "session_id",
                        "skill_id",
                        "skill_version",
                        "capability",
                        "plugin_id",
                        "mcp_connection_id",
                        "arguments_json",
                        "execution_plan_json",
                        "execution_plan_fingerprint",
                        "created_at",
                        "updated_at",
                    )
                ),
            )

    async def run(self, run_id: UUID) -> Record | None:
        return _record(
            await self._database.fetchone(
                "SELECT * FROM skill_runs WHERE skill_run_id = ?", (str(run_id),)
            )
        )

    async def run_for_confirmation(self, request_id: UUID) -> Record | None:
        return _record(
            await self._database.fetchone(
                "SELECT * FROM skill_runs WHERE confirmation_request_id = ?",
                (str(request_id),),
            )
        )

    async def runs_for_session(self, session_id: UUID, limit: int) -> list[Record]:
        return _records(
            await self._database.fetchall(
                "SELECT * FROM skill_runs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(session_id), limit),
            )
        )

    async def mark_run_cancelling(self, run_id: UUID, now: str) -> None:
        await self._update_run(
            "cancel_requested = 1, state = 'cancelling', updated_at = ?", (now,), run_id
        )

    async def mark_run_running(self, run_id: UUID, now: str) -> None:
        await self._update_run(
            "state = 'running', started_at = COALESCE(started_at, ?), updated_at = ?",
            (now, now),
            run_id,
        )

    async def complete_run(self, run_id: UUID, result_json: str, now: str) -> None:
        await self._update_run(
            "state = 'succeeded', progress = 1, result_json = ?, updated_at = ?, completed_at = ?",
            (result_json, now, now),
            run_id,
        )

    async def fail_run(self, run_id: UUID, error_json: str, now: str) -> None:
        await self._update_run(
            "state = 'failed', error_json = ?, updated_at = ?, completed_at = ?",
            (error_json, now, now),
            run_id,
        )

    async def cancel_run(self, run_id: UUID, now: str) -> None:
        await self._update_run(
            "state = 'cancelled', updated_at = ?, completed_at = ?", (now, now), run_id
        )

    async def _update_run(
        self, assignment: str, parameters: tuple[object, ...], run_id: UUID
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                f"UPDATE skill_runs SET {assignment} WHERE skill_run_id = ?",
                (*parameters, str(run_id)),
            )

    async def start_tool_call(self, values: Mapping[str, object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO skill_tool_calls(
                    tool_call_id, skill_run_id, adapter, method, request_json,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                tuple(
                    values[key]
                    for key in (
                        "tool_call_id",
                        "skill_run_id",
                        "adapter",
                        "method",
                        "request_json",
                        "started_at",
                    )
                ),
            )

    async def finish_tool_call(self, values: Mapping[str, object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_tool_calls SET status = ?, response_json = ?, error_json = ?,
                    completed_at = ? WHERE tool_call_id = ?
                """,
                tuple(
                    values[key]
                    for key in (
                        "status",
                        "response_json",
                        "error_json",
                        "completed_at",
                        "tool_call_id",
                    )
                ),
            )

    async def has_permission_grant(
        self,
        *,
        principal: str,
        session_id: UUID,
        skill_id: str,
        capability: str,
        permission: str,
        subject_fingerprint: str,
        now: str,
    ) -> bool:
        row = await self._database.fetchone(
            """
            SELECT 1 FROM permission_grants
            WHERE principal = ? AND skill_id = ? AND capability = ? AND permission = ?
              AND subject_fingerprint = ?
              AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
              AND (scope = 'always' OR (scope = 'session' AND session_id = ?))
            LIMIT 1
            """,
            (
                principal,
                skill_id,
                capability,
                permission,
                subject_fingerprint,
                now,
                str(session_id),
            ),
        )
        return row is not None

    async def create_permission_request(self, values: Mapping[str, object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO permission_requests(
                    request_id, skill_run_id, principal, skill_id, skill_version,
                    capability, subject_fingerprint, plugin_id, plugin_fingerprint,
                    mcp_connection_id, mcp_connection_revision, permissions_json,
                    side_effect, reason, state, requested_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                tuple(
                    values[key]
                    for key in (
                        "request_id",
                        "skill_run_id",
                        "principal",
                        "skill_id",
                        "skill_version",
                        "capability",
                        "subject_fingerprint",
                        "plugin_id",
                        "plugin_fingerprint",
                        "mcp_connection_id",
                        "mcp_connection_revision",
                        "permissions_json",
                        "side_effect",
                        "reason",
                        "requested_at",
                        "expires_at",
                    )
                ),
            )
            await connection.execute(
                "UPDATE skill_runs SET state = 'waiting_for_confirmation', "
                "confirmation_request_id = ?, updated_at = ? WHERE skill_run_id = ?",
                (values["request_id"], values["requested_at"], values["skill_run_id"]),
            )

    async def permission_request(self, request_id: UUID) -> Record | None:
        return _record(
            await self._database.fetchone(
                "SELECT * FROM permission_requests WHERE request_id = ?", (str(request_id),)
            )
        )

    async def decide_permission_request(
        self,
        *,
        request_id: UUID,
        decision: str,
        decided_by: str,
        decided_at: str,
        session_id: UUID,
        grants: list[str],
    ) -> bool:
        async with self._database.transaction() as connection:
            row = await connection.execute_fetchall(
                "SELECT * FROM permission_requests WHERE request_id = ?",
                (str(request_id),),
            )
            if not row:
                return False
            request = next(iter(row))
            cursor = await connection.execute(
                """
                UPDATE permission_requests SET state = 'decided', decision = ?,
                    decided_by = ?, decided_at = ?
                WHERE request_id = ? AND state = 'pending' AND expires_at > ?
                """,
                (decision, decided_by, decided_at, str(request_id), decided_at),
            )
            if cursor.rowcount != 1:
                return False
            scope = "session" if decision == "allow_session" else "always"
            if decision in {"allow_session", "allow_always"}:
                for permission in grants:
                    await connection.execute(
                        """
                        INSERT INTO permission_grants(
                            grant_id, principal, skill_id, skill_version, capability,
                            permission, subject_fingerprint, plugin_id, plugin_fingerprint,
                            mcp_connection_id, mcp_connection_revision, scope, session_id,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            request["principal"],
                            request["skill_id"],
                            request["skill_version"],
                            request["capability"],
                            permission,
                            request["subject_fingerprint"],
                            request["plugin_id"],
                            request["plugin_fingerprint"],
                            request["mcp_connection_id"],
                            request["mcp_connection_revision"],
                            scope,
                            str(session_id) if scope == "session" else None,
                            decided_at,
                        ),
                    )
        return True

    async def pending_permission_requests(self, session_id: UUID, now: str) -> list[Record]:
        return _records(
            await self._database.fetchall(
                """
                SELECT pr.* FROM permission_requests pr
                JOIN skill_runs sr ON sr.skill_run_id = pr.skill_run_id
                WHERE sr.session_id = ? AND pr.state = 'pending' AND pr.expires_at > ?
                ORDER BY pr.requested_at
                """,
                (str(session_id), now),
            )
        )

    async def expire_permission_for_run(self, run_id: UUID, now: str) -> None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "UPDATE permission_requests SET state = 'expired', decided_at = ? "
                "WHERE skill_run_id = ? AND state = 'pending'",
                (now, str(run_id)),
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE skill_runs SET state = 'expired', updated_at = ?, completed_at = ? "
                    "WHERE skill_run_id = ? AND state = 'waiting_for_confirmation'",
                    (now, now, str(run_id)),
                )

    async def expire_pending_permissions(self, now: str) -> list[UUID]:
        async with self._database.transaction() as connection:
            rows = await connection.execute_fetchall(
                "SELECT skill_run_id FROM permission_requests "
                "WHERE state = 'pending' AND expires_at <= ?",
                (now,),
            )
            await connection.execute(
                "UPDATE permission_requests SET state = 'expired', decided_at = ? "
                "WHERE state = 'pending' AND expires_at <= ?",
                (now, now),
            )
            for row in rows:
                await connection.execute(
                    "UPDATE skill_runs SET state = 'expired', updated_at = ?, completed_at = ? "
                    "WHERE skill_run_id = ? AND state = 'waiting_for_confirmation'",
                    (now, now, row["skill_run_id"]),
                )
        return [UUID(str(row["skill_run_id"])) for row in rows]

    async def list_plugin_records(self) -> list[Record]:
        return _records(
            await self._database.fetchall(
                "SELECT * FROM skill_plugins ORDER BY name COLLATE NOCASE, plugin_id"
            )
        )

    async def plugin_record(self, plugin_id: str) -> Record | None:
        return _record(
            await self._database.fetchone(
                "SELECT * FROM skill_plugins WHERE plugin_id = ?", (plugin_id,)
            )
        )

    async def insert_plugin(self, values: Mapping[str, object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO skill_plugins(
                    plugin_id, version, name, description, install_path,
                    manifest_json, enabled, trust_level, sandbox_mode,
                    network_policy, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                tuple(
                    values[key]
                    for key in (
                        "plugin_id",
                        "version",
                        "name",
                        "description",
                        "install_path",
                        "manifest_json",
                        "trust_level",
                        "sandbox_mode",
                        "network_policy",
                        "installed_at",
                        "updated_at",
                    )
                ),
            )

    async def set_plugin_enabled(self, plugin_id: str, enabled: bool, now: str) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "UPDATE skill_plugins SET enabled = ?, updated_at = ? WHERE plugin_id = ?",
                (int(enabled), now, plugin_id),
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE permission_grants SET revoked_at = ? "
                    "WHERE plugin_id = ? AND revoked_at IS NULL",
                    (now, plugin_id),
                )
            return cursor.rowcount == 1

    async def set_plugin_sandbox_backend(
        self, plugin_id: str, backend: str | None, limits_json: str, now: str
    ) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "UPDATE skill_plugins SET sandbox_backend = ?, sandbox_limits_json = ?, "
                "updated_at = ? "
                "WHERE plugin_id = ?",
                (backend, limits_json, now, plugin_id),
            )
            return cursor.rowcount == 1

    async def delete_plugin(self, plugin_id: str) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM skill_plugins WHERE plugin_id = ?", (plugin_id,)
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE permission_grants SET revoked_at = COALESCE(revoked_at, created_at) "
                    "WHERE plugin_id = ? AND revoked_at IS NULL",
                    (plugin_id,),
                )
            return cursor.rowcount == 1

    async def list_mcp_connection_records(self) -> list[Record]:
        return _records(
            await self._database.fetchall(
                "SELECT * FROM mcp_connections ORDER BY name COLLATE NOCASE, connection_id"
            )
        )

    async def mcp_connection_record(self, connection_id: UUID) -> Record | None:
        return _record(
            await self._database.fetchone(
                "SELECT * FROM mcp_connections WHERE connection_id = ?", (str(connection_id),)
            )
        )

    async def insert_mcp_connection(self, values: Mapping[str, object]) -> None:
        keys = (
            "connection_id",
            "name",
            "transport",
            "command_json",
            "url",
            "allow_remote",
            "enabled",
            "timeout_seconds",
            "trust_level",
            "sandbox_mode",
            "network_policy",
            "bearer_token_configured",
            "status",
            "capabilities_json",
            "created_at",
            "updated_at",
        )
        async with self._database.transaction() as connection:
            await connection.execute(
                f"INSERT INTO mcp_connections({', '.join(keys)}) "
                f"VALUES ({', '.join('?' for _ in keys)})",
                tuple(values[key] for key in keys),
            )

    async def update_mcp_connection(self, values: Mapping[str, object]) -> bool:
        keys = (
            "name",
            "transport",
            "command_json",
            "url",
            "allow_remote",
            "enabled",
            "timeout_seconds",
            "trust_level",
            "sandbox_mode",
            "network_policy",
            "bearer_token_configured",
            "status",
            "capabilities_json",
            "updated_at",
        )
        assignment = ", ".join(f"{key} = ?" for key in keys)
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                f"UPDATE mcp_connections SET {assignment}, sandbox_backend = NULL, "
                "sandbox_limits_json = '[]', last_error = NULL, last_tested_at = NULL, "
                "revision = revision + 1 "
                "WHERE connection_id = ?",
                (*tuple(values[key] for key in keys), values["connection_id"]),
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE permission_grants SET revoked_at = ? "
                    "WHERE mcp_connection_id = ? AND revoked_at IS NULL",
                    (values["updated_at"], values["connection_id"]),
                )
            return cursor.rowcount == 1

    async def delete_mcp_connection(self, connection_id: UUID) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM mcp_connections WHERE connection_id = ?", (str(connection_id),)
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE permission_grants SET revoked_at = COALESCE(revoked_at, created_at) "
                    "WHERE mcp_connection_id = ? AND revoked_at IS NULL",
                    (str(connection_id),),
                )
            return cursor.rowcount == 1

    async def mcp_connection_revision(self, connection_id: UUID) -> int | None:
        row = await self._database.fetchone(
            "SELECT revision FROM mcp_connections WHERE connection_id = ?",
            (str(connection_id),),
        )
        return int(row["revision"]) if row is not None else None

    async def reconcile_mcp_secret_flags(self, configured_ids: set[str]) -> None:
        rows = await self._database.fetchall("SELECT connection_id FROM mcp_connections")
        async with self._database.transaction() as connection:
            for row in rows:
                connection_id = str(row["connection_id"])
                await connection.execute(
                    "UPDATE mcp_connections SET bearer_token_configured = ? "
                    "WHERE connection_id = ?",
                    (int(connection_id in configured_ids), connection_id),
                )

    async def mark_mcp_test_error(
        self,
        connection_id: UUID,
        message: str,
        sandbox_backend: str | None,
        sandbox_limits_json: str,
        now: str,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "UPDATE mcp_connections SET status = 'error', last_error = ?, "
                "sandbox_backend = ?, sandbox_limits_json = ?, last_tested_at = ?, "
                "revision = revision + 1, "
                "updated_at = ? WHERE connection_id = ?",
                (
                    message[:2_000],
                    sandbox_backend,
                    sandbox_limits_json,
                    now,
                    now,
                    str(connection_id),
                ),
            )
            await connection.execute(
                "UPDATE permission_grants SET revoked_at = ? "
                "WHERE mcp_connection_id = ? AND revoked_at IS NULL",
                (now, str(connection_id)),
            )

    async def mark_mcp_test_ready(
        self,
        connection_id: UUID,
        capabilities_json: str,
        sandbox_backend: str | None,
        sandbox_limits_json: str,
        now: str,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "UPDATE mcp_connections SET status = 'ready', capabilities_json = ?, "
                "sandbox_backend = ?, sandbox_limits_json = ?, last_error = NULL, "
                "last_tested_at = ?, "
                "revision = revision + 1, updated_at = ? WHERE connection_id = ?",
                (
                    capabilities_json,
                    sandbox_backend,
                    sandbox_limits_json,
                    now,
                    now,
                    str(connection_id),
                ),
            )
            await connection.execute(
                "UPDATE permission_grants SET revoked_at = ? "
                "WHERE mcp_connection_id = ? AND revoked_at IS NULL",
                (now, str(connection_id)),
            )


def _record(row: object | None) -> Record | None:
    return dict(row) if row is not None else None  # type: ignore[arg-type]


def _records(rows: Sequence[object]) -> list[Record]:
    return [dict(row) for row in rows]  # type: ignore[arg-type]
