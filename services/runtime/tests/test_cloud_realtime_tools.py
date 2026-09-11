# pyright: reportPrivateUsage=false

"""Phase 13.5A: Comprehensive Cloud Realtime Tool Bridge Tests.

Scenarios covered:
1. Local loopback WebSocket -> OpenAI adapter -> RuntimeSkillService ->
   SQLite skill_runs 4-way lineage -> tool result -> continuation audio + ACK.
2. Permission denied (sanitized denial output).
3. Unknown tool (sanitized error output).
4. Oversized and malformed arguments (handled safely).
5. Duplicate tool requests across differing event IDs & changed args on same ID.
6. Interruption before invoke, during invoke admission, during invoke execution.
7. Provider EOF cancellation of in-flight tool tasks.
8. Late old response / tombstone fence.
9. Rebind without reserved continuation rejection.
10. Tool-result egress deny / ask / audit failure (zero provider writes).
11. Result size bounds (>32 KiB truncation).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonValue
from chatwaifu_protocol.commands import PlaybackAckCommand, PlaybackAckPayload
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EventModel
from chatwaifu_protocol.skills import (
    SideEffect,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
    SkillRunState,
)
from chatwaifu_runtime.agent.tool_calling import (
    AgentSkillGateway,
    bounded_tool_result_payload,
    format_tool_result_payload,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.realtime.cloud import openai as openai_module
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressGateway,
    ConsentRequiredError,
    EgressGrant,
    PolicyDeniedError,
)
from chatwaifu_runtime.realtime.cloud.contracts import (
    RealtimeSessionOpenRequest,
    RealtimeToolCall,
    ResponseStartedEvent,
    ToolCallRequestedEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
)
from chatwaifu_runtime.realtime.cloud.fake import (
    FakeCloudRealtimeSession,
)
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror
from chatwaifu_runtime.realtime.cloud.openai import OpenAIRealtimeBackend, RealtimeSocket
from chatwaifu_runtime.realtime.cloud.openai_events import (
    OpenAIEventMapper,
    OpenAIRealtimeError,
    object_value,
)
from chatwaifu_runtime.realtime.cloud.tools import CloudToolBridge
from chatwaifu_runtime.runtime_skills.agent_router import (
    ProjectedSkillTool,
)
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from websockets.asyncio.server import ServerConnection, serve


def observe_playback(
    bridge: CloudRealtimeMediaBridge, monkeypatch: pytest.MonkeyPatch
) -> tuple[asyncio.Event, list[bytes], list[dict[str, object]]]:
    complete = asyncio.Event()
    audio: list[bytes] = []
    markers: list[dict[str, object]] = []

    async def capture(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, OutputAudioRawFrame):
            audio.append(frame.audio)
        if isinstance(frame, OutputTransportMessageFrame):
            message: object = frame.message
            if isinstance(message, dict):
                markers.append(cast(dict[str, object], message))

    monkeypatch.setattr(bridge, "push_frame", capture)
    sink = bridge.coordinator.domain_sink
    original = sink.response_completed

    async def completed(
        sid: UUID,
        tid: UUID,
        gid: UUID,
        text: str,
        *,
        has_audio: bool = False,
        playback_confirmed: bool = False,
    ) -> None:
        await original(
            sid, tid, gid, text, has_audio=has_audio, playback_confirmed=playback_confirmed
        )
        if has_audio:
            complete.set()

    monkeypatch.setattr(sink, "response_completed", completed)
    return complete, audio, markers


async def verify_playback(
    container: RuntimeContainer,
    bridge: CloudRealtimeMediaBridge,
    observed: tuple[asyncio.Event, list[bytes], list[dict[str, object]]],
    expected_pcm: bytes,
) -> None:
    complete, audio, markers = observed
    await asyncio.wait_for(complete.wait(), 5)
    assert b"".join(audio) == expected_pcm
    identity = bridge.current_identity
    assert identity is not None
    buffered = [m for m in markers if m.get("phase") == "buffered"]
    assert buffered
    assert not [
        t
        for t in await container.conversation.latest_confirmed_history(identity.session_id)
        if t.role == "assistant"
    ]
    for marker in buffered:
        duration = int(str(marker["duration_ms"]))
        await container.playback.acknowledge(
            PlaybackAckCommand(
                command_id=uuid4(),
                session_id=identity.session_id,
                generation_id=identity.generation_id,
                issued_at=datetime.now(UTC),
                issuer="web-client",
                payload=PlaybackAckPayload(
                    stream_id=UUID(str(marker["stream_id"])),
                    segment_id=UUID(str(marker["segment_id"])),
                    phase="stopped",
                    played_pts_ms=duration,
                    buffered_ms=duration,
                    client_clock_ms=duration,
                    transport="webrtc",
                    reason="ended",
                ),
            )
        )
    history = await container.conversation.latest_confirmed_history(identity.session_id)
    spoken = [t for t in history if t.role == "assistant"]
    assert len(spoken) == 1
    assert "Let me think" not in spoken[0].text


class InMemoryEventStore:
    """In-memory event store satisfying CloudEgressGateway audit requirements."""

    def __init__(self) -> None:
        self.events: list[EventModel] = []
        self.published: list[UUID] = []

    async def append[EventT: EventModel](self, event: EventT) -> EventT:
        self.events.append(event)
        return event

    async def mark_published(self, event_id: UUID) -> None:
        self.published.append(event_id)


def make_egress_gateway(
    policy_mode: Literal["allow", "ask", "deny"] = "allow",
    *,
    event_store: Any | None = None,
    grants: Mapping[UUID, EgressGrant] | None = None,
) -> CloudEgressGateway:
    store = event_store if event_store is not None else InMemoryEventStore()
    gw = CloudEgressGateway(policy_mode=policy_mode, event_store=cast(Any, store))
    if grants is not None:
        for grant in grants.values():
            gw.grant_consent(grant)
    return gw


def create_cloud_settings(tmp_path: Path, **overrides: object) -> Settings:
    data: dict[str, object] = {
        "config_dir": tmp_path / "config",
        "data_dir": tmp_path,
        "storage": {"database_path": tmp_path / "runtime.db"},
        "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
        "tts": {"provider": "fake"},
        "privacy": {"cloud_egress": "allow"},
        "realtime": {
            "connection_mode": "cloud_realtime",
            "cloud_backend": "openai",
            "cloud_tools_enabled": True,
            "openai": {"model": "test-model", "api_key": "local-test-key"},
        },
    }
    data.update(overrides)
    return Settings.model_validate(data)


def make_test_projected_tool(
    name: str = "status_read",
    skill_id: str = "runtime.status",
    capability: str = "read",
    description: str = "Test status tool",
) -> ProjectedSkillTool:
    return ProjectedSkillTool(
        name=name,
        skill_id=skill_id,
        capability=capability,
        description=description,
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        side_effect=SideEffect.READ,
        confirmation_required=False,
    )


class ScriptedSkillGateway(AgentSkillGateway):
    def __init__(
        self,
        *,
        result_content: JsonValue = None,
        is_error: bool = False,
        error_message: str | None = None,
        delay_event: asyncio.Event | None = None,
        admission_barrier: asyncio.Event | None = None,
        fail_on_invoke: Exception | None = None,
    ) -> None:
        self.result_content: JsonValue = (
            result_content if result_content is not None else {"status": "all_ok"}
        )
        self.is_error = is_error
        self.error_message = error_message
        self.delay_event = delay_event
        self.admission_barrier = admission_barrier
        self.fail_on_invoke = fail_on_invoke
        self.invocations: list[dict[str, object]] = []
        self.cancelled_runs: list[UUID] = []
        self.invoked_event = asyncio.Event()
        self.waiting_event = asyncio.Event()

    async def invoke(
        self,
        session_id: UUID,
        invocation: SkillInvocation,
        *,
        principal: str = "local_user",
        turn_id: UUID | None = None,
        generation_id: UUID | None = None,
        origin: Literal["manual", "agent", "external_mcp"] = "manual",
        provider_tool_call_id: str | None = None,
        allow_confirmation: bool = True,
        require_cloud_readonly: bool = False,
    ) -> SkillRunSnapshot:
        if self.admission_barrier is not None:
            await self.admission_barrier.wait()
        if self.fail_on_invoke is not None:
            raise self.fail_on_invoke
        run_id = uuid4()
        self.invocations.append(
            {
                "session_id": session_id,
                "invocation": invocation,
                "principal": principal,
                "turn_id": turn_id,
                "generation_id": generation_id,
                "origin": origin,
                "provider_tool_call_id": provider_tool_call_id,
                "run_id": run_id,
                "allow_confirmation": allow_confirmation,
            }
        )
        self.invoked_event.set()
        return SkillRunSnapshot(
            skill_run_id=run_id,
            session_id=session_id,
            skill_id=invocation.skill_id,
            skill_version="1.0.0",
            capability=invocation.capability,
            state=SkillRunState.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def wait_for_terminal(self, run_id: UUID) -> SkillRunSnapshot:
        self.waiting_event.set()
        if self.delay_event is not None:
            await self.delay_event.wait()
        state = SkillRunState.FAILED if self.is_error else SkillRunState.SUCCEEDED
        res = None
        err = None
        if self.is_error:
            err = StructuredError(
                code="skill_failed",
                message=self.error_message or "Skill execution failed",
                retryable=False,
                component="runtime_skills",
            )
        else:
            res = SkillResult(
                status="succeeded",
                data=self.result_content,
            )
        return SkillRunSnapshot(
            skill_run_id=run_id,
            session_id=uuid4(),
            skill_id="test.skill",
            skill_version="1.0.0",
            capability="read",
            state=state,
            result=res,
            error=err,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def cancel(self, run_id: UUID) -> SkillRunSnapshot:
        self.cancelled_runs.append(run_id)
        return SkillRunSnapshot(
            skill_run_id=run_id,
            session_id=uuid4(),
            skill_id="test.skill",
            skill_version="1.0.0",
            capability="read",
            state=SkillRunState.CANCELLED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_01_loopback_websocket_openai_skill_run_4way_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1. Full loopback: OpenAI wire -> RuntimeSkillService ->
    SQLite skill_runs 4-way lineage -> continuation audio."""
    continuation_completed = asyncio.Event()
    received_tool_output: list[dict[str, object]] = []
    received_continuation_request: list[dict[str, object]] = []
    output_pcm = b"\x01\x00" * 480

    allocated_tool_name = ""

    async def loopback_handler(ws: ServerConnection) -> None:
        nonlocal allocated_tool_name
        sequence = 0

        async def send(event: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            await ws.send(json.dumps({"event_id": f"wire-{sequence}", **event}))

        await send(
            {
                "type": "session.created",
                "session": {"id": "loopback-sess", "type": "realtime"},
            }
        )
        try:
            async for raw in ws:
                event = object_value(json.loads(raw))
                event_type = event.get("type")

                if event_type == "session.update":
                    sess_cfg = object_value(event["session"])
                    assert sess_cfg.get("tool_choice") == "auto"
                    tools = sess_cfg.get("tools")
                    assert isinstance(tools, list)
                    tools_list = cast(list[object], tools)
                    assert len(tools_list) >= 1
                    allocated_tool_name = str(object_value(tools_list[0])["name"])
                    await send(
                        {
                            "type": "session.updated",
                            "session": {**sess_cfg, "id": "loopback-sess"},
                        }
                    )

                elif event_type == "input_audio_buffer.commit":
                    await send({"type": "input_audio_buffer.committed", "item_id": "input-1"})

                elif event_type == "response.create":
                    resp_data = object_value(event.get("response", {}))
                    metadata = resp_data.get("metadata", {})

                    if resp_data.get("tool_choice") == "none":
                        # Continuation response request
                        received_continuation_request.append(resp_data)
                        await send(
                            {
                                "type": "response.created",
                                "response": {"id": "resp-cont-2", "metadata": metadata},
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio.delta",
                                "response_id": "resp-cont-2",
                                "delta": base64.b64encode(output_pcm).decode(),
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio.done",
                                "response_id": "resp-cont-2",
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio_transcript.done",
                                "response_id": "resp-cont-2",
                                "transcript": "System is healthy.",
                            }
                        )
                        await send(
                            {
                                "type": "response.done",
                                "response": {
                                    "id": "resp-cont-2",
                                    "status": "completed",
                                    "output": [
                                        {
                                            "type": "message",
                                            "role": "assistant",
                                            "content": [
                                                {
                                                    "type": "output_audio",
                                                    "transcript": "System is healthy.",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            }
                        )
                        continuation_completed.set()
                    else:
                        # Initial turn decision response: model emits tool call!
                        await send(
                            {
                                "type": "response.created",
                                "response": {"id": "resp-decide-1", "metadata": metadata},
                            }
                        )
                        fc_item = {
                            "id": "fc-item-1",
                            "type": "function_call",
                            "call_id": "call_stat_123",
                            "name": allocated_tool_name,
                            "arguments": "{}",
                        }
                        await send(
                            {
                                "type": "response.output_item.added",
                                "response_id": "resp-decide-1",
                                "output_index": 0,
                                "item": fc_item,
                            }
                        )
                        await send(
                            {
                                "type": "response.function_call_arguments.done",
                                "response_id": "resp-decide-1",
                                "item_id": "fc-item-1",
                                "call_id": "call_stat_123",
                                "arguments": "{}",
                            }
                        )
                        await send(
                            {
                                "type": "response.output_item.done",
                                "response_id": "resp-decide-1",
                                "item": fc_item,
                            }
                        )
                        await send(
                            {
                                "type": "response.done",
                                "response": {
                                    "id": "resp-decide-1",
                                    "status": "completed",
                                    "output": [fc_item],
                                },
                            }
                        )

                elif event_type == "conversation.item.create":
                    item = object_value(event.get("item", {}))
                    if item.get("type") == "function_call_output":
                        received_tool_output.append(item)
        except Exception:
            pass

    async with serve(loopback_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connector(_url: str, key: str, seconds: float) -> RealtimeSocket:
            return await openai_module._connect(f"ws://127.0.0.1:{port}/realtime", key, seconds)

        container = RuntimeContainer(create_cloud_settings(tmp_path))
        assert isinstance(container.cloud_realtime_backend, OpenAIRealtimeBackend)
        container.cloud_realtime_backend._connector = connector
        await container.start()

        bridge: CloudRealtimeMediaBridge | None = None
        try:
            session = await container.sessions.create_session("default")
            assert container.cloud_realtime_factory is not None
            bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
            observed = observe_playback(bridge, monkeypatch)
            bridge._ensure_started()

            # Push user speech
            await bridge._handle_user_speaking_started()
            bridge._handle_input_audio(
                InputAudioRawFrame(audio=b"\x01\x00" * 320, sample_rate=16000, num_channels=1)
            )
            await bridge._handle_user_speaking_stopped()

            # Wait for tool execution, egress evaluation, and continuation completion
            await asyncio.wait_for(continuation_completed.wait(), timeout=5.0)
            await verify_playback(container, bridge, observed, output_pcm)

            assert len(received_tool_output) == 1
            assert received_tool_output[0]["call_id"] == "call_stat_123"
            out_str = str(received_tool_output[0]["output"])
            assert "runtime_version" in out_str

            assert len(received_continuation_request) == 1
            assert received_continuation_request[0]["tools"] == []
            assert received_continuation_request[0]["tool_choice"] == "none"

            # Verify SQLite 4-way lineage in skill_runs
            rows = await container.database.fetchall(
                "SELECT session_id, turn_id, generation_id, provider_tool_call_id, "
                "origin, state FROM skill_runs"
            )
            assert len(rows) == 1
            row = rows[0]
            assert str(row["session_id"]) == str(session.session_id)
            assert row["turn_id"] is not None  # turn_id
            assert row["generation_id"] is not None  # generation_id
            assert row["provider_tool_call_id"] == "call_stat_123"  # provider_tool_call_id
            assert row["origin"] == "agent"  # origin
            assert row["state"] == "succeeded"  # state
        finally:
            if bridge is not None:
                await bridge.coordinator.stop()
                await bridge.cleanup()
            await container.stop()


@pytest.mark.asyncio
async def test_02_permission_denied_sanitized_output() -> None:
    """2. Permission denied by skill gateway yields sanitized error payload."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    gateway = ScriptedSkillGateway(
        is_error=True,
        error_message="Operation not permitted for caller",
    )
    egress = make_egress_gateway(policy_mode="allow")
    tool = make_test_projected_tool()

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    sink = InMemoryDomainSink()
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=sink,
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(RealtimeToolCall(call_id="call_denied_1", name=tool.name, arguments={}),),
    )
    await coordinator.dispatch_event(event)

    # Wait for execution task
    task = bridge.active_task(gen_id)
    if task is not None:
        await task

    assert len(session.tool_results) == 1
    call_id, output_str = session.tool_results[0]
    assert call_id == "call_denied_1"
    output = json.loads(output_str)
    assert output["ok"] is False
    assert output["state"] == "failed"
    assert "Operation not permitted" in output["error"]["message"]


@pytest.mark.asyncio
async def test_03_unknown_tool_sanitized_output() -> None:
    """3. Unknown tool returns sanitized error and does not invoke gateway."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    gateway = ScriptedSkillGateway()
    egress = make_egress_gateway(policy_mode="allow")
    tool = make_test_projected_tool(name="exposed_tool")

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(
            RealtimeToolCall(
                call_id="call_unk_1",
                name="malicious_unexposed_tool",
                arguments={},
            ),
        ),
    )
    await coordinator.dispatch_event(event)

    task = bridge.active_task(gen_id)
    if task is not None:
        await task

    assert len(gateway.invocations) == 0
    assert len(session.tool_results) == 1
    call_id, output_str = session.tool_results[0]
    assert call_id == "call_unk_1"
    output = json.loads(output_str)
    assert output["ok"] is False
    assert output["error"]["code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_04_oversized_and_malformed_arguments() -> None:
    """4. Function call arguments bounds and validation fail closed safely."""
    sid = uuid4()
    mapper = OpenAIEventMapper(sid)

    # 4A. Arguments > 65536 bytes in response.done
    gen_a = uuid4()
    turn_a = mapper.register(gen_a, tools_exposed=True)
    turn_a.requested = True
    mapper.normalize(
        {
            "event_id": "evt_resp_a",
            "type": "response.created",
            "response": {
                "id": "resp_a",
                "metadata": {"cw_session_id": str(sid), "cw_generation_id": str(gen_a)},
            },
        }
    )
    oversized_args = '{"param": "' + ("a" * 70_000) + '"}'
    with pytest.raises(OpenAIRealtimeError) as exc_a:
        mapper.normalize(
            {
                "event_id": "evt_done_a",
                "type": "response.done",
                "response": {
                    "id": "resp_a",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_over_1",
                            "name": "status_read",
                            "arguments": oversized_args,
                        }
                    ],
                },
            }
        )
    assert "openai_realtime_invalid_function_call" in str(exc_a.value)

    # 4B. Malformed JSON
    gen_b = uuid4()
    turn_b = mapper.register(gen_b, tools_exposed=True)
    turn_b.requested = True
    mapper.normalize(
        {
            "event_id": "evt_resp_b",
            "type": "response.created",
            "response": {
                "id": "resp_b",
                "metadata": {"cw_session_id": str(sid), "cw_generation_id": str(gen_b)},
            },
        }
    )
    with pytest.raises(OpenAIRealtimeError) as exc_b:
        mapper.normalize(
            {
                "event_id": "evt_done_b",
                "type": "response.done",
                "response": {
                    "id": "resp_b",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_bad_1",
                            "name": "status_read",
                            "arguments": '{"invalid_json',
                        }
                    ],
                },
            }
        )
    assert "openai_realtime_invalid_function_call" in str(exc_b.value)

    # 4C. NaN / Infinity constant rejection
    gen_c = uuid4()
    turn_c = mapper.register(gen_c, tools_exposed=True)
    turn_c.requested = True
    mapper.normalize(
        {
            "event_id": "evt_resp_c",
            "type": "response.created",
            "response": {
                "id": "resp_c",
                "metadata": {"cw_session_id": str(sid), "cw_generation_id": str(gen_c)},
            },
        }
    )
    with pytest.raises(OpenAIRealtimeError) as exc_c:
        mapper.normalize(
            {
                "event_id": "evt_done_c",
                "type": "response.done",
                "response": {
                    "id": "resp_c",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_nan_1",
                            "name": "status_read",
                            "arguments": '{"val": NaN}',
                        }
                    ],
                },
            }
        )
    assert "openai_realtime_invalid_function_call" in str(exc_c.value)

    # 4D. Nested JSON depth > 8 rejection
    gen_d = uuid4()
    turn_d = mapper.register(gen_d, tools_exposed=True)
    turn_d.requested = True
    mapper.normalize(
        {
            "event_id": "evt_resp_d",
            "type": "response.created",
            "response": {
                "id": "resp_d",
                "metadata": {"cw_session_id": str(sid), "cw_generation_id": str(gen_d)},
            },
        }
    )
    deep_json = '{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":{"i":{"j":1}}}}}}}}}}'
    with pytest.raises(OpenAIRealtimeError) as exc_d:
        mapper.normalize(
            {
                "event_id": "evt_done_d",
                "type": "response.done",
                "response": {
                    "id": "resp_d",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_deep_1",
                            "name": "status_read",
                            "arguments": deep_json,
                        }
                    ],
                },
            }
        )
    assert "openai_realtime_invalid_function_call" in str(exc_d.value)

    # 4E. Duplicate call ID with mutated arguments within single response.done
    gen_e = uuid4()
    turn_e = mapper.register(gen_e, tools_exposed=True)
    turn_e.requested = True
    mapper.normalize(
        {
            "event_id": "evt_resp_e",
            "type": "response.created",
            "response": {
                "id": "resp_e",
                "metadata": {"cw_session_id": str(sid), "cw_generation_id": str(gen_e)},
            },
        }
    )
    with pytest.raises(OpenAIRealtimeError) as exc_e:
        mapper.normalize(
            {
                "event_id": "evt_done_e",
                "type": "response.done",
                "response": {
                    "id": "resp_e",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_dup_1",
                            "name": "status_read",
                            "arguments": '{"query": "first"}',
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_dup_1",
                            "name": "status_read",
                            "arguments": '{"query": "mutated"}',
                        },
                    ],
                },
            }
        )
    assert "openai_realtime_invalid_function_call" in str(exc_e.value)


