"""Versioned sync and knowledge APIs; each invocation resolves a trusted private owner."""

from typing import cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field

from chatwaifu_runtime.knowledge.contracts import (
    Citation,
    Contract,
    MergeRequest,
    PushRequest,
    QueryRequest,
    RelationCorrection,
    ReviewRequest,
)
from chatwaifu_runtime.knowledge.service import KnowledgeService
from chatwaifu_runtime.persistence.sqlite_knowledge import KnowledgeAccessError, KnowledgeConflict

sync_router = APIRouter(prefix="/v1/personal-information/sync", tags=["information-sync"])


async def viewer_scope(request: Request) -> None:
    session = getattr(request.state, "knowledge_viewer_session", None)
    if session is None:
        return
    values = request.query_params.getlist("session_id")
    if request.method in ("POST", "PATCH", "PUT"):
        body = await request.json()
        if isinstance(body, dict) and "session_id" in body:
            value = cast(dict[str, object], body)["session_id"]
            if not isinstance(value, str):
                raise HTTPException(403, "viewer_session_mismatch")
            values.append(value)
    if not values or any(value != session for value in values):
        raise HTTPException(403, "viewer_session_mismatch")


router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"], dependencies=[Depends(viewer_scope)])


def service(request: Request, response: Response) -> KnowledgeService:
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.container.knowledge


@sync_router.post("/push")
async def push(request: Request, response: Response, body: PushRequest) -> JsonObject:
    try:
        return await service(request, response).push(body)
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as error:
        raise HTTPException(409, str(error)) from None


