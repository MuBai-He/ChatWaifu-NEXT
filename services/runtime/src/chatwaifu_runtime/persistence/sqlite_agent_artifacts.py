"""Scoped persistent artifact references."""

from uuid import UUID

from chatwaifu_protocol.agent import ArtifactRef

from chatwaifu_runtime.persistence.database import Database


class SQLiteArtifactRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def put(self, scope: str, artifact: ArtifactRef, relative_path: str) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "INSERT INTO agent_artifacts(artifact_id,scope,payload_json,relative_path) "
                "VALUES(?,?,?,?)",
                (str(artifact.artifact_id), scope, artifact.model_dump_json(), relative_path),
            )

    async def get(self, scope: str, artifact_id: UUID) -> tuple[ArtifactRef, str] | None:
        row = await self._database.fetchone(
            "SELECT payload_json,relative_path FROM agent_artifacts "
            "WHERE artifact_id=? AND scope=?",
            (str(artifact_id), scope),
        )
        return (
            (ArtifactRef.model_validate_json(row["payload_json"]), str(row["relative_path"]))
            if row is not None
            else None
        )

    async def list(self, scope: str, session_id: UUID) -> list[ArtifactRef]:
        rows = await self._database.fetchall(
            "SELECT payload_json FROM agent_artifacts WHERE scope=? ORDER BY rowid DESC LIMIT 100",
            (scope,),
        )
        return [ArtifactRef.model_validate_json(row["payload_json"]) for row in rows]