@pytest.mark.asyncio
async def test_05_duplicate_tool_calls_and_changed_args_rejection() -> None:
    """5. Changed args cancels gen; duplicate call_id suppressed; max 4 enforced."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    gateway = ScriptedSkillGateway()
    egress = make_egress_gateway(policy_mode="allow")
    tool = make_test_projected_tool()

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    # 5A. Changed args on same call ID
    call_orig = RealtimeToolCall(call_id="call_x", name=tool.name, arguments={"arg": 1})
    call_mutated = RealtimeToolCall(call_id="call_x", name=tool.name, arguments={"arg": 2})

    event_a = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(call_orig,),
        event_id="evt_1",
    )
    await coordinator.dispatch_event(event_a)

    # Second event with mutated args on call_x
    event_b = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(call_mutated,),
        event_id="evt_2",
    )
    await coordinator.dispatch_event(event_b)

    # Generation is cancelled due to mutation
    assert mirror.is_tombstoned(gen_id)

    # 5B. Duplicate call with same args across differing event IDs -> suppressed
    gen_id_2 = uuid4()
    mirror.register_generation(gen_id_2, turn_id)
    call_stable = RealtimeToolCall(call_id="call_y", name=tool.name, arguments={"arg": "same"})

    event_c = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id_2,
        provider_response_id="resp_2",
        calls=(call_stable,),
        event_id="evt_c",
    )
    await coordinator.dispatch_event(event_c)

    event_c_dup = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id_2,
        provider_response_id="resp_2",
        calls=(call_stable,),
        event_id="evt_d",  # Different event ID!
    )
    await coordinator.dispatch_event(event_c_dup)

    task = bridge.active_task(gen_id_2)
    if task is not None:
        await task

    # Only 1 invocation occurred for call_y
    invocations_for_y = [
        inv for inv in gateway.invocations if inv["provider_tool_call_id"] == "call_y"
    ]
    assert len(invocations_for_y) == 1

    # 5C. Max 4 calls per decision round
    gen_id_3 = uuid4()
    mirror.register_generation(gen_id_3, turn_id)
    six_calls = tuple(
        RealtimeToolCall(call_id=f"call_many_{i}", name=tool.name, arguments={"i": i})
        for i in range(6)
    )
    event_six = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id_3,
        provider_response_id="resp_3",
        calls=six_calls,
        event_id="evt_six",
    )
    await coordinator.dispatch_event(event_six)
    task_3 = bridge.active_task(gen_id_3)
    if task_3 is not None:
        await task_3

    invocations_for_gen3 = [inv for inv in gateway.invocations if inv["generation_id"] == gen_id_3]
    assert len(invocations_for_gen3) == 4


@pytest.mark.asyncio
async def test_06_interruption_safely_cancels_in_flight_run() -> None:
    """6. Interruption cancels in-flight tool tasks with shielded admission."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    delay_event = asyncio.Event()
    gateway = ScriptedSkillGateway(delay_event=delay_event)
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    egress = make_egress_gateway(policy_mode="allow")
    tool = make_test_projected_tool()

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(RealtimeToolCall(call_id="call_slow", name=tool.name, arguments={}),),
    )
    await coordinator.dispatch_event(event)

    # Wait until execution has admitted run and entered wait_for_terminal
    await asyncio.wait_for(gateway.waiting_event.wait(), timeout=1.0)
    runs = bridge.active_runs(gen_id)
    assert len(runs) == 1
    run_id = next(iter(runs))

    # Interrupt generation
    await coordinator.cancel_generation(gen_id, reason="user_barge_in")

    assert run_id in gateway.cancelled_runs

    # Unblock the skill gateway
    delay_event.set()

    # Zero writes sent to provider
    assert len(session.tool_results) == 0


