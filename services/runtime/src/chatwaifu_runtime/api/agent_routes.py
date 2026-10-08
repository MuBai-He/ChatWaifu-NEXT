"""Operator-authenticated discovery, durable tasks and artifact access."""

from uuid import UUID

from chatwaifu_protocol.agent import (
    AgentDevelopmentPolicy,
    AgentEvent,
    AgentTask,
    AgentTaskAction,
    AgentTaskCreate,
    AgentTaskJournal,
    AgentTaskPage,
    ArtifactRef,
    CandidateApproval,
    CandidateCreate,
    CandidateFeature,
    CapabilityDetail,
    CapabilityPage,
    DecisionRecord,
    GroupAutonomyPolicy,
    GroupAutonomyUpdate,
    TaskAuthorizationUpdate,
    TaskReconciliation,
)
from chatwaifu_protocol.channels import ChannelDeliveryPlanSnapshot
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.external_channels.service import delivery_plan_snapshot

router = APIRouter(prefix="/v1/agent", tags=["agent"])


@router.post("/events")
async def agent_event(request: Request, body: AgentEvent) -> dict[str, bool]:
    container: RuntimeContainer = request.app.state.container
    try:
        return {"accepted": await container.agent_tasks.receive(body)}
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(403, str(error)) from error


@router.get("/development", response_model=AgentDevelopmentPolicy)
async def development_policy(request: Request) -> AgentDevelopmentPolicy:
    container: RuntimeContainer = request.app.state.container
    return await container.agent_development.repository.policy()


@router.put("/development", response_model=AgentDevelopmentPolicy)
async def development_policy_update(
    request: Request, body: AgentDevelopmentPolicy
) -> AgentDevelopmentPolicy:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_development.configure(body)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/candidates", response_model=list[CandidateFeature])
async def candidates(request: Request, session_id: UUID) -> list[CandidateFeature]:
    container: RuntimeContainer = request.app.state.container
    if await _scope(container, session_id) != "local":
        raise HTTPException(403, "candidate development is owner-only")
    return await container.agent_development.repository.list()


@router.post("/candidates", response_model=CandidateFeature, status_code=201)
async def candidate_create(request: Request, body: CandidateCreate) -> CandidateFeature:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_development.create(body)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error


@router.post("/candidates/{candidate_id}/approve", response_model=CandidateFeature)
async def candidate_approve(
    request: Request, candidate_id: UUID, body: CandidateApproval
) -> CandidateFeature:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_development.approve(
            candidate_id, body.expected_revision, body.package_sha256
        )
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error)) from error


@router.get("/groups/{route_id}/policy", response_model=GroupAutonomyPolicy)
async def group_policy(request: Request, route_id: UUID) -> GroupAutonomyPolicy:
    container: RuntimeContainer = request.app.state.container
    route = await container.channel_group_repository.get_route(route_id)
    if route is None:
        raise HTTPException(404, "group route not found")
    return await container.group_autonomy.policy(route)


@router.put("/groups/{route_id}/policy", response_model=GroupAutonomyPolicy)
async def group_policy_update(
    request: Request, route_id: UUID, body: GroupAutonomyUpdate
) -> GroupAutonomyPolicy:
    container: RuntimeContainer = request.app.state.container
    route = await container.channel_group_repository.get_route(route_id)
    if route is None:
        raise HTTPException(404, "group route not found")
    try:
        return await container.group_autonomy.configure(route, body)
    except (ValueError, KeyError) as error:
        raise HTTPException(409, str(error)) from error


@router.get("/groups/{route_id}/decisions", response_model=list[DecisionRecord])
async def group_decisions(request: Request, route_id: UUID) -> list[DecisionRecord]:
    container: RuntimeContainer = request.app.state.container
    if await container.channel_group_repository.get_route(route_id) is None:
        raise HTTPException(404, "group route not found")
    return await container.group_autonomy.repository.recent(route_id)


@router.get("/artifacts", response_model=list[ArtifactRef])
async def artifacts(request: Request, session_id: UUID) -> list[ArtifactRef]:
    container: RuntimeContainer = request.app.state.container
    scope = await _scope(container, session_id)
    return await container.artifacts.repository.list(scope, session_id)


@router.get("/tasks/{task_id}/delivery", response_model=ChannelDeliveryPlanSnapshot | None)
async def task_delivery(
    request: Request, task_id: UUID, session_id: UUID
) -> ChannelDeliveryPlanSnapshot | None:
    container: RuntimeContainer = request.app.state.container
    try:
        task = await container.agent_tasks.get(task_id, session_id)
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error
    if task.delivery_id is None:
        return None
    plan = await container.external_channel_repository.get_delivery_plan(task.delivery_id)
    if plan is None or plan.task_target is None or plan.task_target.task_id != task_id:
        raise HTTPException(409, "task delivery identity mismatch")
    return delivery_plan_snapshot(plan)


