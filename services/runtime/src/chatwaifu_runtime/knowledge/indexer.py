"""Leased indexing with cancellation, policy fencing and compensating index cleanup."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.knowledge.contracts import Projection, Record


class GraphIndex(Protocol):
    async def close(self) -> None: ...
    async def index(self, owner: str, record: Record) -> Projection: ...
    async def remove(self, owner: str, record: Record) -> None: ...
    async def search(
        self, owner: str, question: str, rooms: list[str], limit: int = 30
    ) -> list[str]: ...


class IndexJobs(Protocol):
    async def claim(self) -> JsonObject | None: ...
    async def renew(self, job: JsonObject) -> bool: ...
    async def publish(self, job: JsonObject, projection: Projection) -> bool: ...
    async def complete_removal(self, job: JsonObject) -> bool: ...
    async def fail(
        self, job: JsonObject, cancelled: bool = False, error_code: str = "index_failed"
    ) -> None: ...
    async def versions(self, owner: str, record_id: str) -> list[Record]: ...


class KnowledgeIndexer:
    def __init__(self, jobs: IndexJobs, index: GraphIndex, *, lease_interval: float = 30) -> None:
        self.jobs, self.index = jobs, index
        self.lease_interval = lease_interval
        self.last_error_code: str | None = None

    async def run_once(self) -> bool:
        job = await self.jobs.claim()
        if job is None:
            return False
        record = Record.model_validate(job["record"])
        owner = str(job["owner"])
        current = asyncio.current_task()
        assert current is not None

        lease_lost = False

        async def process() -> None:
            if job["action"] == "remove":
                for previous in await self.jobs.versions(owner, record.record_id):
                    await self.index.remove(owner, previous)
                await self.jobs.complete_removal(job)
            else:
                projection = await self.index.index(owner, record)
                if not await self.jobs.publish(job, projection):
                    await self.index.remove(owner, record)
                    await self.jobs.fail(job, cancelled=True)

        # A revoked lease cancels only its episode, never the long-running worker task.
        episode = asyncio.create_task(process())

        async def renew() -> None:
            nonlocal lease_lost
            while True:
                await asyncio.sleep(self.lease_interval)
                if not await self.jobs.renew(job):
                    lease_lost = True
                    episode.cancel()
                    return

        heartbeat = asyncio.create_task(renew())
        try:
            await episode
            return True
        except asyncio.CancelledError:
            # External shutdown still propagates, including a race with policy revocation.
            await asyncio.shield(self.jobs.fail(job, cancelled=True))
            if not lease_lost or current.cancelling():
                raise
            return True
        except Exception as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            code = f"provider_http_{status}" if isinstance(status, int) else type(error).__name__
            self.last_error_code = code[:80]
            logging.getLogger(__name__).warning(
                "knowledge_index_failed",
                extra={"error_code": self.last_error_code, "job_id": job["job_id"]},
            )
            await self.jobs.fail(job, error_code=self.last_error_code)
            return True
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
