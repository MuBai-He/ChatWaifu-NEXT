"""Run synthetic-only Graphiti/provider acceptance in an isolated server directory.

Never opens the production database through the migration runner. Only model configuration rows
are read from it; private provider credentials stay on the server and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.knowledge.adapters.graphiti_index import GraphitiIndex
from chatwaifu_runtime.knowledge.contracts import Record
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.model_config import ModelConfigurationService


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--production-state", type=Path, required=True)
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
    args = parser.parse_args()
    root = Path(args.workspace)
    production = Path(args.production_state)
    run_id = str(uuid4())
    state = root / ("fixture-" + run_id)
    state.mkdir(mode=0o700)
    settings = Settings.model_validate(
        {
            "config_dir": production / "config",
            "data_dir": state,
            "storage": StorageConfig(database_path=state / "runtime.db"),
            "llm": {"provider": "demo"},
            "tts": {"provider": "fake"},
        }
    )
    db = Database(settings.database_path, settings.storage)
    await db.open()
    models = ModelConfigurationService(db, settings)
    await models.start()
    loaded = False
    for path in (production / "data").glob("*.db"):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
            try:
                rows = source.execute(
                    "SELECT role,provider,model,base_url,timeout_seconds,context_window,"
                    "enabled,updated_at,budget_json FROM model_role_configs"
                ).fetchall()
            except sqlite3.Error:
                continue
        async with db.transaction() as connection:
            await connection.executemany(
                "INSERT OR REPLACE INTO model_role_configs VALUES(?,?,?,?,?,?,?,?,?)", rows
            )
        loaded = True
        break
    if not loaded:
        raise RuntimeError("model_configuration_not_found")
    await models.reload()
    auth = next(
        line.split("=", 1)[1]
        for line in (root / "neo4j.env").read_text().splitlines()
        if line.startswith("NEO4J_AUTH=")
    )
    user, password = auth.split("/", 1)
    graph = GraphitiIndex(args.neo4j_uri, user, password, models)
    records: list[Record] = []
    summary: dict[str, Any] = {
        "fixture_id": run_id,
        "status": "running",
        "raw_user_data_used": False,
    }
    try:
        await graph.start()
        vectors = await models.embed(
            ["项目评审改到星期五下午四点", "会议时间调整为周五16点", "我喜欢甜味冰淇淋"]
        )
        assert len(vectors) == 3 and len(vectors[0]) > 64, "actual_multilingual_embedding_required"
        import math

        def cosine(a: Sequence[float], b: Sequence[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))

        assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2]), (
            "Chinese semantic ordering"
        )
        owner = "fixture:" + run_id
        for number, text, when in [
            (
                1,
                "我负责青禾项目，小林负责提供设计稿。项目评审定在2026年10月15日下午三点。",
                1791800000000,
            ),
            (
                2,
                "青禾项目评审改为2026年10月16日下午四点，原来10月15日的安排取消。小林负责设计稿这件事不变。",
                1791886400000,
            ),
        ]:
            record = Record(
                record_id=f"fixture:{run_id}:{number}",
                room_id="fixture:room",
                kind="source",
                text=text,
                title="合成验证资料",
                occurred_at=when,
                updated_at=when,
                revision=1,
                sender_id="fixture:owner",
                sender_name="我",
                sent_by_owner=True,
            )
            records.append(record)
            start = time.monotonic()
            projection = await graph.index(owner, record)
            assert projection.nodes and projection.relations, "nonempty graph required"
            summary[f"episode_{number}"] = {
                "nodes": len(projection.nodes),
                "relations": len(projection.relations),
                "seconds": round(time.monotonic() - start, 2),
                "fixture_facts": [r.description for r in projection.relations],
            }
            print(
                json.dumps(
                    {
                        "episode": number,
                        "nodes": len(projection.nodes),
                        "relations": len(projection.relations),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        hits = await graph.search(owner, "青禾项目的评审安排和设计稿由谁负责？", ["fixture:room"])
        assert hits, "graph search returned no references"
        summary.update(
            status="passed", embedding_dimensions=len(vectors[0]), search_references=len(hits)
        )
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
        # Only synthetic fixture work runs here; SDK trace is kept local for diagnostics.
        import traceback

        (state / "failure.txt").write_text(traceback.format_exc())
        (state / "failure.txt").chmod(0o600)
    finally:
        cleanup = True
        for record in records:
            try:
                await graph.remove("fixture:" + run_id, record)
            except Exception:
                cleanup = False
        summary["cleanup_ok"] = cleanup
        await graph.close()
        await db.close()
        target = root / "compatibility.json"
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print(
            json.dumps(
                {k: v for k, v in summary.items() if not k.startswith("episode_")},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
