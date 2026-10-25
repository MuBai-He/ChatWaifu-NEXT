"""Synthetic source -> durable queue -> Graphiti -> cited answer -> exclusion acceptance.

Runs only in the isolated knowledge-dev directory. Production is opened read-only to copy model
configuration rows; provider credentials remain server-side. No chat archive is imported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.knowledge.adapters.graphiti_index import GraphitiIndex
from chatwaifu_runtime.knowledge.contracts import (
    Mutation,
    PolicyMutation,
    PushRequest,
    QueryRequest,
    Record,
    RecordDraft,
)
from chatwaifu_runtime.knowledge.indexer import KnowledgeIndexer
from chatwaifu_runtime.knowledge.rebuild import rebuild
from chatwaifu_runtime.knowledge.retrieval import KnowledgeRetrieval
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from chatwaifu_runtime.persistence.sqlite_knowledge_graph import SQLiteKnowledgeGraph
from chatwaifu_runtime.providers.model_config import ModelConfigurationService


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--production-state", type=Path, required=True)
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
    args = parser.parse_args()
    root = Path(args.workspace)
    production = Path(args.production_state)
    fixture = uuid4()
    state = root / ("flow-" + str(fixture))
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
    for path in (production / "data").glob("*.db"):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
            try:
                rows = source.execute(
                    "SELECT "
                    "role,provider,model,base_url,timeout_seconds,context_window,enabled,updated_at,budget_json"
                    " FROM model_role_configs"
                ).fetchall()
            except sqlite3.Error:
                continue
        async with db.transaction() as connection:
            await connection.executemany(
                "INSERT OR REPLACE INTO model_role_configs VALUES(?,?,?,?,?,?,?,?,?)", rows
            )
        break
    await models.reload()
    auth = next(
        line.split("=", 1)[1]
        for line in (root / "neo4j.env").read_text().splitlines()
        if line.startswith("NEO4J_AUTH=")
    )
    user, password = auth.split("/", 1)
    index = GraphitiIndex(args.neo4j_uri, user, password, models)
    sources = SQLiteKnowledgeRepository(db)
    graph = SQLiteKnowledgeGraph(db, sources)
    worker = KnowledgeIndexer(graph, index)
    retrieval = KnowledgeRetrieval(sources, graph, models, index)
    owner = "flow:" + str(fixture)
    room = "fixture:qinghe"
    summary: dict[str, object] = {"fixture_id": str(fixture), "raw_user_data_used": False}
    imported: list[Record] = []
    try:
        await index.start()
        for number, text, when in [
            (
                1,
                "我负责青禾项目，小林负责设计稿。项目评审约定在2026年10月15日下午三点。",
                1791800000000,
            ),
            (
                2,
                "青禾项目评审改为2026年10月16日下午四点，10月15日原定的评审取消。小林继续负责设计稿。",
                1791886400000,
            ),
        ]:
            draft = RecordDraft(
                record_id=f"source:{fixture}:{number}",
                kind="source",
                room_id=room,
                title="青禾项目合成验证",
                text=text,
                occurred_at=when,
                sender_id="person:synthetic-owner",
                sender_name="我",
                sent_by_owner=True,
            )
            body = PushRequest(
                session_id=fixture,
                device_id=fixture,
                policy_epoch=0,
                mutations=[Mutation(operation_id=uuid4(), expected_revision=0, record=draft)],
            )
            await sources.push(owner, body)
            record = await sources.get(owner, draft.record_id)
            assert record is not None
            imported.append(record)
            assert await worker.run_once()
            progress = await sources.status(owner)
            assert cast(dict[str, Any], progress["index"]).get("done", 0) == number, (
                "graph_index_not_published"
            )
            print(json.dumps({"stage": "indexed", "sources": number}), flush=True)
        view = cast(dict[str, Any], await graph.graph(owner, query="青禾"))
        assert view["nodes"] and view["edges"]
        original_edges = view["edges"]
        rebuilt = cast(dict[str, Any], await rebuild(graph, index))
        assert rebuilt["relations_restored"] >= len(original_edges), "canonical_rebuild_lost_facts"
        assert (await graph.graph(owner, query="青禾"))["edges"] == original_edges, (
            "rebuild_changed_canonical_facts"
        )
        summary["rebuild_passed"] = True
        print(
            json.dumps({"stage": "rebuilt", "relations": rebuilt["relations_restored"]}), flush=True
        )
        answer = await retrieval.query(
            owner,
            QueryRequest(
                session_id=fixture, question="青禾项目最新的评审时间是什么，小林负责什么？"
            ),
        )
        assert answer["claims"] and answer["sources"], "answer_missing_citations"
        answer_text = json.dumps(answer["claims"], ensure_ascii=False)
        assert any(value in answer_text for value in ("10月16", "周五", "星期五", "2026-10-16")), (
            "latest_agreement_missing"
        )
        summary.update(
            status="passed",
            nodes=len(view["nodes"]),
            edges=len(view["edges"]),
            claims=len(cast(list[Any], answer["claims"])),
            cited_sources=len(cast(list[Any], answer["sources"])),
        )
        cancellation = RecordDraft(
            record_id=f"source:{fixture}:3",
            kind="source",
            room_id=room,
            title="青禾项目取消通知",
            text="青禾项目原定2026年10月16日下午四点的评审取消，不再举行这次评审，后续另行通知。",
            occurred_at=1791890000000,
            sender_id="person:synthetic-owner",
            sender_name="我",
            sent_by_owner=True,
        )
        await sources.push(
            owner,
            PushRequest(
                session_id=fixture,
                device_id=fixture,
                policy_epoch=0,
                mutations=[
                    Mutation(operation_id=uuid4(), expected_revision=0, record=cancellation)
                ],
            ),
        )
        latest = await sources.get(owner, cancellation.record_id)
        assert latest is not None
        imported.append(latest)
        for attempt in range(3):
            assert await worker.run_once()
            progress = await sources.status(owner)
            if cast(dict[str, Any], progress["index"]).get("done", 0) == 3:
                break
            retry = await db.fetchone(
                (
                    "SELECT next_attempt,error_code FROM knowledge_jobs WHERE owner=?"
                    " AND record_id=? AND action='index' ORDER BY created_at DESC "
                    "LIMIT 1"
                ),
                (owner, cancellation.record_id),
            )
            print(
                json.dumps(
                    {
                        "stage": "index_retry",
                        "error_code": str(retry[1]) if retry else "unknown",
                        "attempt": attempt + 1,
                    }
                ),
                flush=True,
            )
            if attempt < 2:
                import time

                await asyncio.sleep(max(0, (int(retry[0]) / 1000 - time.time())) if retry else 1)
        assert cast(dict[str, Any], (await sources.status(owner))["index"]).get("done", 0) == 3, (
            "cancellation_index_not_published"
        )
        current = cast(dict[str, Any], await graph.graph(owner))
        assert any("取消" in edge["description"] for edge in current["edges"]), (
            "cancellation_missing_from_graph"
        )
        summary["cancellation_graph_published"] = True
        cancelled = await retrieval.query(
            owner,
            QueryRequest(
                session_id=fixture,
                question="青禾项目的评审最新状态是什么，还会按10月16日安排举行吗？",
            ),
        )
        assert "取消" in json.dumps(cancelled["claims"], ensure_ascii=False), "cancellation_missing"
        summary["cancellation_after_rebuild_passed"] = True
        await sources.push(
            owner,
            PushRequest(
                session_id=fixture,
                device_id=fixture,
                policy_epoch=0,
                policies=[
                    PolicyMutation(
                        operation_id=uuid4(), room_id=room, expected_revision=0, excluded=True
                    )
                ],
            ),
        )
        hidden = cast(dict[str, Any], await graph.graph(owner))
        assert hidden["nodes"] == [] and hidden["edges"] == [], "exclusion_did_not_hide_graph"
        denied = await retrieval.query(
            owner, QueryRequest(session_id=fixture, question="青禾项目评审时间？")
        )
        assert not denied["sources"] and not denied["claims"], "excluded_evidence_retrieved"
        summary["exclusion_passed"] = True
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
        import traceback

        failure = state / "failure.txt"
        failure.write_text(traceback.format_exc())
        failure.chmod(0o600)
    finally:
        cleanup = True
        for record in imported:
            try:
                await index.remove(owner, record)
            except Exception:
                cleanup = False
        await index.close()
        await db.close()
        summary["cleanup_ok"] = cleanup
        output = root / "flow-acceptance.json"
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        output.chmod(0o600)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
