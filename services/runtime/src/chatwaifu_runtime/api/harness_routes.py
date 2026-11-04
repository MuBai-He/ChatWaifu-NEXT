"""Owner-only continuous delegation and review, using the existing authentication boundary."""

import asyncio
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from chatwaifu_protocol.harness import (
    ActivityEntry,
    ActivityReceipt,
    ActivityValidationRequest,
    ConfirmedWorkReview,
    HarnessMetricsSnapshot,
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


@router.get("/v1/agent/intents/{intent_id}")
async def get_intent(request: Request, intent_id: UUID, session_id: UUID):
    app = service(request)
    scope = await owner(app, session_id)
    item = await app.repository.intent(intent_id)
    if item is None or item.owner != scope:
        raise HTTPException(404, "intent_not_visible")
    return item


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


@router.get("/v1/agent/metrics", response_model=HarnessMetricsSnapshot)
async def metrics(
    request: Request,
    session_id: UUID,
    work_item_id: str | None = Query(default=None, max_length=256),
):
    app = service(request)
    scope = await owner(app, session_id)
    if work_item_id:
        work = await app.repository.work_item(work_item_id)
        if work is None or work.owner != scope:
            raise HTTPException(404, "work_item_not_visible")
    if app.metrics is None:
        raise HTTPException(503, "harness_metrics_unavailable")
    data = await app.metrics.snapshot(scope, work_item_id)
    data["processor"] = app.processor_health()
    return data


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
    visible: list[ActivityEntry] = []
    for item in items:
        visible.append(
            item
            if await app.eligible(scope, item.evidence, historical=True)
            else item.model_copy(
                update={
                    "summary": "来源已不可用，相关内容已隐藏。",
                    "evidence": [],
                    "details": {},
                    "redacted": True,
                }
            )
        )
    return {
        "items": visible,
        "next_cursor": items[-1].sequence if items else after,
        "next_before": items[0].sequence if len(items) == 100 and after == 0 else None,
        "quiet": quiet,
        "context_epoch": f"{scope}:{await app.knowledge.repository.epoch(scope)}",
        "delivery_validation_supported": True,
    }


@router.post("/v1/agent/activity/validate")
async def validate_activity(request: Request, body: ActivityValidationRequest):
    app = service(request)
    scope = await owner(app, body.session_id)
    valid: list[int] = []
    for item in await app.repository.activity_by_ids(scope, body.sequences):
        if await app.eligible(scope, item.evidence):
            valid.append(item.sequence)
    return {
        "valid_sequences": valid,
        "context_epoch": f"{scope}:{await app.knowledge.repository.epoch(scope)}",
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
    await app.refresh_temporal_work(scope)
    rows = await app.repository.work_items(scope, limit, offset)
    return {"items": rows, "next_offset": offset + len(rows) if len(rows) == limit else None}


@router.post("/v1/personal-assistant/work-items/review-confirmed")
async def review_confirmed_work(request: Request, body: ConfirmedWorkReview):
    try:
        return await service(request).review_confirmed(body)
    except (PermissionError, ValueError, KeyError) as error:
        raise failure(error) from None


@router.get("/v1/personal-assistant/work-items/{work_item_id}")
async def get_work_item(request: Request, work_item_id: str, session_id: UUID):
    app = service(request)
    scope = await owner(app, session_id)
    await app.refresh_temporal_work(scope)
    item = await app.repository.work_item(work_item_id)
    if item is None or item.owner != scope:
        raise HTTPException(404, "work_not_visible")
    return item


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
        "owner_review_supported": True,
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
async def review_items(request: Request, session_id: UUID, include_failures: bool = False):
    await owner(service(request), session_id)
    return {
        "items": await request.app.state.container.harness_review.list(
            session_id, include_failures=include_failures
        )
    }


@router.post("/v1/agent/review/{kind}/{reference}")
async def decide_review(request: Request, kind: str, reference: str, body: ReviewDecision):
    try:
        return await request.app.state.container.harness_review.decide(kind, reference, body)
    except (PermissionError, KeyError, ValueError, RuntimeError) as error:
        raise failure(error) from None
