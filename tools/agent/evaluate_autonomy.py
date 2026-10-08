"""Real configured-model acceptance with real Runtime tools and isolated transport fixtures.

No production DB mutation, QQ send or Calendar write. Record fixture scope explicitly.
"""

from __future__ import annotations

# ruff: noqa: E402, E731
import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.agent import AgentDevelopmentPolicy, CandidateCreate, CapabilityStatus

# pyright: reportPrivateUsage=false
from chatwaifu_protocol.base import JsonObject, SideEffect
from chatwaifu_protocol.skills import SkillCapability, SkillDefinition
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.models import ChannelDeliveryPlanRecord
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext, SkillExecutionError

ROOT = Path(__file__).resolve().parents[2]
for source in (
    "services/runtime/src",
    "packages/protocol-python/src",
    "packages/model-worker-sdk-python/src",
):
    sys.path.insert(0, str(ROOT / source))

from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization, TaskChannelBinding
from chatwaifu_protocol.channels import ChannelConnectionConfiguration, ChannelConnectionStatus
from chatwaifu_runtime.agent.behavior import BehaviorDecisionService
from chatwaifu_runtime.agent.capabilities import CapabilityCatalog, discovery_tools
from chatwaifu_runtime.agent.tool_calling import AgentTurnOrchestrator
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, load_settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.delivery import NapCatDelivery
from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmToolCallRequested,
)
from chatwaifu_runtime.providers.model_config import LocalModelSecretStore
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter


class Meter:
    kind = "configured_model_evaluation"
    supports_tool_calling = True

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider
        self.calls: list[dict[str, Any]] = []
        self.usage: list[dict[str, Any]] = []
        self.requests = 0

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests += 1
        async for event in self.provider.stream(request):
            if isinstance(event, LlmToolCallRequested):
                self.calls.append({"name": event.call.name, "arguments": event.call.arguments})
            if isinstance(event, LlmResponseCompleted) and event.usage:
                from dataclasses import asdict

                self.usage.append(asdict(event.usage))
            yield event


def configured_provider(source_root: Path) -> tuple[LlmProvider, str]:
    settings = load_settings(source_root / "config/default.toml", source_root / ".env")
    path = settings.database_path
    if not path.is_absolute():
        path = source_root / path
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = dict(connection.execute("SELECT * FROM model_role_configs WHERE role='chat'").fetchone())
    connection.close()
    if row["provider"] != "openai_compatible" or not row["enabled"]:
        raise ValueError("real configured chat model is not callable")
    secrets = LocalModelSecretStore(source_root / ".local/config/model-secrets.json")
    return OpenAiCompatibleLlmProvider(
        base_url=row["base_url"],
        model=row["model"],
        api_key=lambda: secrets.get("chat"),
        timeout_seconds=45,
        request_usage=True,
    ), row["model"]