@pytest.mark.asyncio
async def test_07_provider_eof_cancels_in_flight_tool_tasks() -> None:
    """7. Provider EOF cancels in-flight tool tasks without orphaned tasks."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    delay_event = asyncio.Event()
    gateway = ScriptedSkillGateway(delay_event=delay_event)
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    egress = make_egress_gateway(policy_mode="allow")
    tool = make_test_projected_tool()

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(RealtimeToolCall(call_id="call_eof", name=tool.name, arguments={}),),
    )
    await coordinator.dispatch_event(event)
    await asyncio.wait_for(gateway.waiting_event.wait(), timeout=1.0)

    # Stop bridge as in teardown / EOF
    await bridge.stop()

    assert len(gateway.cancelled_runs) >= 1
    assert len(bridge.active_runs(gen_id)) == 0
    delay_event.set()


@pytest.mark.asyncio
async def test_08_late_old_response_tombstone_fence() -> None:
    """8. Late tool calls for tombstoned / cancelled generations are strictly dropped."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    gateway = ScriptedSkillGateway()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=make_egress_gateway(policy_mode="allow"),
        tools_snapshot={"status_read": make_test_projected_tool()},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    # Cancel generation beforehand
    mirror.cancel_generation(gen_id)

    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_old",
        calls=(RealtimeToolCall(call_id="call_tomb", name="status_read", arguments={}),),
    )
    await coordinator.dispatch_event(event)

    assert len(gateway.invocations) == 0
    assert len(session.tool_results) == 0


