"""Owner-only continuous delegation and review, using the existing authentication boundary."""

import asyncio
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from chatwaifu_protocol.harness import (
    ActivityReceipt,
    IntentAction,
    ProcessingRequest,
    StandingIntentInput,
    StandingIntentUpdate,
    WorkItemAction,
)
from fastapi import APIRouter, HTTPException, Query, Request

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.agent.review import ReviewDecision
from chatwaifu_runtime.companion.ambient import is_quiet_time

router = APIRouter(tags=["agent-harness"])


def service(request: Request) -> AgentHarnessService:
    return request.app.state.container.harness


def failure(error: Exception) -> HTTPException:
    status = (
        403 if isinstance(error, PermissionError) else 404 if isinstance(error, KeyError) else 409
    )
    return HTTPException(status, str(error))


async def owner(app: AgentHarnessService, session_id: UUID) -> str:
    try:
        return await app.owner(session_id)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.get("/v1/agent/sources")
async def sources(request: Request, session_id: UUID):
    app = service(request)
    await owner(app, session_id)
    return await request.app.state.container.harness_sources(session_id)


@router.get("/v1/agent/intents")
async def intents(request: Request, session_id: UUID):
    app = service(request)
    scope = await owner(app, session_id)
    return {"items": await app.repository.intents(scope)}


@router.post("/v1/agent/intents", status_code=201)
async def create_intent(request: Request, body: StandingIntentInput):
    try:
        return await service(request).create_intent(body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.put("/v1/agent/intents/{intent_id}")
async def update_intent(request: Request, intent_id: UUID, body: StandingIntentUpdate):
    try:
        return await service(request).update_intent(intent_id, body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.post("/v1/agent/intents/{intent_id}")
async def intent_action(request: Request, intent_id: UUID, body: IntentAction):
    try:
        return await service(request).act_intent(intent_id, body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.get("/v1/agent/activity")
async def activity(
    request: Request,
    session_id: UUID,
    after: int = 0,
    before: int | None = None,
    wait_seconds: int = Query(default=0, ge=0, le=25),
):
    app = service(request)
    scope = await owner(app, session_id)
    app.activity_changed.clear()
    items = await app.repository.activity(scope, max(0, after), before)
    if not items and wait_seconds and before is None and after > 0:
        if app.activity_waiters.locked():
            raise HTTPException(429, "activity_waiter_capacity")
        async with app.activity_waiters:
            try:
                async with asyncio.timeout(wait_seconds):
                    await app.activity_changed.wait()
            except TimeoutError:
                pass
            items = await app.repository.activity(scope, after)
    settings = request.app.state.container.companion_settings.get()
    quiet = settings.quiet_hours_enabled and is_quiet_time(
        datetime.now(ZoneInfo("Asia/Shanghai")), settings.quiet_start, settings.quiet_end
    )
    return {
        "items": items,
        "next_cursor": items[-1].sequence if items else after,
        "quiet": quiet,
        ("context_epoch"): (
            f"{request.app.state.container.boot_id}:"
            f"{request.app.state.container.memory.knowledge_epoch}"
        ),
    }


@router.post("/v1/agent/activity/{sequence}/ack")
async def acknowledge_activity(request: Request, sequence: int, body: ActivityReceipt):
    app = service(request)
    scope = await owner(app, body.session_id)
    try:
        await app.repository.acknowledge_activity(scope, sequence, body.device_id, body.state)
    except PermissionError as error:
        raise failure(error) from None
    return {"accepted": True}


@router.get("/v1/personal-assistant/work-items")
async def work_items(
    request: Request,
    session_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    app = service(request)
    scope = await owner(app, session_id)
    rows = await app.repository.work_items(scope, limit, offset)
    return {"items": rows, "next_offset": offset + len(rows) if len(rows) == limit else None}


@router.post("/v1/personal-assistant/work-items/{work_item_id}")
async def work_action(request: Request, work_item_id: str, body: WorkItemAction):
    try:
        return await service(request).act_work(work_item_id, body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.get("/v1/personal-information/processing")
async def processing(request: Request, session_id: UUID):
    app = service(request)
    scope = await owner(app, session_id)
    return {
        "items": await app.repository.processing(scope),
        "supported_sync_versions": ["1.0", "1.1"],
        "processing_owner": "runtime",
    }


@router.get("/v1/personal-information/processing/progress")
async def processing_progress(request: Request, session_id: UUID, after: int = 0):
    app = service(request)
    scope = await owner(app, session_id)
    rows = await app.repository.progress(scope, max(0, after))
    return {
        "items": rows,
        "next_cursor": int(str(rows[-1]["sequence"])) if rows else after,
        "more": len(rows) == 100,
    }


@router.post("/v1/personal-information/processing")
async def processing_action(request: Request, body: ProcessingRequest):
    try:
        return await service(request).handoff(body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.get("/v1/personal-assistant/reminder-occurrences")
async def reminder_occurrences(request: Request, session_id: UUID):
    app = service(request)
    return {"items": await app.repository.reminder_status(await owner(app, session_id))}


@router.get("/v1/agent/review")
async def review_items(request: Request, session_id: UUID):
    await owner(service(request), session_id)
    return {"items": await request.app.state.container.harness_review.list(session_id)}


@router.post("/v1/agent/review/{kind}/{reference}")
async def decide_review(request: Request, kind: str, reference: str, body: ReviewDecision):
    try:
        return await request.app.state.container.harness_review.decide(kind, reference, body)
    except (PermissionError, KeyError, ValueError, RuntimeError) as error:
        raise failure(error) from None