async def run_scene(scene: dict[str, Any], provider: LlmProvider) -> dict[str, Any]:
    meter = Meter(provider)
    started = time.monotonic()
    output = ""
    success = False
    scope = "model decision; no live channel operations"
    with tempfile.TemporaryDirectory(prefix="cw-agent-eval-") as directory:
        root = Path(directory)
        container = RuntimeContainer(
            Settings.model_validate(
                {
                    "config_dir": root / "config",
                    "data_dir": root,
                    "storage": {"database_path": root / "runtime.db"},
                    "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
                    "tts": {"provider": "fake"},
                }
            )
        )
        await container.start()
        scheduler = None
        try:
            session = await container.sessions.create_session("default")
            profile = container.characters.get("default")
            assert profile is not None
            kind = scene["kind"]
            if kind == "behavior":
                judged = await BehaviorDecisionService(meter).decide(
                    profile.system_prompt,
                    {
                        "scene": "group",
                        "messages": [
                            {
                                "source_ref": "original:1",
                                "text": scene["text"],
                                "speaker": "member1",
                            }
                        ],
                        "available_actions": [
                            "wait",
                            "respond",
                            "clarify",
                            "task",
                            "defer",
                            "capability_gap",
                        ],
                    },
                    frozenset({"original:1"}),
                )
                success = judged.action in scene["expected"]
                output = judged.model_dump_json()
            elif kind == "discovery":
                catalog = container.capabilities
                if scene.get("register"):
                    extra = SkillDefinition(
                        skill_id="fixture.new-tool",
                        version="1.0.0",
                        name="Newly registered",
                        description="新注册的字数统计工具，统计中文字符和数字。",
                        capabilities=[
                            SkillCapability(
                                name="count",
                                description="统计输入字符数",
                                input_schema={"type": "object"},
                                output_schema={"type": "object"},
                                side_effect=SideEffect.READ,
                            )
                        ],
                    )
                    catalog = CapabilityCatalog(
                        lambda: [*container.runtime_skills.list(), extra],
                        lambda skill: (
                            "Count the provided text."
                            if skill == extra.skill_id
                            else container.runtime_skills.instructions(skill)
                        ),
                    )
                # QQ connection fixtures only establish catalog presence; invocation remains fenced.
                agent = AgentTurnOrchestrator(
                    meter,
                    container.runtime_skills,
                    RuntimeSkillRouter(container.runtime_skills.list),
                    catalog=catalog,
                )
                request = LlmRequest(
                    generation_id=uuid4(),
                    user_text=str(scene["text"]) + "。仅发现并查看能力与状态，不执行动作。",
                    context=(
                        (
                            "user",
                            "Runtime场景: "
                            + json.dumps(scene.get("context", {}), ensure_ascii=False),
                        ),
                    ),
                    system_prompt=profile.system_prompt
                    + "\n先搜索能力，找不到就浏览分类或换一种说法。"
                    "调用 inspect_capability 查看找到的能力完整参数，明确实际状态。"
                    "不要虚构执行结果。",
                )
                async with asyncio.timeout(90):
                    async for text in agent.stream(
                        request,
                        session_id=session.session_id,
                        turn_id=uuid4(),
                        ensure_current=lambda: None,
                        tools=discovery_tools(),
                    ):
                        output += text
                target = scene["expected_capability"]
                success = any(
                    c["name"] == "inspect_capability"
                    and c["arguments"].get("capability_id") == target
                    for c in meter.calls
                )
                if scene.get("first_miss"):
                    success = success and any(
                        c["name"] == "discover_capabilities"
                        and c["arguments"].get("query") == "zzzxxyy_unique_absence"
                        for c in meter.calls
                    )
            elif kind == "candidate":
                scope = (
                    "real configured model, real isolated network-denied candidate worker; "
                    "no production installation"
                )
                service = container.agent_development
                service.model_factory = lambda: meter
                await service.configure(AgentDevelopmentPolicy(enabled=True))
                candidate = await service.create(
                    CandidateCreate(
                        session_id=session.session_id,
                        goal=str(scene["text"]),
                        source_ref="isolated-model-candidate",
                    )
                )
                async with asyncio.timeout(180):
                    while True:
                        current_candidate = await service.repository.get(candidate.candidate_id)
                        if current_candidate and current_candidate.state not in {
                            "queued",
                            "developing",
                        }:
                            break
                        await asyncio.sleep(0.05)
                success = (
                    current_candidate.state == "tested" and current_candidate.artifact is not None
                )
                success = success and current_candidate.plugin_id not in {
                    s.skill_id for s in container.runtime_skills.list()
                }
                output = current_candidate.model_dump_json()
            else:
                scope = "real Runtime/files/rendering; isolated QQ and Calendar transport fixtures"
                initial = scene.get("initial")
                if initial:
                    await container.workspace_skills.write(
                        str(session.session_id), {"path": "input.txt", "text": str(initial)}
                    )
                task_type = scene["task_type"]
                failure_count: list[int] = [0]
                if scene.get("inject_failure"):
                    handler_name = "workspace_write" if task_type == "file" else "document_word"
                    original_handler = container.runtime_skills._builtin._session_handlers[
                        handler_name
                    ]

                    async def fail_once(sid: str, arguments: JsonObject) -> JsonObject:
                        if failure_count[0] == 0:
                            failure_count[0] += 1
                            raise SkillExecutionError(
                                "version_conflict" if task_type == "file" else "invalid_document",
                                "Fixture failure before effects; inspect and repair, then retry.",
                            )
                        return await original_handler(sid, arguments)

                    container.runtime_skills._builtin._session_handlers[handler_name] = fail_once
                allowed = ["workspace.files", "documents.create"]
                binding = None
                catalog_availability: Callable[[str, str], tuple[CapabilityStatus, str] | None] = (
                    container._agent_capability_availability
                )
                fixture_calendar: list[dict[str, Any]] = []
                sent_files: list[bytes] = []
                if task_type == "calendar":
                    allowed = ["calendar.read", "agenda.manage"]

                    async def read_calendar(sid: str, args: JsonObject) -> JsonObject:
                        return {
                            "source": "google_live",
                            "windows": [{"fixture": True, "date": "2026-10-08"}],
                            "events": [
                                {
                                    "title": "已有会议",
                                    "start": "2026-10-08T18:00:00+08:00",
                                    "end": "2026-10-08T19:00:00+08:00",
                                },
                                *fixture_calendar,
                            ],
                        }

                    async def write_calendar(sid: str, args: dict[str, Any]) -> dict[str, Any]:
                        if args["action"] != "create":
                            raise PermissionError("fixture only allows requested creation")
                        event = {**args, "item_id": uuid4().hex, "etag": "fixture-v1"}
                        fixture_calendar.append(event)
                        return {"event": event, "verified": False, "fixture": True}

                    container.runtime_skills._builtin._session_handlers["calendar_read"] = (
                        read_calendar
                    )
                    container.runtime_skills._builtin._session_handlers["agenda_manage"] = (
                        write_calendar
                    )
                    catalog_availability = lambda skill, cap: (
                        None
                        if skill in allowed
                        else container._agent_capability_availability(skill, cap)
                    )
                if task_type == "qq":
                    allowed += ["qq.scene", "channel.file"]
                    cid = uuid4()
                    await container.external_channel_repository.create_connection(
                        ChannelConnectionConfiguration(
                            connection_id=cid,
                            provider_id="qq_napcat",
                            name="ISOLATED EVAL FIXTURE",
                            character_id="default",
                            principal_scope="local",
                            account_key="900",
                            allowed_sender_keys=["999"],
                            enabled=True,
                        ),
                        access_token_hash=hashlib.sha256(b"isolated-eval-token").hexdigest(),
                        created_at=datetime.now(UTC),
                    )
                    await container.external_channel_repository.touch_connection(
                        cid, status=ChannelConnectionStatus.READY, seen_at=datetime.now(UTC)
                    )
                    binding = TaskChannelBinding(
                        connection_id=cid,
                        account_key="900",
                        conversation_key="group:888",
                        sender_key="999",
                        source_ref="100",
                        route_id=uuid4(),
                        route_revision=1,
                        scene_id="fixture-group",
                    )

                    async def authorize(value: TaskChannelBinding) -> bool:
                        return value == binding

                    container.agent_tasks.scene_authorizer = authorize
                    container.qq_scene_capabilities.binding_authorizer = authorize

                    async def qq_call(
                        cid: UUID,
                        account: str,
                        action: str,
                        args: dict[str, Any],
                        guard: Callable[[], Awaitable[bool]],
                    ) -> dict[str, Any]:
                        if not await guard():
                            raise PermissionError("fixture scene revoked")
                        if action == "get_group_root_files":
                            return {
                                "files": [{"file_id": "fixture-material", "file_name": "材料.txt"}],
                                "folders": [],
                            }
                        if action == "get_group_file_url":
                            return {"url": "https://fixture.invalid/material"}
                        if action == "get_group_info":
                            return {"group_name": "验收讨论组"}
                        if action == "get_msg":
                            return {"raw_message": str(scene["text"])}
                        raise ValueError("fixture action unavailable")

                    async def material(
                        context: GenerationSkillContext,
                        binding: TaskChannelBinding,
                        resource: JsonObject,
                    ) -> JsonObject:
                        if (
                            binding.conversation_key != "group:888"
                            or resource.get("file_id") != "fixture-material"
                        ):
                            raise PermissionError("fixture resource scope invalid")
                        return {
                            "text": initial,
                            "name": "材料.txt",
                            "source_ref": "100",
                            "truncated": False,
                            "fixture": True,
                        }

                    container.qq_scene_capabilities.call = qq_call
                    container.qq_scene_capabilities.read_material = material

                    async def delivery_allowed(plan: ChannelDeliveryPlanRecord) -> bool:
                        target = plan.task_target
                        if target is None:
                            return False
                        task = await container.agent_tasks.repository.get(target.task_id)
                        return (
                            task is not None
                            and task.state.value == "running"
                            and target.binding == binding
                        )

                    class FixtureClient:
                        async def send_file(
                            self,
                            receiver: str,
                            data: bytes,
                            name: str,
                            *,
                            group_id: str,
                            before_send: Callable[[], Awaitable[bool]],
                            checkpoint: Callable[[], Awaitable[None]],
                        ) -> str:
                            if group_id != "888" or not await before_send():
                                raise PermissionError("fixture target invalid")
                            await checkpoint()
                            sent_files.append(data)
                            return "fixture-receipt-1"

                    delivery = NapCatDelivery(
                        container.external_channel_repository,
                        cast(NapCatClient, FixtureClient()),
                        cid,
                        "999",
                        root / "audio",
                        artifacts=container.artifacts,
                        task_authorization=delivery_allowed,
                    )
                    scheduler = ChannelDeliveryScheduler(
                        container.external_channel_repository,
                        delivery,
                        container.event_publisher,
                        connection_id=cid,
                        on_plan_terminal=container.channel_files.on_terminal,
                    )
                    await scheduler.start()
                    reviewed = {
                        "get_msg",
                        "get_group_info",
                        "get_group_root_files",
                        "get_group_file_system_info",
                        "get_group_msg_history",
                        "get_group_files_by_folder",
                        "get_group_file_url",
                        "read_file",
                    }
                    catalog_availability = lambda skill, cap: (
                        None
                        if skill == "channel.file" or (skill == "qq.scene" and cap in reviewed)
                        else container._agent_capability_availability(skill, cap)
                    )
                tasks = container.agent_tasks
                tasks.model_factory = lambda: meter
                tasks.catalog = CapabilityCatalog(
                    container.runtime_skills.list,
                    container.runtime_skills.instructions,
                    availability=catalog_availability,
                )
                task = await tasks.create(
                    AgentTaskCreate(
                        session_id=session.session_id,
                        goal=str(scene["text"]),
                        authorization=TaskAuthorization(
                            allowed_skill_ids=allowed,
                            resource_roots=["."],
                            allow_writes=True,
                            source_ref="isolated-real-model-eval:" + str(scene["id"]),
                            expires_at=datetime.now(UTC) + timedelta(hours=1),
                        ),
                        max_active_seconds=240,
                        max_tool_calls=30,
                    ),
                    channel_binding=binding,
                )
                async with asyncio.timeout(260):
                    while True:
                        current = await tasks.repository.get(task.task_id)
                        if current and current.state.value not in {"running", "queued"}:
                            break
                        await asyncio.sleep(0.05)
                steps = await tasks.repository.steps(task.task_id)
                marker = str(scene["expected_marker"])
                if task_type == "file":
                    try:
                        data = await container.workspace_skills.read(
                            str(session.session_id), {"path": "result.txt"}
                        )
                        success = (
                            marker in str(data["text"])
                            and all(line in str(data["text"]) for line in str(initial).splitlines())
                            and steps[-1]["capability"] == "read"
                        )
                    except (OSError, ValueError):
                        success = False
                elif task_type == "calendar":
                    success = (
                        len(fixture_calendar) == 1
                        and fixture_calendar[0].get("title") == marker
                        and steps[-1]["skill_id"] == "calendar.read"
                    )
                else:
                    artifacts = await container.artifacts.repository.list(
                        "local", session.session_id
                    )
                    docs = [
                        a
                        for a in artifacts
                        if a.task_id == task.task_id
                        and a.name.endswith((".docx", ".pptx"))
                        and a.validation_status == "rendered"
                    ]
                    success = bool(docs) and (task_type != "qq" or len(sent_files) == 1)
                success = success and current.state.value == "succeeded"
                if scene.get("inject_failure"):
                    success = (
                        success
                        and failure_count[0] == 1
                        and any(s.get("ok") is False for s in steps)
                    )
                output = json.dumps(
                    {
                        "state": current.state.value,
                        "blocked_reason": current.blocked_reason,
                        "steps": [
                            {"skill": s["skill_id"], "capability": s["capability"], "ok": s["ok"]}
                            for s in steps
                        ],
                    },
                    ensure_ascii=False,
                )
            return {
                "scene_id": scene["id"],
                "kind": scene["kind"],
                "success": success,
                "scope": scope,
                "duration_seconds": round(time.monotonic() - started, 2),
                "requests": meter.requests,
                "usage": meter.usage,
                "calls": meter.calls,
                "result": output[:6000],
            }
        finally:
            if scheduler:
                await scheduler.stop()
            await container.stop()