@pytest.mark.asyncio
async def test_09_rebind_without_reserved_continuation_rejected() -> None:
    """9. Provider rebind without explicit continuation raises OpenAIRealtimeError."""
    sid = uuid4()
    gen_id = uuid4()
    mapper = OpenAIEventMapper(sid)
    turn = mapper.register(gen_id, tools_exposed=True)
    turn.requested = True

    meta = {"cw_session_id": str(sid), "cw_generation_id": str(gen_id)}

    # First response.created
    evt_1 = mapper.normalize(
        {
            "event_id": "evt_resp_1",
            "type": "response.created",
            "response": {"id": "resp_first", "metadata": meta},
        }
    )
    assert len(evt_1) == 1
    assert isinstance(evt_1[0], ResponseStartedEvent)

    # Second response.created without continuation_reserved -> must raise!
    with pytest.raises(OpenAIRealtimeError) as exc_info:
        mapper.normalize(
            {
                "event_id": "evt_resp_2_unres",
                "type": "response.created",
                "response": {"id": "resp_second_unreserved", "metadata": meta},
            }
        )
    assert "openai_realtime_response_rebinding" in str(exc_info.value)

    # Now explicitly reserve continuation
    mapper.reserve_continuation(gen_id)

    # Second response.created WITH continuation_reserved succeeds
    evt_2 = mapper.normalize(
        {
            "event_id": "evt_resp_2_res",
            "type": "response.created",
            "response": {"id": "resp_second_reserved", "metadata": meta},
        }
    )
    assert len(evt_2) == 1
    assert isinstance(evt_2[0], ResponseStartedEvent)
    assert evt_2[0].provider_response_id == "resp_second_reserved"