@router.get("/artifacts/{artifact_id}/content")
async def artifact_content(
    request: Request,
    artifact_id: UUID,
    session_id: UUID,
) -> Response:
    container: RuntimeContainer = request.app.state.container
    try:
        artifact, content = await container.artifacts.read(session_id, artifact_id)
    except KeyError as error:
        raise HTTPException(404, "artifact not visible") from error
    except ValueError as error:
        raise HTTPException(409, "artifact integrity check failed") from error
    from urllib.parse import quote

    return Response(
        content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.name),
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _scope(container: RuntimeContainer, session_id: UUID) -> str:
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session.user_scope


async def _visible_skills(container: RuntimeContainer, session_id: UUID) -> frozenset[str] | None:
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.user_scope == "local":
        return None
    if session.scene_id is None:
        return frozenset()
    for connection in await container.external_channel_repository.list_connections():
        cursor = None
        while True:
            routes, cursor = await container.channel_group_repository.list_routes(
                connection.configuration.connection_id, cursor=cursor
            )
            for route in routes:
                if route.scene_id == session.scene_id and route.enabled:
                    return await container.group_agent_skills(route)
            if cursor is None:
                break
    return frozenset()


@router.get("/capabilities", response_model=CapabilityPage)
async def capabilities(
    request: Request,
    session_id: UUID,
    query: str = Query(default="", max_length=500),
    category: str | None = None,
    cursor: str | None = None,
) -> CapabilityPage:
    container: RuntimeContainer = request.app.state.container
    visible = await _visible_skills(container, session_id)
    try:
        return container.capabilities.search(
            query,
            allowed_skill_ids=visible,
            category=category,
            cursor=cursor,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/capabilities/{capability_id:path}", response_model=CapabilityDetail)
async def capability(request: Request, capability_id: str, session_id: UUID) -> CapabilityDetail:
    container: RuntimeContainer = request.app.state.container
    visible = await _visible_skills(container, session_id)
    try:
        return container.capabilities.inspect(
            capability_id,
            allowed_skill_ids=visible,
        )
    except KeyError as error:
        raise HTTPException(404, "capability not visible") from error


@router.get("/tasks", response_model=AgentTaskPage)
async def tasks(
    request: Request,
    session_id: UUID,
    cursor: str | None = None,
) -> AgentTaskPage:
    container: RuntimeContainer = request.app.state.container
    scope = await _scope(container, session_id)
    try:
        return await container.agent_tasks.repository.page(scope, cursor)
    except ValueError as error:
        raise HTTPException(400, "invalid task cursor") from error


@router.post("/tasks", response_model=AgentTask, status_code=201)
async def create_task(request: Request, body: AgentTaskCreate) -> AgentTask:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_tasks.create(body)
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error)) from error
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error


@router.get("/tasks/{task_id}", response_model=AgentTask)
async def task(request: Request, task_id: UUID, session_id: UUID) -> AgentTask:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_tasks.get(task_id, session_id)
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error


@router.post("/tasks/{task_id}/actions", response_model=AgentTask)
async def task_action(
    request: Request,
    task_id: UUID,
    session_id: UUID,
    body: AgentTaskAction,
) -> AgentTask:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_tasks.action(task_id, session_id, body)
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/tasks/{task_id}/journal", response_model=AgentTaskJournal)
async def task_journal(request: Request, task_id: UUID, session_id: UUID) -> AgentTaskJournal:
    container: RuntimeContainer = request.app.state.container
    try:
        await container.agent_tasks.get(task_id, session_id)
        return AgentTaskJournal(items=await container.agent_tasks.repository.steps(task_id))
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error


@router.put("/tasks/{task_id}/authorization", response_model=AgentTask)
async def task_authorization(
    request: Request, task_id: UUID, session_id: UUID, body: TaskAuthorizationUpdate
) -> AgentTask:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_tasks.update_authorization(task_id, session_id, body)
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/tasks/{task_id}/reconcile", response_model=AgentTask)
async def task_reconcile(
    request: Request, task_id: UUID, session_id: UUID, body: TaskReconciliation
) -> AgentTask:
    container: RuntimeContainer = request.app.state.container
    try:
        return await container.agent_tasks.reconcile(task_id, session_id, body)
    except KeyError as error:
        raise HTTPException(404, "task not visible") from error
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
