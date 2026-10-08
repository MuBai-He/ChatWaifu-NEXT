"""Durable candidate states and atomic daily budget."""

from uuid import UUID

from chatwaifu_protocol.agent import AgentDevelopmentPolicy, CandidateFeature

from chatwaifu_runtime.persistence.database import Database


class SQLiteDevelopmentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def policy(self) -> AgentDevelopmentPolicy:
        row = await self.database.fetchone(
            "SELECT payload_json FROM agent_development_policy WHERE singleton=1"
        )
        return (
            AgentDevelopmentPolicy.model_validate_json(row["payload_json"])
            if row
            else AgentDevelopmentPolicy()
        )

    async def configure(self, policy: AgentDevelopmentPolicy, expected_revision: int) -> bool:
        if policy.revision != expected_revision + 1:
            raise ValueError("development revision must advance once")
        async with self.database.transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT revision FROM agent_development_policy WHERE singleton=1"
                )
            ).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                return False
            await connection.execute(
                "INSERT INTO agent_development_policy(singleton,revision,payload_json) "
                "VALUES(1,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,"
                "payload_json=excluded.payload_json",
                (policy.revision, policy.model_dump_json()),
            )
            return True

    async def reserve(self, candidate: CandidateFeature, day: str) -> bool:
        async with self.database.transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT payload_json FROM agent_development_policy WHERE singleton=1"
                )
            ).fetchone()
            if (
                row is None
                or not AgentDevelopmentPolicy.model_validate_json(row["payload_json"]).enabled
            ):
                return False
            result = await connection.execute(
                "INSERT OR IGNORE INTO agent_candidates(candidate_id,day,payload_json,revision) "
                "VALUES(?,?,?,?)",
                (str(candidate.candidate_id), day, candidate.model_dump_json(), candidate.revision),
            )
            return result.rowcount == 1

    async def get(self, candidate_id: UUID) -> CandidateFeature | None:
        row = await self.database.fetchone(
            "SELECT payload_json FROM agent_candidates WHERE candidate_id=?", (str(candidate_id),)
        )
        return CandidateFeature.model_validate_json(row["payload_json"]) if row else None

    async def list(self) -> list[CandidateFeature]:
        rows = await self.database.fetchall(
            "SELECT payload_json FROM agent_candidates ORDER BY day DESC LIMIT 100"
        )
        return [CandidateFeature.model_validate_json(r["payload_json"]) for r in rows]

    async def save(self, candidate: CandidateFeature, expected_revision: int) -> bool:
        if candidate.revision != expected_revision + 1:
            raise ValueError("candidate revision must advance once")
        async with self.database.transaction() as connection:
            result = await connection.execute(
                "UPDATE agent_candidates SET payload_json=?,revision=? "
                "WHERE candidate_id=? AND revision=?",
                (
                    candidate.model_dump_json(),
                    candidate.revision,
                    str(candidate.candidate_id),
                    expected_revision,
                ),
            )
            return result.rowcount == 1
