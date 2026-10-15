"""Actual database checks of graph publication and policy/lease/review invariants."""

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.knowledge.contracts import (
    Citation,
    Mutation,
    Node,
    PolicyMutation,
    Projection,
    PushRequest,
    Record,
    RecordDraft,
    Relation,
    ReviewRequest,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from chatwaifu_runtime.persistence.sqlite_knowledge_graph import SQLiteKnowledgeGraph

type GraphFixture = tuple[SQLiteKnowledgeRepository, SQLiteKnowledgeGraph]


@pytest.fixture
async def graph(tmp_path: Path) -> AsyncIterator[GraphFixture]:
    db = Database(tmp_path / "repository.db", StorageConfig())
    await db.open()
    sources = SQLiteKnowledgeRepository(db)
    graph = SQLiteKnowledgeGraph(db, sources)
    yield sources, graph
    await db.close()


def body(
    *mutations: Mutation, policies: Sequence[PolicyMutation] = (), epoch: int = 0
) -> PushRequest:
    return PushRequest(
        session_id=uuid4(),
        device_id=uuid4(),
        policy_epoch=epoch,
        mutations=list(mutations),
        policies=list(policies),
    )


def message(key: str = "one", revision: int = 0, text: str = "周四开会") -> Mutation:
    return Mutation(
        operation_id=uuid4(),
        expected_revision=revision,
        record=RecordDraft(
            record_id="source:" + key,
            kind="source",
            room_id="room:one",
            text=text,
            occurred_at=1791800000000,
        ),
    )


def projection(key: str = "one", rev: int = 1) -> Projection:
    return Projection(
        nodes=[
            Node(node_id="person:one", kind="person", label="小林", room_id="room:one"),
            Node(node_id="project:one", kind="project", label="青禾项目", room_id="room:one"),
        ],
        relations=[
            Relation(
                relation_id="relation:one",
                source="person:one",
                target="project:one",
                predicate="responsible",
                description="小林负责青禾项目",
                source_refs=[Citation(record_id="source:" + key, revision=rev)],
            )
        ],
    )


async def test_actual_projection_and_exclusion_hide_entire_graph(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    assert await repository.publish(job, projection())
    view = cast(dict[str, Any], await repository.graph("local"))
    assert len(view["nodes"]) == 2 and len(view["edges"]) == 1
    await sources.push(
        "local",
        body(
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=0, excluded=True
                )
            ]
        ),
    )
    view = cast(dict[str, Any], await repository.graph("local"))
    assert view["nodes"] == [] and view["edges"] == []


async def test_exclusion_fences_inflight_model_output(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    await sources.push(
        "local",
        body(
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=0, excluded=True
                )
            ]
        ),
    )
    assert not await repository.publish(job, projection())
    assert (cast(dict[str, Any], await repository.graph("local")))["nodes"] == []


async def test_corrected_source_fences_old_result(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    await sources.push("local", body(message(revision=1, text="改周五")))
    assert not await repository.publish(job, projection())
    newer = await repository.claim()
    assert newer
    assert await repository.publish(newer, projection(rev=2))


async def test_reindex_cannot_overwrite_review(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    await repository.publish(job, projection())
    await repository.review(
        "local",
        "relation:one",
        ReviewRequest(session_id=uuid4(), expected_revision=1, decision="confirm"),
    )
    await sources.push("local", body(message("two")))
    second = await repository.claim()
    assert second
    changed = projection("two")
    changed.relations[0].description = "模型企图覆盖已确认内容"
    await repository.publish(second, changed)
    view = cast(dict[str, Any], await repository.graph("local"))
    edge = view["edges"][0]
    assert edge["description"] == "小林负责青禾项目" and edge["status"] == "confirmed"


async def test_unknown_evidence_is_never_published(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    forged = projection()
    forged.relations[0].source_refs.append(Citation(record_id="source:foreign", revision=1))
    assert not await repository.publish(job, forged)
    assert (cast(dict[str, Any], await repository.graph("local")))["nodes"] == []


async def test_cross_owner_graph_is_empty(graph: GraphFixture) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert job
    await repository.publish(job, projection())
    assert (cast(dict[str, Any], await repository.graph("other")))["nodes"] == []


async def test_restore_waits_for_removal_and_reports_current_index_state(
    graph: GraphFixture,
) -> None:
    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert await repository.publish(job, projection())
    assert (await sources.status("local"))["indexed_records"] == 1
    await sources.push(
        "local",
        body(
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=0, excluded=True
                )
            ]
        ),
    )
    removal = await repository.claim()
    assert removal is not None
    assert removal["action"] == "remove"
    assert await repository.complete_removal(removal)
    assert (await sources.status("local"))["indexed_records"] == 0
    await sources.push(
        "local",
        body(
            epoch=1,
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=1, excluded=False
                )
            ],
        ),
    )
    assert (await sources.status("local"))["indexed_records"] == 0
    restored = await repository.claim()
    assert restored is not None
    assert restored["action"] == "index"
    assert await repository.publish(restored, projection())
    assert (await sources.status("local"))["indexed_records"] == 1