@pytest.mark.asyncio
async def test_10_egress_policy_deny_ask_audit_failure_zero_writes() -> None:
    """10. Egress policy deny/ask and audit failure guarantee zero socket writes."""
    sid = uuid4()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )

    # 10A. Policy deny
    egress_deny = make_egress_gateway(policy_mode="deny")
    with pytest.raises(PolicyDeniedError):
        await egress_deny.evaluate_and_submit_tool_result(
            session=session,
            backend_id=session.backend_id,
            call_id="call_deny",
            output="{}",
        )
    assert len(session.tool_results) == 0

    # 10B. Policy ask without grant
    egress_ask = make_egress_gateway(policy_mode="ask")
    with pytest.raises(ConsentRequiredError):
        await egress_ask.evaluate_and_submit_tool_result(
            session=session,
            backend_id=session.backend_id,
            call_id="call_ask_nogrant",
            output="{}",
        )
    assert len(session.tool_results) == 0

    # 10C. Policy ask with grant missing tool_result
    egress_ask.grant_consent(
        EgressGrant(
            session_id=sid,
            backend_id=session.backend_id,
            approved_by="user",
            allowed_component_kinds=frozenset({"memory", "kernel"}),
        ),
    )
    with pytest.raises(ConsentRequiredError):
        await egress_ask.evaluate_and_submit_tool_result(
            session=session,
            backend_id=session.backend_id,
            call_id="call_ask_missing_kind",
            output="{}",
        )
    assert len(session.tool_results) == 0

    # 10D. Policy ask with grant including tool_result -> succeeds
    egress_ask.grant_consent(
        EgressGrant(
            session_id=sid,
            backend_id=session.backend_id,
            approved_by="user",
            allowed_component_kinds=frozenset({"tool_result"}),
        ),
    )
    await egress_ask.evaluate_and_submit_tool_result(
        session=session,
        backend_id=session.backend_id,
        call_id="call_ask_ok",
        output="{}",
    )
    assert len(session.tool_results) == 1
    assert session.tool_results[0][0] == "call_ask_ok"

    # 10E. Audit persistence failure -> fail closed, 0 writes
    class BrokenEventStore:
        async def append(self, model: object) -> object:
            raise RuntimeError("Durable SQLite disk failure")

    broken_store = BrokenEventStore()
    egress_broken = CloudEgressGateway(
        policy_mode="allow",
        event_store=cast(Any, broken_store),
    )
    with pytest.raises(RuntimeError) as exc_info:
        await egress_broken.evaluate_and_submit_tool_result(
            session=session,
            backend_id=session.backend_id,
            call_id="call_broken_audit",
            output="{}",
        )
    assert "Egress audit persistence failed" in str(exc_info.value)
    # Ensure no additional writes happened
    assert len(session.tool_results) == 1

    # 10F. Absent event store -> fail closed, 0 writes
    egress_no_store = CloudEgressGateway(policy_mode="allow", event_store=None)
    with pytest.raises(RuntimeError) as exc_no_store:
        await egress_no_store.evaluate_and_submit_tool_result(
            session=session,
            backend_id=session.backend_id,
            call_id="call_no_store",
            output="{}",
        )
    assert "Durable event store is absent" in str(exc_no_store.value)
    assert len(session.tool_results) == 1