@sync_router.get("/pull")
async def pull(
    request: Request,
    response: Response,
    session_id: UUID,
    device_id: UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> JsonObject:
    app = service(request, response)
    try:
        return await app.repository.pull(await app.owner(session_id), str(device_id), after, limit)
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


class Acknowledgement(Contract):
    session_id: UUID
    device_id: UUID
    cursor: int = Field(ge=0)
    applied: list[Citation] = Field(default_factory=lambda: list[Citation](), max_length=100)


@sync_router.post("/ack")
async def acknowledge(request: Request, response: Response, body: Acknowledgement) -> JsonObject:
    app = service(request, response)
    try:
        await app.repository.acknowledge(
            await app.owner(body.session_id), str(body.device_id), body.cursor, body.applied
        )
        return {"acknowledged": True}
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as error:
        raise HTTPException(409, str(error)) from None


@router.get("/status")
async def status(request: Request, response: Response, session_id: UUID) -> JsonObject:
    app = service(request, response)
    try:
        result = await app.repository.status(await app.owner(session_id))
        result["enabled"] = app.enabled
        config = request.app.state.container.settings.knowledge
        result["history_daily_limit"] = config.history_daily_limit
        result["history_enabled"] = config.history_enabled
        return result
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


@router.get("/conflicts")
async def conflicts(request: Request, response: Response, session_id: UUID) -> JsonObject:
    app = service(request, response)
    try:
        return {"items": list(await app.repository.conflicts(await app.owner(session_id)))}
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


@router.post("/commands")
async def command(request: Request, response: Response, body: PushRequest) -> JsonObject:
    return await push(request, response, body)


@router.get("/graph")
async def graph(
    request: Request,
    response: Response,
    session_id: UUID,
    q: str = Query(default="", max_length=500),
    center: str | None = None,
    kind: str | None = None,
    at: int | None = None,
    hops: int = Query(default=2, ge=1, le=3),
    limit: int = Query(default=200, ge=1, le=500),
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.graph(
            await app.owner(session_id),
            query=q,
            center=center,
            kind=kind,
            at=at,
            hops=hops,
            limit=limit,
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


@router.get("/records")
async def records(
    request: Request, response: Response, session_id: UUID, q: str = "", kind: str | None = None
) -> JsonObject:
    app = service(request, response)
    try:
        return {
            "items": [
                {
                    **r.model_dump(mode="json"),
                    "sync": await app.repository.record_ack(
                        await app.owner(session_id), r.record_id, r.revision
                    ),
                }
                for r in await app.repository.records(
                    await app.owner(session_id), query=q, kind=kind
                )
            ]
        }
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


@router.get("/records/{record_id}")
async def record(
    request: Request,
    response: Response,
    record_id: str,
    session_id: UUID,
    revision: int | None = None,
) -> JsonObject:
    app = service(request, response)
    try:
        owner = await app.owner(session_id)
        item = await app.repository.get(owner, record_id, revision)
        current = await app.repository.get(owner, record_id)
        if not item or not current or current.deleted:
            raise HTTPException(404, "record_unavailable")
        from chatwaifu_runtime.knowledge.contracts import Citation

        if not await app.repository.eligible(
            owner, Citation(record_id=record_id, revision=current.revision)
        ):
            raise HTTPException(403, "source_excluded")
        return {
            **item.model_dump(mode="json"),
            "sync": await app.repository.record_ack(owner, item.record_id, item.revision),
        }
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


@router.get("/entities/{node_id}")
async def entity(
    request: Request, response: Response, node_id: str, session_id: UUID
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.entity(
            await app.owner(session_id), node_id
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(404, str(e)) from None


class RenameRequest(Contract):
    session_id: UUID
    expected_graph_revision: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=256)


@router.patch("/entities/{node_id}")
async def rename(
    request: Request, response: Response, node_id: str, body: RenameRequest
) -> JsonObject:
    app = service(request, response)
    try:
        await request.app.state.container.knowledge_graph.rename(
            await app.owner(body.session_id), node_id, body.label, body.expected_graph_revision
        )
        return {"updated": True}
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


@router.post("/entities/merge")
async def merge(request: Request, response: Response, body: MergeRequest) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.merge(
            await app.owner(body.session_id), body
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


class SplitRequest(Contract):
    session_id: UUID
    operation_id: UUID
    expected_graph_revision: int = Field(ge=0)


@router.post("/entities/split")
async def split(request: Request, response: Response, body: SplitRequest) -> JsonObject:
    app = service(request, response)
    try:
        await request.app.state.container.knowledge_graph.split(
            await app.owner(body.session_id), str(body.operation_id), body.expected_graph_revision
        )
        return {"split": True}
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


@router.post("/relations/{relation_id}/review")
async def review(
    request: Request, response: Response, relation_id: str, body: ReviewRequest
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.review(
            await app.owner(body.session_id), relation_id, body
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


@router.post("/query")
async def query(request: Request, response: Response, body: QueryRequest) -> JsonObject:
    app = service(request, response)
    try:
        if app.retrieval is None:
            raise HTTPException(503, "knowledge_unavailable")
        return await app.retrieval.query(await app.owner(body.session_id), body)
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None
    except ValueError:
        raise HTTPException(409, "knowledge_evidence_changed_or_invalid") from None


class ConflictResolution(Contract):
    session_id: UUID
    keep_local: bool
    operation_id: UUID
    expected_revision: int = Field(ge=0)


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    request: Request, response: Response, conflict_id: str, body: ConflictResolution
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge.repository.resolve_conflict(
            await app.owner(body.session_id),
            conflict_id,
            body.keep_local,
            str(body.operation_id),
            body.expected_revision,
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as e:
        raise HTTPException(409, str(e)) from None


class ViewerRequest(Contract):
    session_id: UUID
    device_id: UUID


@router.post("/viewer-session")
async def viewer(request: Request, response: Response, body: ViewerRequest) -> JsonObject:
    app = service(request, response)
    try:
        owner = await app.owner(body.session_id)
        ttl = request.app.state.container.settings.knowledge.viewer_ttl_seconds
        token = await app.repository.create_viewer(
            owner, str(body.session_id), str(body.device_id), ttl
        )
        response.set_cookie(
            "cw2_knowledge",
            token,
            max_age=ttl,
            path="/v1/knowledge",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return {
            "expires_in": ttl,
            "cookie": (
                f"cw2_knowledge={token}; Path=/v1/knowledge; Secure; HttpOnly; "
                f"SameSite=Strict; Max-Age={ttl}"
            ),
        }
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


class RetryRequest(Contract):
    session_id: UUID


@router.post("/index/retry")
async def retry_index(request: Request, response: Response, body: RetryRequest) -> JsonObject:
    app = service(request, response)
    try:
        await request.app.state.container.knowledge_graph.retry_failed(
            await app.owner(body.session_id)
        )
        return {"queued": True}
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None


@router.post("/relations/{relation_id}/correct")
async def correct_relation(
    request: Request, response: Response, relation_id: str, body: RelationCorrection
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.correct_relation(
            await app.owner(body.session_id), relation_id, body
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as error:
        raise HTTPException(409, str(error)) from None


@router.get("/relations/{relation_id}/history")
async def relation_history(
    request: Request, response: Response, relation_id: str, session_id: UUID
) -> JsonObject:
    app = service(request, response)
    try:
        return await request.app.state.container.knowledge_graph.relation_history(
            await app.owner(session_id), relation_id
        )
    except KnowledgeAccessError:
        raise HTTPException(403, "private_owner_required") from None
    except KnowledgeConflict as error:
        raise HTTPException(409, str(error)) from None