async def test_human_correction_has_its_own_evidence_and_fences_old_answer(
    graph: GraphFixture,
) -> None:
    from chatwaifu_runtime.knowledge.contracts import RelationCorrection

    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert await repository.publish(job, projection())
    correction = RelationCorrection(
        session_id=uuid4(),
        operation_id=uuid4(),
        expected_revision=1,
        source="person:one",
        target="project:one",
        predicate="participates",
        description="小林协助青禾项目",
    )
    receipt = await repository.correct_relation("local", "relation:one", correction)
    assert await repository.correct_relation("local", "relation:one", correction) == receipt
    view = cast(dict[str, Any], await repository.graph("local"))
    assert view["edges"][0]["description"] == "小林协助青禾项目"
    annotation_data = receipt["annotation"]
    assert isinstance(annotation_data, dict)
    annotation_id = annotation_data["record_id"]
    assert isinstance(annotation_id, str)
    annotation = await sources.get("local", annotation_id)
    assert annotation is not None
    assert annotation.kind == "reference" and annotation.confirmed
    assert annotation.source_refs == [Citation(record_id="source:one", revision=1)]
    original = await sources.get("local", "source:one")
    assert original is not None and original.text == "周四开会"
    history = (await repository.relation_history("local", "relation:one"))["items"]
    assert isinstance(history, list) and len(history) == 1
    assert not await repository.validate_snapshot(
        "local", 0, [Citation(record_id="source:one", revision=1)], {"relation:one": 1}, False
    )
    pages = [page async for page in repository.rebuild_pages()]
    assert pages[0][2][0].description == "小林协助青禾项目"


async def test_excluded_relation_cannot_be_corrected_back_into_visibility(
    graph: GraphFixture,
) -> None:
    from chatwaifu_runtime.knowledge.contracts import RelationCorrection
    from chatwaifu_runtime.knowledge.errors import KnowledgeConflict

    sources, repository = graph
    await sources.push("local", body(message()))
    job = await repository.claim()
    assert job is not None
    assert await repository.publish(job, projection())
    await sources.push(
        "local",
        body(
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=0, excluded=True
                )
            ]
        ),
    )
    with pytest.raises(KnowledgeConflict):
        await repository.correct_relation(
            "local",
            "relation:one",
            RelationCorrection(
                session_id=uuid4(),
                operation_id=uuid4(),
                expected_revision=1,
                source="person:one",
                target="project:one",
                predicate="responsible",
                description="迟到的更正",
            ),
        )


async def test_revoked_episode_does_not_stop_worker_or_publish_late_result(
    graph: GraphFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from chatwaifu_runtime.knowledge.indexer import KnowledgeIndexer

    sources, jobs = graph
    await sources.push("local", body(message()))
    entered, revoke, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_renew = jobs.renew

    async def renew(job: JsonObject) -> bool:
        await revoke.wait()
        return await original_renew(job)

    monkeypatch.setattr(jobs, "renew", renew)

    class Index:
        async def close(self) -> None:
            pass

        async def search(
            self, owner: str, question: str, rooms: list[str], limit: int = 30
        ) -> list[str]:
            return []

        calls = 0

        async def index(self, owner: str, record: Record) -> Projection:
            self.calls += 1
            if self.calls == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return Projection()

        async def remove(self, owner: str, record: Record) -> None:
            pass

    index = Index()
    worker = KnowledgeIndexer(jobs, index, lease_interval=0)
    run = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), 2)
    await sources.push(
        "local",
        body(
            policies=[
                PolicyMutation(
                    operation_id=uuid4(), room_id="room:one", expected_revision=0, excluded=True
                )
            ]
        ),
    )
    revoke.set()
    assert await asyncio.wait_for(run, 2) is True
    assert stopped.is_set() and run.cancelling() == 0
    other = message("two")
    other.record.room_id = "room:two"
    await sources.push("local", body(other, epoch=1))
    # Consume the revoked room's cleanup and then the next eligible source.
    assert await worker.run_once()
    assert await worker.run_once()
    assert index.calls == 2


@pytest.mark.parametrize("revocation_race", [False, True])
async def test_external_worker_shutdown_still_propagates(
    graph: GraphFixture, monkeypatch: pytest.MonkeyPatch, revocation_race: bool
) -> None:
    import asyncio

    from chatwaifu_runtime.knowledge.indexer import KnowledgeIndexer

    sources, jobs = graph
    await sources.push("local", body(message()))
    entered, heartbeat_ready, revoke = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stopped = asyncio.Event()

    class Index:
        async def close(self) -> None:
            pass

        async def search(
            self, owner: str, question: str, rooms: list[str], limit: int = 30
        ) -> list[str]:
            return []

        async def index(self, owner: str, record: Record) -> Projection:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            raise AssertionError("hanging fixture unexpectedly resumed")

        async def remove(self, owner: str, record: Record) -> None:
            pass

    async def renew(job: JsonObject) -> bool:
        heartbeat_ready.set()
        await revoke.wait()
        run.cancel()
        return False

    monkeypatch.setattr(jobs, "renew", renew)
    index = Index()
    worker = KnowledgeIndexer(jobs, index, lease_interval=0)
    run = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(heartbeat_ready.wait(), 2)
    if revocation_race:
        revoke.set()
    else:
        run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 2)
    assert stopped.is_set()
    row = await jobs.db.fetchone("SELECT state,error_code FROM knowledge_jobs")
    assert row is not None
    assert tuple(row) == ("pending", "cancelled")


async def test_explicit_unlimited_history_bypasses_daily_cap(graph: GraphFixture) -> None:
    sources, jobs = graph
    jobs.history_limit = 1
    first = message("old-one")
    first.record.occurred_at = 946684800000
    await sources.push("local", body(first))
    job = await jobs.claim()
    assert job is not None
    assert await jobs.publish(job, Projection())
    second = message("old-two")
    second.record.occurred_at = 978307200000
    await sources.push("local", body(second))
    assert await jobs.claim() is None
    jobs.history_limit = 0
    assert await jobs.claim() is not None