def test_11_result_size_bounds_truncation() -> None:
    """11. Results exceeding 32 KiB schema budget are truncated with metadata flag."""
    huge_data: dict[str, JsonValue] = {"key": "x" * 50_000}
    snapshot = SkillRunSnapshot(
        skill_run_id=uuid4(),
        session_id=uuid4(),
        skill_id="test.huge",
        skill_version="1.0",
        capability="read",
        state=SkillRunState.SUCCEEDED,
        result=SkillResult(status="succeeded", data=huge_data),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    payload, summary = format_tool_result_payload(snapshot)
    bounded = bounded_tool_result_payload(payload, summary, max_bytes=32_768)

    encoded = json.dumps(bounded, ensure_ascii=False)
    assert len(encoded.encode("utf-8")) <= 32_768
    assert bounded.get("truncated") is True


@pytest.mark.asyncio
async def test_12_real_runtime_skill_admission_cancellation_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12. Real RuntimeSkillService admission cancel regression (commit 9314314).

    Verifies that cancellation occurring during admission snapshot read (get_run)
    compensates the run and cancels the scheduled worker task without orphan workers.
    """
    container = RuntimeContainer(create_cloud_settings(tmp_path))
    await container.start()
    service = container.runtime_skills
    snapshot_entered = asyncio.Event()
    worker_started = asyncio.Event()
    worker_cancelled = asyncio.Event()
    release = asyncio.Event()
    captured: list[UUID] = []
    original_get_run = service.get_run
    invocation_task: asyncio.Task[object] | None = None

    async def held_worker(_run_id: UUID) -> None:
        worker_started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    async def held_snapshot(run_id: UUID) -> SkillRunSnapshot:
        if asyncio.current_task() is invocation_task:
            captured.append(run_id)
            snapshot_entered.set()
            await release.wait()
        return await original_get_run(run_id)

    monkeypatch.setattr(service, "_execute", held_worker)
    monkeypatch.setattr(service, "get_run", held_snapshot)

    try:
        session = await container.sessions.create_session("default")
        invocation_task = asyncio.create_task(
            service.invoke(
                session.session_id,
                SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
                allow_confirmation=False,
            )
        )
        await asyncio.wait_for(snapshot_entered.wait(), timeout=2.0)
        await asyncio.wait_for(worker_started.wait(), timeout=2.0)
        invocation_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation_task
        assert worker_cancelled.is_set(), "Admission cancel left an orphaned worker"
        assert len(captured) == 1
        terminal = await original_get_run(captured[0])
        assert terminal.state in {SkillRunState.FAILED, SkillRunState.CANCELLED}
    finally:
        release.set()
        if invocation_task is not None:
            await asyncio.gather(invocation_task, return_exceptions=True)
        await container.stop()


@pytest.mark.asyncio
async def test_13_permission_required_denial_no_pending_confirmation(
    tmp_path: Path,
) -> None:
    """13. Capability requiring confirmation is rejected immediately when allow_confirmation=False.

    Leaves 0 pending confirmation requests, 0 pending timers, and safely fails via compensation.
    """
    container = RuntimeContainer(create_cloud_settings(tmp_path))
    await container.start()
    try:
        service = container.runtime_skills
        await service.install_example_plugin("local-echo")
        session = await container.sessions.create_session("default")

        # Invoke append_note which has confirmation_required=True
        with pytest.raises(PermissionError) as exc_info:
            await service.invoke(
                session.session_id,
                SkillInvocation(
                    skill_id="local.echo",
                    capability="append_note",
                    arguments={"text": "cannot confirm in voice turn"},
                ),
                allow_confirmation=False,
            )
        assert "Non-interactive invocation rejected" in str(exc_info.value)
        assert "confirmation_required" in str(exc_info.value)

        # Verify zero pending confirmation requests
        pending = await service.pending_confirmations(session.session_id)
        assert len(pending) == 0

        # Verify zero active confirmation expiry tasks
        assert len(service._confirmation_expiry_tasks) == 0

        # Verify the skill run was compensated to failed state
        rows = await container.database.fetchall(
            "SELECT state, error_json FROM skill_runs WHERE session_id = ?",
            (str(session.session_id),),
        )
        assert len(rows) == 1
        assert rows[0]["state"] == "failed"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_14_interrupt_after_audit_before_write_drops_under_lock() -> None:
    """14. Interruption arriving after audit before submit_tool_result drops write under lock."""
    sid = uuid4()
    gen_id = uuid4()

    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )

    class InterceptingEventStore(InMemoryEventStore):
        async def append[EventT: EventModel](self, event: EventT) -> EventT:
            await super().append(event)
            # Simulate user interrupt arriving right as durable audit succeeds, before session write
            await session.interrupt(generation_id=gen_id)
            return event

    store = InterceptingEventStore()
    egress = make_egress_gateway(policy_mode="allow", event_store=store)

    # Submit tool result with gen_id
    receipt = await egress.evaluate_and_submit_tool_result(
        session=session,
        backend_id=session.backend_id,
        call_id="call_racy_interrupt",
        output='{"status": "ok"}',
        generation_id=gen_id,
    )

    assert receipt is not None
    assert len(store.events) == 1
    # Under write lock, submit_tool_result detected interrupted gen_id and dropped write!
    assert len(session.tool_results) == 0


@pytest.mark.asyncio
async def test_15_worker_triggered_egress_denial_no_self_await() -> None:
    """15. Egress denial inside worker cancels generation without self-await deadlock or crash."""
    sid = uuid4()
    gen_id = uuid4()
    turn_id = uuid4()

    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"),
        tools_enabled=True,
    )
    gateway = ScriptedSkillGateway()
    # Egress policy is deny -> will trigger exception in _execute_tool_round
    egress = make_egress_gateway(policy_mode="deny")
    tool = make_test_projected_tool()

    bridge = CloudToolBridge(
        session=session,
        skills=gateway,
        egress_gateway=egress,
        tools_snapshot={tool.name: tool},
        backend_id=session.backend_id,
    )

    mirror = RealtimeSessionMirror(session_id=sid, backend_id=session.backend_id)
    mirror.register_generation(gen_id, turn_id)
    coordinator = CloudRealtimeCoordinator(
        session_id=sid,
        session=session,
        mirror=mirror,
        domain_sink=InMemoryDomainSink(),
        tool_bridge=bridge,
    )

    event = ToolCallRequestedEvent(
        session_id=sid,
        generation_id=gen_id,
        provider_response_id="resp_1",
        calls=(RealtimeToolCall(call_id="call_denied_egress", name=tool.name, arguments={}),),
    )
    await coordinator.dispatch_event(event)

    task = bridge.active_task(gen_id)
    if task is not None:
        # Awaiting the background task must complete cleanly without RuntimeError (no self-await)
        await task

    # Mirror should be tombstoned
    assert mirror.is_tombstoned(gen_id)
    # Zero writes sent to provider
    assert len(session.tool_results) == 0


@pytest.mark.asyncio
async def test_16_late_old_decision_chunks_dropped() -> None:
    """16. Late chunks targeting a completed decision response are safely dropped."""
    sid = uuid4()
    gen_id = uuid4()
    mapper = OpenAIEventMapper(sid)
    turn = mapper.register(gen_id, tools_exposed=True)
    turn.requested = True

    meta = {"cw_session_id": str(sid), "cw_generation_id": str(gen_id)}

    # Start response
    mapper.normalize(
        {
            "event_id": "evt_1",
            "type": "response.created",
            "response": {"id": "resp_decide", "metadata": meta},
        }
    )

    # Complete response.done with function call
    mapper.normalize(
        {
            "event_id": "evt_2",
            "type": "response.done",
            "response": {
                "id": "resp_decide",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "status_read",
                        "arguments": "{}",
                    }
                ],
            },
        }
    )
    assert turn.decision_done is True
    assert turn.tool_calls_emitted is True

    # Now late events arrive referencing resp_decide
    late_events: list[dict[str, object]] = [
        {
            "event_id": "evt_late_1",
            "type": "response.function_call_arguments.delta",
            "response_id": "resp_decide",
            "call_id": "call_1",
            "delta": "{}",
        },
        {
            "event_id": "evt_late_2",
            "type": "response.output_item.added",
            "response_id": "resp_decide",
            "output_index": 1,
            "item": {
                "id": "fc_late",
                "type": "function_call",
                "call_id": "call_2",
                "name": "foo",
            },
        },
        {
            "event_id": "evt_late_3",
            "type": "response.output_audio.delta",
            "response_id": "resp_decide",
            "delta": "AQAB",
        },
    ]

    for late_evt in late_events:
        res = mapper.normalize(late_evt)
        assert res == []


@pytest.mark.asyncio
async def test_17_duplicate_response_created_idempotent() -> None:
    """17. Duplicate response.created with new event_id and same response_id is idempotent."""
    sid = uuid4()
    gen_id = uuid4()
    mapper = OpenAIEventMapper(sid)
    turn = mapper.register(gen_id, tools_exposed=True)
    turn.requested = True

    meta = {"cw_session_id": str(sid), "cw_generation_id": str(gen_id)}

    # Initial response.created
    res_1 = mapper.normalize(
        {
            "event_id": "evt_resp_1",
            "type": "response.created",
            "response": {"id": "resp_same", "metadata": meta},
        }
    )
    assert len(res_1) == 1
    assert isinstance(res_1[0], ResponseStartedEvent)

    # Replay of identical response.created with different event_id
    res_2 = mapper.normalize(
        {
            "event_id": "evt_resp_replay",
            "type": "response.created",
            "response": {"id": "resp_same", "metadata": meta},
        }
    )
    assert res_2 == []

    # Reserve continuation
    mapper.reserve_continuation(gen_id)

    # First continuation response.created
    res_cont_1 = mapper.normalize(
        {
            "event_id": "evt_cont_1",
            "type": "response.created",
            "response": {"id": "resp_cont_same", "metadata": meta},
        }
    )
    assert len(res_cont_1) == 1
    assert isinstance(res_cont_1[0], ResponseStartedEvent)

    # Replay of continuation response.created with different event_id
    res_cont_2 = mapper.normalize(
        {
            "event_id": "evt_cont_replay",
            "type": "response.created",
            "response": {"id": "resp_cont_same", "metadata": meta},
        }
    )
    assert res_cont_2 == []


@pytest.mark.asyncio
async def test_18_normal_text_decision_continuation_streaming_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """18. Normal text decision: text-only decision, preface deleted, continuation streams audio."""
    continuation_completed = asyncio.Event()
    received_modalities: list[list[str]] = []
    deleted_item_ids: list[str] = []
    received_tool_choices: list[str] = []
    output_pcm = b"\x02\x00" * 480

    async def loopback_handler(ws: ServerConnection) -> None:
        sequence = 0

        async def send(event: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            await ws.send(json.dumps({"event_id": f"wire-{sequence}", **event}))

        await send(
            {
                "type": "session.created",
                "session": {"id": "loopback-sess", "type": "realtime"},
            }
        )
        try:
            async for raw in ws:
                event = object_value(json.loads(raw))
                event_type = event.get("type")

                if event_type == "session.update":
                    sess_cfg = object_value(event["session"])
                    await send(
                        {
                            "type": "session.updated",
                            "session": {**sess_cfg, "id": "loopback-sess"},
                        }
                    )

                elif event_type == "input_audio_buffer.commit":
                    await send({"type": "input_audio_buffer.committed", "item_id": "input-1"})

                elif event_type == "conversation.item.delete":
                    item_id = str(event.get("item_id", ""))
                    deleted_item_ids.append(item_id)
                    await send({"type": "conversation.item.deleted", "item_id": item_id})

                elif event_type == "response.create":
                    resp_data = object_value(event.get("response", {}))
                    metadata = resp_data.get("metadata", {})
                    mods = [
                        str(m) for m in cast(list[object], resp_data.get("output_modalities", []))
                    ]
                    received_modalities.append(mods)
                    tool_choice = str(resp_data.get("tool_choice", "auto"))
                    received_tool_choices.append(tool_choice)

                    if tool_choice == "none":
                        # Continuation response
                        await send(
                            {
                                "type": "response.created",
                                "response": {"id": "resp-cont-text", "metadata": metadata},
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio.delta",
                                "response_id": "resp-cont-text",
                                "delta": base64.b64encode(output_pcm).decode(),
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio.done",
                                "response_id": "resp-cont-text",
                            }
                        )
                        await send(
                            {
                                "type": "response.output_audio_transcript.done",
                                "response_id": "resp-cont-text",
                                "transcript": "Hello, how can I help you today?",
                            }
                        )
                        await send(
                            {
                                "type": "response.done",
                                "response": {
                                    "id": "resp-cont-text",
                                    "status": "completed",
                                    "output": [
                                        {
                                            "type": "message",
                                            "role": "assistant",
                                            "content": [
                                                {
                                                    "type": "output_audio",
                                                    "transcript": "How can I help you?",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            }
                        )
                        continuation_completed.set()
                    else:
                        # Initial text decision round (no tools called!)
                        await send(
                            {
                                "type": "response.created",
                                "response": {"id": "resp-decide-text", "metadata": metadata},
                            }
                        )
                        preface_item = {
                            "id": "preface-item-001",
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Let me think about that."}],
                        }
                        await send(
                            {
                                "type": "response.output_item.added",
                                "response_id": "resp-decide-text",
                                "output_index": 0,
                                "item": preface_item,
                            }
                        )
                        await send(
                            {
                                "type": "response.output_item.done",
                                "response_id": "resp-decide-text",
                                "item": preface_item,
                            }
                        )
                        await send(
                            {
                                "type": "response.done",
                                "response": {
                                    "id": "resp-decide-text",
                                    "status": "completed",
                                    "output": [preface_item],
                                },
                            }
                        )
        except Exception:
            pass

    async with serve(loopback_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connector(_url: str, key: str, seconds: float) -> RealtimeSocket:
            return await openai_module._connect(f"ws://127.0.0.1:{port}/realtime", key, seconds)

        container = RuntimeContainer(create_cloud_settings(tmp_path))
        assert isinstance(container.cloud_realtime_backend, OpenAIRealtimeBackend)
        container.cloud_realtime_backend._connector = connector
        await container.start()

        bridge: CloudRealtimeMediaBridge | None = None
        try:
            session = await container.sessions.create_session("default")
            assert container.cloud_realtime_factory is not None
            bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
            observed = observe_playback(bridge, monkeypatch)
            bridge._ensure_started()

            await bridge._handle_user_speaking_started()
            bridge._handle_input_audio(
                InputAudioRawFrame(audio=b"\x01\x00" * 320, sample_rate=16000, num_channels=1)
            )
            await bridge._handle_user_speaking_stopped()

            # Wait for decision round -> preface deletion -> continuation audio
            await asyncio.wait_for(continuation_completed.wait(), timeout=5.0)
            await verify_playback(container, bridge, observed, output_pcm)

            # Assertions
            # 1. Initial response requested modalities: ["text"]
            assert received_modalities[0] == ["text"]
            # 2. Preface was deleted before continuation was requested
            assert "preface-item-001" in deleted_item_ids
            # 3. Continuation requested modalities: ["text", "audio"] and tool_choice: "none"
            assert received_modalities[1] == ["audio"]
            assert received_tool_choices[1] == "none"
        finally:
            if bridge is not None:
                await bridge.coordinator.stop()
                await bridge.cleanup()
            await container.stop()


def test_mirror_only_accepts_one_reserved_continuation() -> None:
    mirror = RealtimeSessionMirror(uuid4(), backend_id="fake")
    gen = uuid4()
    mirror.register_generation(gen, uuid4(), provider_response_id="decision")
    assert mirror.bind_provider_response("unreserved", gen) is None
    mirror.reserve_continuation(gen)
    assert mirror.bind_provider_response("final", gen) is not None
    assert mirror.bind_provider_response("final", gen) is not None
    mirror.reserve_continuation(gen)
    assert mirror.bind_provider_response("third", gen) is None
    assert mirror.lookup_response_generation("unreserved") is None
    assert mirror.lookup_response_generation("third") is None


@pytest.mark.asyncio
async def test_cloud_admission_rechecks_readonly_registry_policy(tmp_path: Path) -> None:
    container = RuntimeContainer(create_cloud_settings(tmp_path))
    await container.start()
    try:
        service = container.runtime_skills
        session = await container.sessions.create_session("default")
        entry = service._registry.get("runtime.status")
        assert entry is not None
        entry.definition.capabilities[0] = entry.definition.capabilities[0].model_copy(
            update={"side_effect": SideEffect.WRITE}
        )
        with pytest.raises(PermissionError, match="no longer eligible"):
            await service.invoke(
                session.session_id,
                SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
                allow_confirmation=False,
                require_cloud_readonly=True,
            )
        assert not await container.database.fetchall("SELECT * FROM skill_runs")
        assert not await service.pending_confirmations(session.session_id)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_direct_unknown_response_cannot_reserve_or_invoke() -> None:
    sid, gen, tid = uuid4(), uuid4(), uuid4()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default"), tools_enabled=True
    )
    mirror = RealtimeSessionMirror(sid, backend_id="fake")
    mirror.register_generation(gen, tid, provider_response_id="known")
    skills = ScriptedSkillGateway()
    bridge = CloudToolBridge(
        session=session,
        skills=skills,
        egress_gateway=make_egress_gateway(policy_mode="allow"),
        tools_snapshot={"status_read": make_test_projected_tool()},
        backend_id=session.backend_id,
        mirror=mirror,
    )
    await bridge.handle_tool_calls(
        ToolCallRequestedEvent(
            sid, gen, "unknown", (RealtimeToolCall("call_unknown", "status_read", {}),)
        ),
        turn_id=tid,
    )
    assert not skills.invocations
    assert not bridge._seen_call_ids
    assert not bridge._executed_generations
    assert bridge.active_task(gen) is None
