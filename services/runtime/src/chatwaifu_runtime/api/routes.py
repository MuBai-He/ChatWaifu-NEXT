"""Loopback Runtime HTTP and WebSocket routes."""

import asyncio
import base64
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from chatwaifu_protocol.commands import PlaybackAckCommand
from chatwaifu_protocol.skills import SkillInvocation
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse

from chatwaifu_runtime import __version__
from chatwaifu_runtime.api.models import (
    AliyunCosyVoiceTtsConfigurationRequest,
    AliyunTtsConfigurationRequest,
    CharacterInteractionRequest,
    CreateSessionRequest,
    ExamplePluginRequest,
    InstallPluginRequest,
    InterruptRequest,
    MemoryCorrectionRequest,
    MemoryPinnedRequest,
    MemoryProposalDecisionRequest,
    ModelRoleConfigurationRequest,
    PluginEnabledRequest,
    ResetSessionRequest,
    RuntimeHealth,
    SkillConfirmationDecisionRequest,
    SubmitTextRequest,
    TtsProviderSelectionRequest,
    WebRtcOfferRequest,
    WebRtcPatchRequest,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.companion.models import CompanionSettingsUpdate
from chatwaifu_runtime.providers.model_config import MODEL_ROLES, ModelRole, ModelRoleConfig
from chatwaifu_runtime.providers.tts_config import (
    AliyunCosyVoiceTtsConfiguration,
    AliyunTtsConfiguration,
)

router = APIRouter(prefix="/v1")


def _container(request: Request) -> RuntimeContainer:
    return request.app.state.container


@router.get("/runtime/health", response_model=RuntimeHealth)
async def runtime_health(request: Request) -> RuntimeHealth:
    container = _container(request)
    providers = container.providers.public_status()
    providers["stt"] = container.stt.kind
    return RuntimeHealth(
        status="ok",
        version=__version__,
        database="ready",
        subscribers=container.event_hub.subscriber_count,
        dropped_events=container.event_hub.dropped_events,
        providers=providers,
        resources=container.resources.status().model_dump(mode="json"),
    )


@router.get("/companion/settings")
async def read_companion_settings(request: Request) -> dict[str, object]:
    return _container(request).companion_settings.get().model_dump(mode="json")


@router.put("/companion/settings")
async def update_companion_settings(
    request: Request, body: CompanionSettingsUpdate
) -> dict[str, object]:
    container = _container(request)
    settings = await container.companion_settings.update(body)
    container.resources.touch()
    container.ambient.settings_changed()
    return settings.model_dump(mode="json")


@router.get("/companion/status")
async def read_companion_status(request: Request) -> dict[str, object]:
    container = _container(request)
    return (await container.ambient.status()).model_dump(mode="json")


@router.post("/companion/resources/sleep")
async def sleep_companion_resources(request: Request) -> dict[str, object]:
    try:
        status_snapshot = await _container(request).resources.sleep_now()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return status_snapshot.model_dump(mode="json")


@router.post("/companion/resources/wake")
async def wake_companion_resources(request: Request) -> dict[str, object]:
    return _container(request).resources.wake().model_dump(mode="json")


@router.post("/sessions/{session_id}/companion/proactive")
async def trigger_proactive_preview(request: Request, session_id: UUID) -> dict[str, object]:
    try:
        accepted = await _container(request).ambient.trigger_manual(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "session_id": str(accepted.session_id),
        "turn_id": str(accepted.turn_id),
        "generation_id": str(accepted.generation_id),
        "state": accepted.state.value,
    }


@router.get("/runtime/version")
async def runtime_version() -> dict[str, str]:
    return {"name": "chatwaifu-runtime", "version": __version__, "protocol": "1.0"}


@router.get("/config")
async def runtime_config(request: Request) -> dict[str, object]:
    return _container(request).settings.public_dict()


@router.get("/model-configurations")
async def read_model_configurations(request: Request) -> dict[str, object]:
    items = [
        item.model_dump(mode="json") for item in _container(request).model_configurations.list()
    ]
    return {"schema_version": "1.0", "items": items, "count": len(items)}


@router.put("/model-configurations/{role}")
async def update_model_configuration(
    request: Request,
    role: ModelRole,
    body: ModelRoleConfigurationRequest,
) -> dict[str, object]:
    if role not in MODEL_ROLES:
        raise HTTPException(status_code=404, detail="model role not found")
    try:
        config = ModelRoleConfig(
            role=role,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            timeout_seconds=body.timeout_seconds,
            context_window=body.context_window,
            enabled=body.enabled,
            updated_at=datetime.now(UTC),
        )
        updated = await _container(request).model_configurations.update(
            config,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
        )
        if role == "embedding":
            await _container(request).memory.reindex_all()
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return updated.model_dump(mode="json")


@router.post("/model-configurations/{role}/test")
async def test_model_configuration(request: Request, role: ModelRole) -> dict[str, object]:
    try:
        result = await _container(request).model_configurations.probe(role)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"model probe failed: {error}") from error
    return {"role": role, **result}


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, object]:
    container = _container(request)
    if container.characters.get(body.character_id) is None:
        raise HTTPException(status_code=404, detail="character not found")
    snapshot = await container.sessions.create_session(body.character_id)
    container.activity.touch(snapshot.session_id)
    container.resources.touch()
    container.providers.tts.bind_session(snapshot.session_id)
    return snapshot.model_dump(mode="json")


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: UUID) -> dict[str, object]:
    snapshot = await _container(request).sessions.get_session(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="session not found")
    _container(request).activity.touch(session_id)
    return snapshot.model_dump(mode="json")