async def main(args: argparse.Namespace) -> None:
    provider, model = configured_provider(args.source_root)
    scenes = json.loads(args.fixtures.read_text())["scenarios"]
    if args.kind:
        scenes = [s for s in scenes if s["kind"] == args.kind]
    if args.limit:
        scenes = scenes[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("choose a fresh evidence output; previous trials must remain immutable")
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict[str, Any]] = []

    async def trial(scene: dict[str, Any], repeat: int) -> None:
        async with semaphore:
            try:
                result = await run_scene(scene, provider)
            except Exception as error:
                result = {
                    "scene_id": scene["id"],
                    "kind": scene["kind"],
                    "success": False,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:400],
                }
            result.update(repeat=repeat, model=model)
            async with lock:
                results.append(result)
                with args.output.open("a") as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(
                    json.dumps(
                        {
                            "finished": len(results),
                            "total": len(scenes) * args.repeats,
                            "scene": scene["id"],
                            "repeat": repeat,
                            "success": result["success"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    await asyncio.gather(
        *(trial(scene, repeat) for scene in scenes for repeat in range(1, args.repeats + 1))
    )
    summary: dict[str, Any] = {
        "model": model,
        "repeats": args.repeats,
        "scenario_count": len(scenes),
        "native_qq_receipt": "unverified",
        "native_calendar": "unverified",
        "metrics": {
            kind: {
                "passed": sum(r["success"] for r in results if r["kind"] == kind),
                "total": sum(r["kind"] == kind for r in results),
            }
            for kind in {"behavior", "discovery", "task", "candidate"}
        },
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    behavior = [r for r in results if r["kind"] == "behavior"]
    expected: dict[str, list[str]] = {s["id"]: s.get("expected", []) for s in scenes}
    response = [r for r in behavior if "wait" not in expected[r["scene_id"]]]
    summary["response_recall"] = {
        "passed": sum(r["success"] for r in response),
        "total": len(response),
    }
    thresholds = {"behavior": 0.90, "discovery": 0.95, "task": 0.85, "candidate": 0.85}
    summary["quality_gate_passed"] = all(
        metric["passed"] / metric["total"] >= thresholds[kind]
        for kind, metric in summary["metrics"].items()
        if metric["total"]
    ) and (not response or summary["response_recall"]["passed"] / len(response) >= 0.95)
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not summary["quality_gate_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("/Users/mubai/Desktop/CW2"))
    parser.add_argument(
        "--fixtures", type=Path, default=ROOT / "tests/fixtures/agent/autonomous_scenarios.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--kind", choices=["behavior", "discovery", "task", "candidate"])
    parser.add_argument("--limit", type=int)
    asyncio.run(main(parser.parse_args()))
