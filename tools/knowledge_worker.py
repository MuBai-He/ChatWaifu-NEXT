"""Single-consumer graph worker. Owns no chat, audio, channel or reminder service."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import signal
from pathlib import Path

from chatwaifu_runtime.config.settings import load_settings
from chatwaifu_runtime.knowledge.adapters.graphiti_index import GraphitiIndex
from chatwaifu_runtime.knowledge.indexer import KnowledgeIndexer
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from chatwaifu_runtime.persistence.sqlite_knowledge_graph import SQLiteKnowledgeGraph
from chatwaifu_runtime.providers.model_config import ModelConfigurationService


async def run(config: Path, environment: Path, rebuild_index: bool = False) -> None:
    settings = load_settings(config, environment)
    if not settings.knowledge.enabled or settings.knowledge.neo4j_password is None:
        raise RuntimeError("knowledge_worker_not_configured")
    db = Database(settings.database_path, settings.storage)
    await db.open()
    models = ModelConfigurationService(db, settings)
    await models.start()
    index = GraphitiIndex(
        settings.knowledge.neo4j_uri,
        settings.knowledge.neo4j_user,
        settings.knowledge.neo4j_password.get_secret_value(),
        models,
    )
    graph = SQLiteKnowledgeGraph(
        db,
        SQLiteKnowledgeRepository(db),
        history_limit=settings.knowledge.history_daily_limit,
        history_enabled=settings.knowledge.history_enabled,
    )
    worker = KnowledgeIndexer(graph, index)
    current = asyncio.current_task()
    assert current is not None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, current.cancel)
    try:
        if rebuild_index:
            import json

            from chatwaifu_runtime.knowledge.rebuild import rebuild

            print(json.dumps(await rebuild(graph, index)))
            return
        await index.start()
        while True:
            await models.reload()
            if not await worker.run_once():
                await asyncio.sleep(5)  # Idle queue poll; only one worker holds the process lock.
    finally:
        await index.close()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    lock_path = args.config.parent / "knowledge-worker.lock"
    with lock_path.open("a") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            asyncio.run(run(args.config, args.env, args.rebuild))
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    main()
