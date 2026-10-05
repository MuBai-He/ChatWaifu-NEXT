"""A single non-secret operator policy with transactional revision fencing."""

from datetime import datetime

from chatwaifu_protocol.channel_settings import ChannelRuntimePolicy, ChannelRuntimeSettingsSnapshot

from chatwaifu_runtime.persistence.database import Database


class SQLiteChannelSettingsRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self) -> ChannelRuntimeSettingsSnapshot | None:
        row = await self._database.fetchone(
            "SELECT revision,policy_json,updated_at FROM channel_runtime_settings "
            "WHERE singleton_id=1"
        )
        if row is None:
            return None
        return ChannelRuntimeSettingsSnapshot(
            revision=int(row["revision"]),
            policy=ChannelRuntimePolicy.model_validate_json(row["policy_json"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def save(
        self, policy: ChannelRuntimePolicy, expected_revision: int, now: datetime
    ) -> ChannelRuntimeSettingsSnapshot | None:
        async with self._database.transaction() as connection:
            if expected_revision == 0:
                cursor = await connection.execute(
                    "INSERT OR IGNORE INTO channel_runtime_settings"
                    "(singleton_id,revision,policy_json,updated_at) VALUES(1,1,?,?)",
                    (policy.model_dump_json(), now.isoformat()),
                )
            else:
                cursor = await connection.execute(
                    "UPDATE channel_runtime_settings SET revision=revision+1,policy_json=?,"
                    "updated_at=? WHERE singleton_id=1 AND revision=?",
                    (policy.model_dump_json(), now.isoformat(), expected_revision),
                )
            success = cursor.rowcount == 1
            await cursor.close()
        if not success:
            return None
        return ChannelRuntimeSettingsSnapshot(
            revision=expected_revision + 1, policy=policy, updated_at=now
        )
