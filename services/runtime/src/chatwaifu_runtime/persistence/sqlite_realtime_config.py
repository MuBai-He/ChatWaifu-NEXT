"""SQLite repository for versioned realtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chatwaifu_runtime.persistence.database import Database

type ConnectionMode = Literal["cascade", "cloud_realtime"]
type CloudBackend = Literal["openai", "fake"]


@dataclass(frozen=True, slots=True)
class RealtimeConfigurationRecord:
    id: str
    schema_version: str
    revision: int
    connection_mode: ConnectionMode
    cloud_backend: CloudBackend
    model: str
    voice: str
    transcription_model: str
    cloud_tools_enabled: bool
    cloud_egress_consent: bool
    secret_key_ref: str | None
    updated_at: str


class SQLiteRealtimeConfigurationRepository:
    """Stores non-secret versioned realtime configuration in SQLite with revision CAS."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_configuration(self) -> RealtimeConfigurationRecord | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT id, schema_version, revision, connection_mode, cloud_backend,
                       model, voice, transcription_model, cloud_tools_enabled,
                       cloud_egress_consent, secret_key_ref, updated_at
                FROM realtime_configurations
                WHERE id = 'default'
                """
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            return RealtimeConfigurationRecord(
                id=str(row[0]),
                schema_version=str(row[1]),
                revision=int(row[2]),
                connection_mode=row[3],
                cloud_backend=row[4],
                model=str(row[5]),
                voice=str(row[6]),
                transcription_model=str(row[7]),
                cloud_tools_enabled=bool(row[8]),
                cloud_egress_consent=bool(row[9]),
                secret_key_ref=str(row[10]) if row[10] is not None else None,
                updated_at=str(row[11]),
            )

    async def insert_configuration(self, record: RealtimeConfigurationRecord) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO realtime_configurations (
                    id, schema_version, revision, connection_mode, cloud_backend,
                    model, voice, transcription_model, cloud_tools_enabled,
                    cloud_egress_consent, secret_key_ref, updated_at
                ) VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.schema_version,
                    record.revision,
                    record.connection_mode,
                    record.cloud_backend,
                    record.model,
                    record.voice,
                    record.transcription_model,
                    int(record.cloud_tools_enabled),
                    int(record.cloud_egress_consent),
                    record.secret_key_ref,
                    record.updated_at,
                ),
            )

    async def update_configuration_cas(
        self, expected_revision: int, record: RealtimeConfigurationRecord
    ) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE realtime_configurations
                SET schema_version = ?,
                    revision = ?,
                    connection_mode = ?,
                    cloud_backend = ?,
                    model = ?,
                    voice = ?,
                    transcription_model = ?,
                    cloud_tools_enabled = ?,
                    cloud_egress_consent = ?,
                    secret_key_ref = ?,
                    updated_at = ?
                WHERE id = 'default' AND revision = ?
                """,
                (
                    record.schema_version,
                    record.revision,
                    record.connection_mode,
                    record.cloud_backend,
                    record.model,
                    record.voice,
                    record.transcription_model,
                    int(record.cloud_tools_enabled),
                    int(record.cloud_egress_consent),
                    record.secret_key_ref,
                    record.updated_at,
                    expected_revision,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            return updated