@router.get("/sessions/{session_id}/character-state")
async def read_character_state(request: Request, session_id: UUID) -> dict[str, object]:
    container = _container(request)
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    snapshot = await container.character_kernel.snapshot(session.character_id)
    return snapshot.model_dump(mode="json")


@router.post("/sessions/{session_id}/character-interactions")
async def register_character_interaction(
    request: Request,
    session_id: UUID,
    body: CharacterInteractionRequest,
) -> dict[str, object]:
    container = _container(request)
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.activity.touch(session_id)
    container.resources.touch()
    snapshot = await container.character_kernel.observe_interaction(
        session_id=session_id,
        character_id=session.character_id,
        kind=body.kind,
        region=body.region,
    )
    return snapshot.model_dump(mode="json")


@router.delete("/sessions/{session_id}")
async def close_session(request: Request, session_id: UUID) -> dict[str, object]:
    try:
        await _container(request).voice_media.close_session(session_id)
        await _container(request).conversation.cancel(session_id, "session_closing")
        snapshot = await _container(request).sessions.close_session(session_id)
        await _container(request).providers.tts.release_session(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    return snapshot.model_dump(mode="json")


@router.get("/tts/providers")
async def read_tts_providers(
    request: Request,
    session_id: UUID | None = None,
) -> dict[str, object]:
    container = _container(request)
    if session_id is not None and await container.sessions.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    items: list[dict[str, object]] = []
    for snapshot in await container.providers.tts.snapshots(session_id):
        descriptor = snapshot.descriptor
        health = snapshot.health
        items.append(
            {
                "provider_id": descriptor.provider_id,
                "display_name": descriptor.display_name,
                "model": descriptor.model,
                "languages": list(descriptor.languages),
                "supports_voice_cloning": descriptor.supports_voice_cloning,
                "supports_style": descriptor.supports_style,
                "supports_speed": descriptor.supports_speed,
                "supports_pitch": descriptor.supports_pitch,
                "native_streaming": descriptor.native_streaming,
                "local_only": descriptor.local_only,
                "status": health.status,
                "model_loaded": health.model_loaded,
                "queue_depth": health.queue_depth,
                "device": health.device,
                "detail": health.detail,
                "selected": snapshot.selected,
            }
        )
    return {
        "schema_version": "1.0",
        "default_provider": container.providers.tts.kind,
        "items": items,
        "count": len(items),
    }


@router.get("/tts/configurations/aliyun_qwen_realtime")
async def read_aliyun_tts_configuration(request: Request) -> dict[str, object]:
    return _container(request).tts_configurations.get().model_dump(mode="json")


@router.put("/tts/configurations/aliyun_qwen_realtime")
async def update_aliyun_tts_configuration(
    request: Request,
    body: AliyunTtsConfigurationRequest,
) -> dict[str, object]:
    try:
        configuration = AliyunTtsConfiguration(
            **body.model_dump(exclude={"api_key", "clear_api_key"}),
            updated_at=datetime.now(UTC),
        )
        updated = await _container(request).tts_configurations.update(
            configuration,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return updated.model_dump(mode="json")


@router.post("/tts/configurations/aliyun_qwen_realtime/test")
async def test_aliyun_tts_configuration(request: Request) -> dict[str, object]:
    try:
        return await _container(request).providers.tts.probe("aliyun_qwen_realtime")
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail=f"百炼语音测试失败: {error}") from error


@router.get("/tts/configurations/aliyun_cosyvoice_realtime")
async def read_aliyun_cosyvoice_tts_configuration(request: Request) -> dict[str, object]:
    return _container(request).tts_configurations.get_cosyvoice().model_dump(mode="json")


@router.put("/tts/configurations/aliyun_cosyvoice_realtime")
async def update_aliyun_cosyvoice_tts_configuration(
    request: Request,
    body: AliyunCosyVoiceTtsConfigurationRequest,
) -> dict[str, object]:
    try:
        configuration = AliyunCosyVoiceTtsConfiguration(
            **body.model_dump(exclude={"api_key", "clear_api_key"}),
            updated_at=datetime.now(UTC),
        )
        updated = await _container(request).tts_configurations.update(
            configuration,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return updated.model_dump(mode="json")


@router.post("/tts/configurations/aliyun_cosyvoice_realtime/test")
async def test_aliyun_cosyvoice_tts_configuration(request: Request) -> dict[str, object]:
    try:
        return await _container(request).providers.tts.probe("aliyun_cosyvoice_realtime")
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail=f"CosyVoice 测试失败: {error}") from error


@router.put("/sessions/{session_id}/tts/provider")
async def select_tts_provider(
    request: Request,
    session_id: UUID,
    body: TtsProviderSelectionRequest,
) -> dict[str, object]:
    container = _container(request)
    if await container.sessions.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    await container.conversation.cancel(session_id, "tts_provider_changed")
    try:
        selected = await container.providers.tts.select(session_id, body.provider_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="TTS provider not found") from error
    return {
        "schema_version": "1.0",
        "session_id": str(session_id),
        "provider_id": selected,
    }


@router.post("/sessions/{session_id}/webrtc/offer")
async def create_webrtc_offer(
    request: Request,
    session_id: UUID,
    body: WebRtcOfferRequest,
) -> dict[str, str]:
    container = _container(request)
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not container.settings.realtime.enabled:
        raise HTTPException(status_code=503, detail="realtime media is disabled")
    try:
        return await container.voice_media.offer(
            session_id,
            sdp=body.sdp,
            type=body.type,
            pc_id=body.pc_id,
            restart_pc=body.restart_pc,
            activation_mode=body.activation_mode,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/webrtc/offer")
async def patch_webrtc_offer(request: Request, body: WebRtcPatchRequest) -> dict[str, str]:
    await _container(request).voice_media.patch(
        pc_id=body.pc_id,
        candidates=[
            (item.candidate, item.sdp_mid, item.sdp_mline_index) for item in body.candidates
        ],
    )
    return {"status": "accepted"}


@router.delete("/sessions/{session_id}/webrtc")
async def close_webrtc_session(request: Request, session_id: UUID) -> dict[str, object]:
    closed = await _container(request).voice_media.close_session(session_id)
    return {"session_id": str(session_id), "connections_closed": closed}


@router.get("/sessions/{session_id}/events")
async def read_session_events(
    request: Request,
    session_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    events = await _container(request).event_store.read_stream(
        session_id, after_sequence=after_sequence, limit=limit
    )
    return {"items": events, "count": len(events)}


@router.post("/sessions/{session_id}/turns", status_code=status.HTTP_202_ACCEPTED)
async def submit_text_turn(
    request: Request, session_id: UUID, body: SubmitTextRequest
) -> dict[str, object]:
    _container(request).activity.touch(session_id)
    _container(request).resources.touch()
    try:
        accepted = await _container(request).conversation.submit_text(session_id, body.text)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "session_id": str(accepted.session_id),
        "turn_id": str(accepted.turn_id),
        "generation_id": str(accepted.generation_id),
        "state": accepted.state.value,
    }


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_generation(
    request: Request, session_id: UUID, body: InterruptRequest
) -> dict[str, object]:
    interrupted = await _container(request).conversation.cancel(session_id, body.reason)
    return {"session_id": str(session_id), "interrupted": interrupted}


@router.post("/sessions/{session_id}/playback/ack")
async def acknowledge_playback(
    request: Request,
    session_id: UUID,
    body: PlaybackAckCommand,
) -> dict[str, object]:
    if body.session_id != session_id:
        raise HTTPException(status_code=409, detail="command session_id does not match route")
    try:
        result = await _container(request).playback.acknowledge(body)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if (
        result.all_segments_completed
        and result.committed_event_id is not None
        and result.turn_id is not None
    ):
        session = await _container(request).sessions.get_session(session_id)
        if session is not None:
            await _container(request).memory.observe_assistant_spoken(
                session_id,
                result.turn_id,
                result.committed_event_id,
                session.character_id,
                result.spoken_text,
            )
    return {
        "command_id": str(result.command_id),
        "segment_id": str(result.segment_id),
        "state": result.state,
        "played_pts_ms": result.played_pts_ms,
        "completed": result.completed,
        "spoken_text": result.spoken_text,
        "duplicate": result.duplicate,
    }


@router.get("/sessions/{session_id}/generations/{generation_id}/playback")
async def read_playback_status(
    request: Request,
    session_id: UUID,
    generation_id: UUID,
) -> dict[str, object]:
    try:
        return await _container(request).playback.status(session_id, generation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/reset")
async def reset_session_data(
    request: Request, session_id: UUID, body: ResetSessionRequest
) -> dict[str, object]:
    try:
        result = await _container(request).conversation.reset(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "session_id": str(result.session_id),
        "turns_deleted": result.turns_deleted,
        "events_deleted": result.events_deleted,
        "memories_deleted": result.memories_deleted,
        "audio_assets_deleted": result.audio_assets_deleted,
    }


@router.get("/sessions/{session_id}/messages")
async def read_session_messages(
    request: Request,
    session_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    session = await _container(request).sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = await _container(request).conversation.list_messages(session_id, limit)
    return {"items": messages, "count": len(messages)}


@router.get("/audio/{asset_id}.wav", response_class=FileResponse)
async def read_audio_asset(request: Request, asset_id: UUID) -> FileResponse:
    path = _container(request).audio_assets.resolve(asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="audio asset not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{asset_id}.wav")


@router.get("/characters")
async def read_characters(request: Request) -> dict[str, object]:
    profiles = [
        profile.model_dump(mode="json", exclude={"system_prompt"})
        for profile in _container(request).characters.list()
    ]
    return {"items": profiles, "count": len(profiles)}


@router.get("/memory")
async def read_memory(
    request: Request,
    include_tombstoned: bool = Query(default=False),
    namespace: str | None = Query(default=None, max_length=256),
    kind: str | None = Query(default=None, max_length=64),
    sensitivity: str | None = Query(default=None, max_length=32),
) -> dict[str, object]:
    items = await _container(request).memory.list(
        include_tombstoned=include_tombstoned,
        namespace=namespace,
        kind=kind,
        sensitivity=sensitivity,
    )
    serialized: list[dict[str, object]] = []
    for item in items:
        value = item.model_dump(mode="json")
        value["content"] = item.text
        serialized.append(value)
    return {"items": serialized, "count": len(serialized)}


@router.get("/memory/proposals")
async def read_memory_proposals(
    request: Request, status_filter: str | None = Query(default=None, alias="status")
) -> dict[str, object]:
    items = await _container(request).memory.list_proposals(status=status_filter)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
    }


@router.post("/sessions/{session_id}/memory/proposals/{proposal_id}/decision")
async def decide_memory_proposal(
    request: Request,
    session_id: UUID,
    proposal_id: UUID,
    body: MemoryProposalDecisionRequest,
) -> dict[str, object]:
    if await _container(request).sessions.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        proposal = await _container(request).memory.decide_proposal(
            session_id, proposal_id, body.decision
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return proposal.model_dump(mode="json")


@router.get("/memory/{memory_id}/sources")
async def read_memory_sources(request: Request, memory_id: UUID) -> dict[str, object]:
    items = await _container(request).memory.list_sources(memory_id)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
    }


@router.patch("/sessions/{session_id}/memory/{memory_id}")
async def correct_memory(
    request: Request,
    session_id: UUID,
    memory_id: UUID,
    body: MemoryCorrectionRequest,
) -> dict[str, object]:
    try:
        record = await _container(request).memory.correct(session_id, memory_id, body.text)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return record.model_dump(mode="json")


@router.put("/sessions/{session_id}/memory/{memory_id}/pinned")
async def set_memory_pinned(
    request: Request,
    session_id: UUID,
    memory_id: UUID,
    body: MemoryPinnedRequest,
) -> dict[str, object]:
    try:
        record = await _container(request).memory.set_pinned(session_id, memory_id, body.pinned)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return record.model_dump(mode="json")


@router.delete("/sessions/{session_id}/memory/{memory_id}")
async def forget_memory(request: Request, session_id: UUID, memory_id: UUID) -> dict[str, object]:
    changed = await _container(request).memory.forget(session_id, memory_id)
    if not changed:
        raise HTTPException(status_code=404, detail="active memory not found")
    return {"memory_id": str(memory_id), "state": "tombstoned"}


@router.get("/skills")
async def read_runtime_skills(request: Request) -> dict[str, object]:
    definitions = [
        definition.model_dump(mode="json")
        for definition in _container(request).runtime_skills.list()
    ]
    return {"items": definitions, "count": len(definitions)}


@router.get("/skills/{skill_id}/instructions")
async def read_runtime_skill_instructions(request: Request, skill_id: str) -> dict[str, str]:
    try:
        instructions = _container(request).runtime_skills.instructions(skill_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="skill not found") from error
    return {"skill_id": skill_id, "instructions": instructions}


@router.post("/sessions/{session_id}/skill-runs", status_code=status.HTTP_202_ACCEPTED)
async def invoke_runtime_skill(
    request: Request, session_id: UUID, body: SkillInvocation
) -> dict[str, object]:
    if await _container(request).sessions.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        snapshot = await _container(request).runtime_skills.invoke(session_id, body)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return snapshot.model_dump(mode="json")


@router.get("/sessions/{session_id}/skill-runs")
async def read_skill_runs(
    request: Request,
    session_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    items = await _container(request).runtime_skills.list_runs(session_id, limit)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.get("/skill-runs/{skill_run_id}")
async def read_skill_run(request: Request, skill_run_id: UUID) -> dict[str, object]:
    try:
        snapshot = await _container(request).runtime_skills.get_run(skill_run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="skill run not found") from error
    return snapshot.model_dump(mode="json")


@router.post("/skill-runs/{skill_run_id}/cancel")
async def cancel_skill_run(request: Request, skill_run_id: UUID) -> dict[str, object]:
    try:
        snapshot = await _container(request).runtime_skills.cancel(skill_run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="skill run not found") from error
    return snapshot.model_dump(mode="json")


@router.get("/sessions/{session_id}/skill-confirmations")
async def read_skill_confirmations(request: Request, session_id: UUID) -> dict[str, object]:
    items = await _container(request).runtime_skills.pending_confirmations(session_id)
    return {"items": items, "count": len(items)}


@router.post("/skill-confirmations/{request_id}")
async def decide_skill_confirmation(
    request: Request, request_id: UUID, body: SkillConfirmationDecisionRequest
) -> dict[str, object]:
    try:
        snapshot = await _container(request).runtime_skills.decide_confirmation(
            request_id, body.decision
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="confirmation request not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return snapshot.model_dump(mode="json")


@router.get("/plugins")
async def read_plugins(request: Request) -> dict[str, object]:
    items = await _container(request).runtime_skills.list_plugins()
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@router.post("/plugins/install", status_code=status.HTTP_201_CREATED)
async def install_plugin(request: Request, body: InstallPluginRequest) -> dict[str, object]:
    try:
        plugin = await _container(request).runtime_skills.install_plugin(Path(body.source_path))
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return plugin.model_dump(mode="json")


@router.post("/plugins/install-example", status_code=status.HTTP_201_CREATED)
async def install_example_plugin(request: Request, body: ExamplePluginRequest) -> dict[str, object]:
    try:
        plugin = await _container(request).runtime_skills.install_example_plugin(body.example_id)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return plugin.model_dump(mode="json")


@router.put("/plugins/{plugin_id}/enabled")
async def set_plugin_enabled(
    request: Request, plugin_id: str, body: PluginEnabledRequest
) -> dict[str, object]:
    try:
        plugin = await _container(request).runtime_skills.set_plugin_enabled(
            plugin_id, body.enabled
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="plugin not found") from error
    return plugin.model_dump(mode="json")


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin(request: Request, plugin_id: str) -> dict[str, object]:
    try:
        trash_path = await _container(request).runtime_skills.uninstall_plugin(plugin_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="plugin not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"plugin_id": plugin_id, "removed": True, "recoverable_from": str(trash_path)}


@router.post("/sessions/{session_id}/skills/runtime.status")
async def run_runtime_status_skill(request: Request, session_id: UUID) -> dict[str, object]:
    session = await _container(request).sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    result = await _container(request).runtime_skills.run_status(session_id)
    return result.model_dump(mode="json")


@router.websocket("/events")
async def runtime_events(websocket: WebSocket) -> None:
    await websocket.accept()
    container: RuntimeContainer = websocket.app.state.container
    requested_session = websocket.query_params.get("session_id")

    def event_filter(event: dict[str, object]) -> bool:
        return requested_session is None or str(event.get("session_id")) == requested_session

    subscription = container.event_hub.subscribe(event_filter)
    await websocket.send_json(
        {
            "schema_version": "1.0",
            "event_type": "system.runtime_started",
            "payload": {"version": __version__},
        }
    )
    try:
        while True:
            event_task = asyncio.create_task(subscription.receive())
            disconnect_task = asyncio.create_task(websocket.receive())
            completed, pending = await asyncio.wait(
                {event_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(
                *(cast(Awaitable[object], task) for task in pending),
                return_exceptions=True,
            )
            if disconnect_task in completed:
                message = disconnect_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                continue
            await websocket.send_json(event_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        container.event_hub.unsubscribe(subscription)


@router.websocket("/audio/stream")
async def runtime_audio_stream(websocket: WebSocket) -> None:
    container: RuntimeContainer = websocket.app.state.container
    requested_session = websocket.query_params.get("session_id")
    if requested_session is None:
        await websocket.close(code=1008, reason="session_id is required")
        return
    try:
        session_id = UUID(requested_session)
    except ValueError:
        await websocket.close(code=1008, reason="invalid session_id")
        return
    if await container.sessions.get_session(session_id) is None:
        await websocket.close(code=1008, reason="session not found")
        return
    await websocket.accept()
    subscription = container.audio_streams.subscribe(session_id)
    try:
        while True:
            packet_task = asyncio.create_task(subscription.receive())
            disconnect_task = asyncio.create_task(websocket.receive())
            completed, pending = await asyncio.wait(
                {packet_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(
                *(cast(Awaitable[object], task) for task in pending),
                return_exceptions=True,
            )
            if disconnect_task in completed:
                message = disconnect_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                continue
            packet = packet_task.result()
            await websocket.send_json(
                {
                    "type": "chatwaifu.tts_stream",
                    "schema_version": "1.0",
                    "phase": packet.phase,
                    "session_id": str(packet.session_id),
                    "turn_id": str(packet.turn_id),
                    "generation_id": str(packet.generation_id),
                    "stream_id": str(packet.stream_id),
                    "segment_id": str(packet.segment_id),
                    "segment_index": packet.segment_index,
                    "text": packet.text,
                    "sequence": packet.sequence,
                    "sample_rate": packet.sample_rate,
                    "channels": packet.channels,
                    "native_streaming": packet.native_streaming,
                    "pcm16_base64": (
                        base64.b64encode(packet.pcm16).decode("ascii") if packet.pcm16 else ""
                    ),
                    "duration_ms": packet.duration_ms,
                    "provider_id": packet.provider_id,
                    "model": packet.model,
                    "reason": packet.reason,
                }
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        container.audio_streams.unsubscribe(subscription)
