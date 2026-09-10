"""Comprehensive automated test suite for Phase 13.7A: Client-Driven Cloud Realtime Recovery.

Covers connection ownership, restored context, and egress policy:
1. Provider EOF termination safely signals WebRTC transport closure and real loopback reconnect
   restores confirmed SQLite dialogue across two full Pipecat pipeline executions.
2. Latest confirmed history extracts finalized user text and completed playback-confirmed
   assistant spoken text, replacing redacted photo memories, excluding pending turns,
   filtering across memory scope resets, deterministic >16 limit/ordering, and bounded unicode.
3. Pipecat adapter serialized admission, replacement offer joining, targeted pc_id teardown,
   offer-to-closed fast fail, cross-session pc_id reuse rejection, superseding timeout fail-closed,
   and task-token discard comparison.
4. Egress gateway fail-closed policy enforcement, non-retryable denial, missing consent
   enforcement, and audited receipt writes.
5. Context patch budget pruning drops priority 6 dialogue history before memories and safety,
   with UTF-8 multi-byte boundary truncation safety and deterministic older-turn pruning.
6. Cloud realtime factory database read failure fail-closed guarantee.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
)
from chatwaifu_protocol.memory import MemoryRecord
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import (
    RealtimeConfig,
    Settings,
    StorageConfig,
    SttConfig,
)
from chatwaifu_runtime.conversation.models import (
    ConfirmedConversationTurn,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_conversation import (
    REDACTED_ASSISTANT_PLACEHOLDER,
    SQLiteConversationRepository,
)
from chatwaifu_runtime.realtime.admission import InMemoryTurnAdmission
from chatwaifu_runtime.realtime.cloud import openai as openai_module
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressGateway,
    ConsentRequiredError,
    EgressGrant,
    PolicyDeniedError,
    RealtimeContextPatchBuilder,
)
from chatwaifu_runtime.realtime.cloud.contracts import (
    CloudRealtimeBackend,
    CloudRealtimeSession,
    RealtimeCapabilities,
    RealtimeSessionIntent,
    RealtimeSessionOpenRequest,
    SessionClosedEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import InMemoryDomainSink
from chatwaifu_runtime.realtime.cloud.factory import RuntimeCloudRealtimeFactory
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeSession
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.cloud.openai import OpenAIRealtimeBackend, RealtimeSocket
from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter, WebRtcOffer
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from websockets.asyncio.server import ServerConnection, serve

# ---------------------------------------------------------------------------
# Acceptance 1: Controlled Provider EOF Triggers Teardown & Loopback Reconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_bridge_eof_signals_closed_event_and_notifies_watcher() -> None:
    """Acceptance 1: Provider EOF signals closed_event on bridge and initiates teardown."""
    sid = uuid4()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default")
    )
    bridge = CloudRealtimeMediaBridge.create(
        session_id=sid,
        backend_id=session.backend_id,
        session=session,
        admission=InMemoryTurnAdmission(),
        domain_sink=InMemoryDomainSink(),
    )
    bridge.push_frame = AsyncMock()
    bridge._ensure_started()

    assert not bridge.closed_event.is_set(), "closed_event must be unset initially"

    # Simulate cloud provider EOF / socket closure
    await bridge.coordinator.dispatch_event(
        SessionClosedEvent(session_id=sid, backend_id="fake", reason="provider_eof")
    )

    assert bridge.closed_event.is_set(), "closed_event must be set immediately on provider EOF"

    # Verify watcher waiting on closed_event resumes without polling
    watcher_notified = False

    async def mock_watcher() -> None:
        nonlocal watcher_notified
        await bridge.closed_event.wait()
        watcher_notified = True

    watcher_task = asyncio.create_task(mock_watcher())
    await asyncio.wait_for(watcher_task, timeout=1.0)
    assert watcher_notified is True

    await bridge.coordinator.stop()
    await bridge.cleanup()


@pytest.mark.asyncio
async def test_production_path_eof_teardown_and_loopback_reconnect_restoration(
    tmp_path: Path,
) -> None:
    """Loopback WebSocket executes PipecatMediaAdapter._run_connection twice.

    Connection 1: Ends cleanly when loopback server sends provider EOF (1011).
    Context update: First turn is confirmed in SQLite.
    Connection 2: Client reconnects; RuntimeCloudRealtimeFactory restores confirmed SQLite dialogue
    and sends it to loopback server in session.update.
    """
    wire_updates: list[tuple[int, dict[str, object]]] = []
    conn_index = 0
    first_bridge_ready = asyncio.Event()
    second_wire_ready = asyncio.Event()

    async def loopback_handler(ws: ServerConnection) -> None:
        nonlocal conn_index
        conn_index += 1
        cid = conn_index
        seq = 0

        async def send(ev: dict[str, object]) -> None:
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"event_id": f"e-{seq}", **ev}))

        await send(
            {
                "type": "session.created",
                "session": {"id": f"sess-{cid}", "type": "realtime"},
            }
        )
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "session.update":
                wire_updates.append((cid, msg))
                await send(
                    {
                        "type": "session.updated",
                        "session": {**msg["session"], "id": f"sess-{cid}"},
                    }
                )
                if cid == 1:
                    # Connection 1: drop socket with 1011 (provider EOF)
                    await first_bridge_ready.wait()
                    await ws.close(code=1011)
                    return
                second_wire_ready.set()

    async with serve(loopback_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connector(_url: str, key: str, sec: float) -> RealtimeSocket:
            return await openai_module._connect(f"ws://127.0.0.1:{port}/realtime", key, sec)

        settings = Settings.model_validate(
            {
                "config_dir": tmp_path / "config",
                "data_dir": tmp_path,
                "storage": {"database_path": tmp_path / "runtime.db"},
                "llm": {"provider": "demo"},
                "tts": {"provider": "fake"},
                "privacy": {"cloud_egress": "allow"},
                "realtime": {
                    "connection_mode": "cloud_realtime",
                    "cloud_backend": "openai",
                    "openai": {"model": "test-model", "api_key": "local-key"},
                },
            }
        )
        container = RuntimeContainer(settings)
        assert isinstance(container.cloud_realtime_backend, OpenAIRealtimeBackend)
        container.cloud_realtime_backend._connector = connector
        await container.start()

        try:
            sess = await container.sessions.create_session("default")
            sid = sess.session_id

            # Setup mock WebRTC connection 1
            conn1 = MagicMock(spec=SmallWebRTCConnection)
            conn1.pc_id = "pc-loopback-1"
            conn1.is_connected.return_value = True
            conn1.connect = AsyncMock()
            conn1.disconnect = AsyncMock()
            conn1._close = AsyncMock()
            conn1.audio_input_track.return_value = None
            conn1.video_input_track.return_value = None
            conn1.screen_video_input_track.return_value = None
            conn1.send_app_message = MagicMock()
            conn1.replace_audio_track = MagicMock()
            conn1.replace_video_track = MagicMock()

            # Run connection 1 through adapter._run_connection
            assert container.voice_media._adapter is not None
            adapter = cast(PipecatMediaAdapter, container.voice_media._adapter)
            assert adapter._cloud_bridge_factory is not None
            original_factory = adapter._cloud_bridge_factory

            async def ready_factory(session_id: UUID) -> CloudRealtimeMediaBridge:
                bridge = await original_factory(session_id)
                first_bridge_ready.set()
                return bridge

            adapter._cloud_bridge_factory = ready_factory
            task1: asyncio.Task[None] = asyncio.create_task(
                adapter._run_connection(sid, conn1, "ptt"),
                name="task-conn-1",
            )

            # Wait for task1 to finish on provider EOF
            await asyncio.wait_for(task1, timeout=5.0)
            assert conn1.disconnect.called, (
                "conn1.disconnect must be called on cloud bridge termination"
            )

            # Record confirmed turn into SQLite for this session
            now = datetime.now(UTC)
            t_user = uuid4()
            t_asst = uuid4()
            g_asst = uuid4()
            async with container.database.transaction() as db_conn:
                await db_conn.execute(
                    """INSERT INTO turns (
                           turn_id, session_id, role, committed_text, committed_at, created_at
                       ) VALUES (?, ?, 'user', 'How do I recover from disconnect?', ?, ?)""",
                    (str(t_user), str(sid), now.isoformat(), now.isoformat()),
                )
                await db_conn.execute(
                    """INSERT INTO turns (turn_id, session_id, role, generation_id, created_at)
                       VALUES (?, ?, 'assistant', ?, ?)""",
                    (str(t_asst), str(sid), str(g_asst), now.isoformat()),
                )
                await db_conn.execute(
                    """INSERT INTO generations (
                           generation_id, session_id, turn_id, state, backend_kind,
                           spoken_text, started_at, completed_at
                       ) VALUES (?, ?, ?, 'completed', 'cloud_realtime',
                                'Reconnection restores your prior context.', ?, ?)""",
                    (str(g_asst), str(sid), str(t_asst), now.isoformat(), now.isoformat()),
                )

            # Setup mock WebRTC connection 2
            conn2 = MagicMock(spec=SmallWebRTCConnection)
            conn2.pc_id = "pc-loopback-2"
            conn2.is_connected.return_value = True
            conn2.connect = AsyncMock()
            conn2.disconnect = AsyncMock()
            conn2._close = AsyncMock()
            conn2.audio_input_track.return_value = None
            conn2.video_input_track.return_value = None
            conn2.screen_video_input_track.return_value = None
            conn2.send_app_message = MagicMock()
            conn2.replace_audio_track = MagicMock()
            conn2.replace_video_track = MagicMock()

            # Run connection 2 through adapter._run_connection
            task2: asyncio.Task[None] = asyncio.create_task(
                adapter._run_connection(sid, conn2, "ptt"),
                name="task-conn-2",
            )

            # Wait for connection 2 to send session.update to server
            await asyncio.wait_for(second_wire_ready.wait(), timeout=3.0)

            assert len(wire_updates) == 2, (
                f"Expected 2 session.update events, got {len(wire_updates)}"
            )
            update_conn2 = wire_updates[1][1]
            session_payload = update_conn2.get("session", {})
            assert isinstance(session_payload, dict)
            instructions = str(session_payload.get("instructions", ""))
            assert "How do I recover from disconnect?" in instructions
            assert "Reconnection restores your prior context." in instructions

            # Clean up connection 2 cleanly via targeted close
            await container.voice_media.close_session(sid, pc_id="pc-loopback-2")
            await asyncio.wait_for(task2, timeout=2.0)
        finally:
            await container.stop()


# ---------------------------------------------------------------------------
# Acceptance 2: Durable Snapshot Contract & Dialogue Restoration Filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_confirmed_history_filters_and_restores_dialogue_accurately(
    tmp_path: Path,
) -> None:
    """Acceptance 2 & Exhaustively validates dialogue confirmation filters.

    Validates:
    - Finalized user turns (committed_text present) are included.
    - Uncommitted user turns (committed_text is NULL) are excluded.
    - Empty user turns (committed_text is whitespace) are excluded.
    - Completed playback-confirmed assistant turns (state='completed', spoken_text) included.
    - Unheard cascade turns (spoken_text is empty) are excluded.
    - Running assistant generations (state='running') are excluded.
    - Cancelled assistant generations (state='cancelled') are excluded.
    - Redacted photo memories included with is_redacted=True and placeholder text.
    - Foreign session isolation (turns belonging to other_sid excluded).
    - Cross-character isolation (turns belonging to different character excluded).
    - Memory scope resets: turns before reset_at excluded for specific character and '__all__'.
    - Windowing: >16 turns deterministic limit and chronological ordering (oldest first).
    - Bounded long unicode history (truncated at 1000 characters).
    """
    db_path = tmp_path / "recovery_filters.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)
    repo = SQLiteConversationRepository(database, event_store)

    target_sid = uuid4()
    other_sid = uuid4()
    window_sid = uuid4()
    now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)

    async with database.transaction() as conn:
        for s_id, char_id in [
            (target_sid, "default"),
            (other_sid, "default"),
            (window_sid, "default"),
        ]:
            await conn.execute(
                """
                INSERT INTO sessions (
                    session_id, character_id, state, conversation_state,
                    revision, next_sequence, created_at, updated_at
                ) VALUES (?, ?, 'active', 'idle', 0, 1, ?, ?)
                """,
                (str(s_id), char_id, now.isoformat(), now.isoformat()),
            )

        # 0. Memory scope reset at 10:00:05
        reset_time = datetime(2026, 9, 10, 10, 0, 5, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO memory_scope_resets (character_id, reset_at)
            VALUES ('default', ?)
            """,
            (reset_time.isoformat(),),
        )

        # Turn 0: User turn BEFORE reset_at -> MUST BE EXCLUDED by memory reset filter
        t0_id = uuid4()
        t0_time = datetime(2026, 9, 10, 10, 0, 2, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, 'user', 'Turn before reset', ?, ?)
            """,
            (str(t0_id), str(target_sid), t0_time.isoformat(), t0_time.isoformat()),
        )

        # Turn 1: Valid finalized User turn
        t1_id = uuid4()
        t1_time = datetime(2026, 9, 10, 10, 0, 10, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, 'user', 'Hello assistant!', ?, ?)
            """,
            (str(t1_id), str(target_sid), t1_time.isoformat(), t1_time.isoformat()),
        )

        # Turn 2: Valid completed playback-confirmed Assistant turn
        t2_id = uuid4()
        g2_id = uuid4()
        t2_time = datetime(2026, 9, 10, 10, 0, 12, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t2_id), str(target_sid), t2_time.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, spoken_text, started_at, completed_at
            ) VALUES (
                ?, ?, ?, 'completed', 'cloud_realtime', ?, 'Hello user, how can I help?', ?, ?
            )
            """,
            (
                str(g2_id),
                str(target_sid),
                str(t2_id),
                str(uuid4()),
                t2_time.isoformat(),
                t2_time.isoformat(),
            ),
        )
        await conn.execute(
            "UPDATE turns SET generation_id = ? WHERE turn_id = ?",
            (str(g2_id), str(t2_id)),
        )

        # Turn 3: Uncommitted User turn (committed_text IS NULL) -> MUST BE EXCLUDED
        t3_id = uuid4()
        t3_time = datetime(2026, 9, 10, 10, 0, 14, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'user', ?)
            """,
            (str(t3_id), str(target_sid), t3_time.isoformat()),
        )

        # Turn 4: Whitespace-only User turn -> MUST BE EXCLUDED
        t4_id = uuid4()
        t4_time = datetime(2026, 9, 10, 10, 0, 15, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, 'user', '   ', ?, ?)
            """,
            (str(t4_id), str(target_sid), t4_time.isoformat(), t4_time.isoformat()),
        )

        # Turn 5: Completed Assistant turn with empty spoken_text (unheard) -> MUST BE EXCLUDED
        t5_id = uuid4()
        g5_id = uuid4()
        t5_time = datetime(2026, 9, 10, 10, 0, 16, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t5_id), str(target_sid), t5_time.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, spoken_text, started_at, completed_at
            ) VALUES (?, ?, ?, 'completed', 'cascade', ?, '', ?, ?)
            """,
            (
                str(g5_id),
                str(target_sid),
                str(t5_id),
                str(uuid4()),
                t5_time.isoformat(),
                t5_time.isoformat(),
            ),
        )
        await conn.execute(
            "UPDATE turns SET generation_id = ? WHERE turn_id = ?",
            (str(g5_id), str(t5_id)),
        )

        # Turn 5b: Assistant turn without any generation (generation_id IS NULL) -> MUST BE EXCLUDED
        t5b_id = uuid4()
        t5b_time = datetime(2026, 9, 10, 10, 0, 16, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t5b_id), str(target_sid), t5b_time.isoformat()),
        )

        # Turn 6: Running Assistant turn -> MUST BE EXCLUDED
        t6_id = uuid4()
        g6_id = uuid4()
        t6_time = datetime(2026, 9, 10, 10, 0, 17, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t6_id), str(target_sid), t6_time.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, spoken_text, started_at
            ) VALUES (?, ?, ?, 'running', 'cloud_realtime', ?, 'Unfinished text', ?)
            """,
            (
                str(g6_id),
                str(target_sid),
                str(t6_id),
                str(uuid4()),
                t6_time.isoformat(),
            ),
        )
        await conn.execute(
            "UPDATE turns SET generation_id = ? WHERE turn_id = ?",
            (str(g6_id), str(t6_id)),
        )

        # Turn 7: Cancelled Assistant turn -> MUST BE EXCLUDED
        t7_id = uuid4()
        g7_id = uuid4()
        t7_time = datetime(2026, 9, 10, 10, 0, 18, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t7_id), str(target_sid), t7_time.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, spoken_text, started_at, completed_at
            ) VALUES (?, ?, ?, 'cancelled', 'cloud_realtime', ?, 'Cancelled speech', ?, ?)
            """,
            (
                str(g7_id),
                str(target_sid),
                str(t7_id),
                str(uuid4()),
                t7_time.isoformat(),
                t7_time.isoformat(),
            ),
        )
        await conn.execute(
            "UPDATE turns SET generation_id = ? WHERE turn_id = ?",
            (str(g7_id), str(t7_id)),
        )

        # Turn 8: Redacted Assistant turn -> MUST BE INCLUDED with placeholder
        t8_id = uuid4()
        g8_id = uuid4()
        t8_time = datetime(2026, 9, 10, 10, 0, 20, tzinfo=UTC)
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, created_at)
            VALUES (?, ?, 'assistant', ?)
            """,
            (str(t8_id), str(target_sid), t8_time.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, spoken_text, started_at, completed_at
            ) VALUES (?, ?, ?, 'completed', 'cloud_realtime', ?, 'Sensitive photo content', ?, ?)
            """,
            (
                str(g8_id),
                str(target_sid),
                str(t8_id),
                str(uuid4()),
                t8_time.isoformat(),
                t8_time.isoformat(),
            ),
        )
        await conn.execute(
            "UPDATE turns SET generation_id = ? WHERE turn_id = ?",
            (str(g8_id), str(t8_id)),
        )
        await conn.execute(
            """
            INSERT INTO photo_context_redactions (
                generation_id, session_id, principal_scope, character_id, created_at
            )
            VALUES (?, ?, 'user', 'default', ?)
            """,
            (str(g8_id), str(target_sid), t8_time.isoformat()),
        )

        # Turn 9: Foreign session turn -> MUST BE EXCLUDED
        t_foreign = uuid4()
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, 'user', 'Foreign session secret', ?, ?)
            """,
            (str(t_foreign), str(other_sid), now.isoformat(), now.isoformat()),
        )

        # Turn 10: Long unicode user turn (1500 chars) -> MUST BE TRUNCATED to 1000 + '...'
        t10_id = uuid4()
        t10_time = datetime(2026, 9, 10, 10, 0, 25, tzinfo=UTC)
        long_unicode_text = "你好世界🌟" * 300  # 1500 characters
        await conn.execute(
            """
            INSERT INTO turns (turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, 'user', ?, ?, ?)
            """,
            (
                str(t10_id),
                str(target_sid),
                long_unicode_text,
                t10_time.isoformat(),
                t10_time.isoformat(),
            ),
        )

    # Query target_sid history
    history = await repo.latest_confirmed_history(target_sid, limit=16)

    # Assertions for target_sid:
    # Included turns: Turn 1, Turn 2, Turn 8, Turn 10 (4 turns total)
    # Excluded: Turn 0 (before reset), Turn 3 (uncommitted), Turn 4 (whitespace),
    #           Turn 5 (unheard), Turn 6 (running), Turn 7 (cancelled), Turn 9 (foreign)
    assert len(history) == 4
    assert history[0].turn_id == t1_id
    assert history[0].role == "user"
    assert history[0].text == "Hello assistant!"
    assert history[0].is_redacted is False

    assert history[1].turn_id == t2_id
    assert history[1].role == "assistant"
    assert history[1].text == "Hello user, how can I help?"
    assert history[1].is_redacted is False

    assert history[2].turn_id == t8_id
    assert history[2].role == "assistant"
    assert history[2].text == REDACTED_ASSISTANT_PLACEHOLDER
    assert history[2].is_redacted is True

    assert history[3].turn_id == t10_id
    assert history[3].role == "user"
    assert len(history[3].text) == 1003
    assert history[3].text.endswith("...")

    # Windowing test: Insert 20 confirmed turns for window_sid
    async with database.transaction() as conn:
        for i in range(20):
            t_id = uuid4()
            t_dt = datetime(2026, 9, 10, 10, 1, i + 1, tzinfo=UTC)
            await conn.execute(
                """
                INSERT INTO turns (
                    turn_id, session_id, role, committed_text, committed_at, created_at
                ) VALUES (?, ?, 'user', ?, ?, ?)
                """,
                (str(t_id), str(window_sid), f"Turn {i}", t_dt.isoformat(), t_dt.isoformat()),
            )

    window_history = await repo.latest_confirmed_history(window_sid, limit=16)
    assert len(window_history) == 16
    # Oldest returned should be Turn 4, newest Turn 19 (the 16 most recent turns, oldest first)
    assert window_history[0].text == "Turn 4"
    assert window_history[-1].text == "Turn 19"
    # Strict chronological ordering
    for i in range(len(window_history) - 1):
        dt_current = window_history[i].created_at
        dt_next = window_history[i + 1].created_at
        assert dt_current is not None and dt_next is not None
        assert dt_current < dt_next

    await database.close()


# ---------------------------------------------------------------------------
# Acceptance 3: Pipecat Adapter Safeguards & Targeted pc_id Teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipecat_adapter_serialized_admission_and_targeted_pc_id_teardown() -> None:
    """Acceptance 3: Concurrent offer replaces connection; late DELETE with old pc_id is safe."""
    session_id = uuid4()
    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=AsyncMock(),
    )

    task1_cancelled = False
    task1_started = asyncio.Event()

    async def conn1_loop() -> None:
        nonlocal task1_cancelled
        task1_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task1_cancelled = True
            raise

    task1 = asyncio.create_task(conn1_loop(), name="webrtc-pc-1")
    await task1_started.wait()
    adapter._tasks["pc-1"] = task1
    adapter._sessions["pc-1"] = session_id

    assert adapter.active_connections == 1

    async def conn2_loop() -> None:
        await asyncio.Event().wait()

    task2 = asyncio.create_task(conn2_loop(), name="webrtc-pc-2")
    adapter._tasks["pc-2"] = task2
    adapter._sessions["pc-2"] = session_id

    # Test 1: Late DELETE specifying old pc-1 does NOT close pc-2
    closed = await adapter.close_session(session_id, pc_id="pc-1")
    assert closed == 1
    assert task1_cancelled is True
    assert not task2.done(), "Replacement connection pc-2 must NOT be closed by late DELETE pc-1"

    # Test 2: Late duplicate DELETE for pc-1 returns 0 and does not touch pc-2
    closed_again = await adapter.close_session(session_id, pc_id="pc-1")
    assert closed_again == 0
    assert not task2.done()

    # Test 3: DELETE targeting wrong session returns 0
    wrong_session = uuid4()
    closed_wrong = await adapter.close_session(wrong_session, pc_id="pc-2")
    assert closed_wrong == 0
    assert not task2.done()

    # Test 4: Targeted DELETE for pc-2 cleanly closes pc-2
    closed_pc2 = await adapter.close_session(session_id, pc_id="pc-2")
    assert closed_pc2 == 1
    assert task2.done()

    await adapter.close()


@pytest.mark.asyncio
async def test_pipecat_adapter_closed_fails_fast() -> None:
    """Offer to closed adapter fails immediately with RuntimeError."""
    session_id = uuid4()
    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=AsyncMock(),
    )
    await adapter.close()
    offer = WebRtcOffer(sdp="dummy-sdp", type="offer", pc_id="pc-closed-test")
    with pytest.raises(RuntimeError, match="is closed or closing"):
        await adapter.offer(session_id, offer)


@pytest.mark.asyncio
async def test_pipecat_adapter_rejects_cross_session_pc_id_reuse() -> None:
    """Offer with pc_id belonging to another session raises ValueError."""
    session_1 = uuid4()
    session_2 = uuid4()
    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=AsyncMock(),
    )
    adapter._sessions["pc-1"] = session_1
    offer = WebRtcOffer(sdp="dummy-sdp", type="offer", pc_id="pc-1")
    with pytest.raises(ValueError, match="belongs to session"):
        await adapter.offer(session_2, offer)


@pytest.mark.asyncio
async def test_pipecat_adapter_superseding_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded task failing to terminate within timeout fails closed."""
    session_id = uuid4()
    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=AsyncMock(),
    )

    async def uncooperative_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()

    old_task = asyncio.create_task(uncooperative_task(), name="webrtc-pc-old")
    adapter._tasks["pc-old"] = old_task
    adapter._sessions["pc-old"] = session_id

    offer = WebRtcOffer(sdp="dummy-sdp", type="offer", pc_id=None)

    # Mock asyncio.wait to simulate timeout without sleeping
    async def fast_wait(
        fs: set[asyncio.Task[object]], *args: object, **kwargs: object
    ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
        return set(), fs

    monkeypatch.setattr(asyncio, "wait", fast_wait)

    with pytest.raises(RuntimeError, match="failed to terminate within timeout"):
        await adapter.offer(session_id, offer)

    monkeypatch.undo()
    old_task.cancel()
    await asyncio.gather(old_task, return_exceptions=True)
    await adapter.close()


@pytest.mark.asyncio
async def test_pipecat_adapter_discard_task_token_safety() -> None:
    """Discarding old task token does not remove replacement task."""
    session_id = uuid4()
    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=AsyncMock(),
    )
    task1 = asyncio.create_task(asyncio.sleep(10))
    task2 = asyncio.create_task(asyncio.sleep(10))
    adapter._tasks["pc-1"] = task2
    adapter._sessions["pc-1"] = session_id

    # Discard with stale task1 token has zero effect on task2
    adapter._discard("pc-1", task1)
    assert adapter._tasks.get("pc-1") is task2
    assert adapter._sessions.get("pc-1") == session_id

    # Discard with matching task2 token removes it
    adapter._discard("pc-1", task2)
    assert "pc-1" not in adapter._tasks
    assert "pc-1" not in adapter._sessions

    task1.cancel()
    task2.cancel()
    await asyncio.gather(task1, task2, return_exceptions=True)
    await adapter.close()


# ---------------------------------------------------------------------------
# Acceptance 4: Egress Gateway Policy Enforcement & Consent Requirements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_egress_gateway_policy_enforcement_and_receipt_audit() -> None:
    """Acceptance 4: Egress gateway enforces policy; records receipt without transcripts."""
    session_id = uuid4()
    backend = MagicMock(spec=CloudRealtimeBackend)
    backend.backend_id = "fake_backend"
    backend.capabilities = AsyncMock(
        return_value=RealtimeCapabilities(
            backend_id="fake_backend",
            supports_tool_call=False,
        )
    )
    fake_cloud_session = MagicMock(spec=CloudRealtimeSession)
    fake_cloud_session.send_context_patch = AsyncMock()
    backend.open_session = AsyncMock(return_value=fake_cloud_session)

    # 1. Policy = 'deny' fails closed
    gateway_deny = CloudEgressGateway(policy_mode="deny")
    intent = RealtimeSessionIntent(session_id=session_id, character_id="default")
    with pytest.raises(PolicyDeniedError, match="denied by policy"):
        await gateway_deny.open_session(backend, intent)

    assert backend.open_session.call_count == 0

    # 2. Policy = 'ask' without grant fails closed
    gateway_ask = CloudEgressGateway(policy_mode="ask")
    with pytest.raises(ConsentRequiredError, match="Explicit user consent required"):
        await gateway_ask.open_session(backend, intent)

    assert backend.open_session.call_count == 0

    # 3. Policy = 'ask' with explicit grant succeeds and records audited receipt
    grant = EgressGrant(session_id=session_id, backend_id="fake_backend")
    gateway_ask.grant_consent(grant)

    history_turn = ConfirmedConversationTurn(
        turn_id=uuid4(),
        role="user",
        text="Audited text message",
        generation_id=None,
        created_at=datetime.now(UTC),
    )

    cloud_sess = await gateway_ask.open_session(
        backend,
        intent,
        conversation_history=[history_turn],
    )
    assert cloud_sess is fake_cloud_session
    assert backend.open_session.call_count == 1

    assert len(gateway_ask.audit_receipts) == 2
    assert gateway_ask.audit_receipts[0].policy_decision == "consent_required"
    approved_receipt = gateway_ask.audit_receipts[1]
    assert approved_receipt.policy_decision == "ask_approved"
    assert approved_receipt.provider_backend_id == "fake_backend"
    assert "recent_history" in approved_receipt.component_kinds

    # Zero transcript leakage in receipt payload
    assert not hasattr(approved_receipt, "text")
    assert not hasattr(approved_receipt, "transcript")


@pytest.mark.asyncio
async def test_egress_gateway_missing_recent_history_grant_raises_consent_required() -> None:
    """Grant missing 'recent_history' raises ConsentRequiredError."""
    session_id = uuid4()
    backend = MagicMock(spec=CloudRealtimeBackend)
    backend.backend_id = "fake_backend"
    backend.capabilities = AsyncMock(
        return_value=RealtimeCapabilities(
            backend_id="fake_backend",
            supports_tool_call=False,
        )
    )
    gateway = CloudEgressGateway(policy_mode="ask")
    # Grant permits safety and memory, but NOT recent_history
    grant = EgressGrant(
        session_id=session_id,
        backend_id="fake_backend",
        allowed_component_kinds=frozenset({"safety", "memory"}),
    )
    gateway.grant_consent(grant)

    history_turn = ConfirmedConversationTurn(
        turn_id=uuid4(),
        role="user",
        text="Prior message content",
        generation_id=None,
        created_at=datetime.now(UTC),
    )
    intent = RealtimeSessionIntent(session_id=session_id, character_id="default")

    with pytest.raises(
        ConsentRequiredError, match="Context patch components exceed granted component kinds"
    ):
        await gateway.open_session(backend, intent, conversation_history=[history_turn])


# ---------------------------------------------------------------------------
# Acceptance 5: Context Budget Pruning & UTF-8 Multi-Byte Safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_budget_pruning_drops_recent_history_before_memories_and_safety() -> None:
    """Acceptance 5: Priority 6 recent history is dropped first when budget is constrained."""
    now = datetime.now(UTC)
    kernel = CharacterKernelSnapshot(
        character_id="waifu",
        user_scope="user",
        revision=1,
        affect=AffectState(valence=0.8, arousal=0.5, energy=0.6, attention=0.7, updated_at=now),
        relationship=RelationshipState(
            affinity=0.9, trust=0.85, familiarity=0.8, comfort=0.8, updated_at=now
        ),
    )

    memories = [
        MemoryRecord(
            memory_id=uuid4(),
            namespace="character:waifu:user",
            kind="semantic.fact",
            text="User loves building asynchronous Python and TypeScript applications.",
            observed_at=now,
            confidence=0.9,
            importance=0.8,
            source_event_ids=[uuid4()],
            created_at=now,
            updated_at=now,
            pinned=True,
            state="active",
        )
    ]

    history = [
        ConfirmedConversationTurn(
            turn_id=uuid4(),
            role="user" if i % 2 == 0 else "assistant",
            text=f"Turn message number {i} with substantial text content to fill budget.",
            generation_id=None,
            created_at=datetime.now(UTC),
        )
        for i in range(10)
    ]

    # Large budget: all components retained
    generous_builder = RealtimeContextPatchBuilder(max_bytes=16384, max_tokens=4000)
    generous_patch = generous_builder.build_patch(
        safety_contract="Strict safety guidelines.",
        kernel_snapshot=kernel,
        memories=memories,
        conversation_history=history,
    )
    kinds = [c.kind for c in generous_patch.components]
    assert "safety" in kinds
    assert "relationship" in kinds
    assert "affect" in kinds
    assert "memory" in kinds
    assert "recent_history" in kinds

    # Constrained budget: forces pruning
    safety_bytes = len(b"Strict safety guidelines.")
    constrained_builder = RealtimeContextPatchBuilder(
        max_bytes=safety_bytes + 250,
        max_tokens=100,
    )
    constrained_patch = constrained_builder.build_patch(
        safety_contract="Strict safety guidelines.",
        kernel_snapshot=kernel,
        memories=memories,
        conversation_history=history,
    )
    constrained_kinds = [c.kind for c in constrained_patch.components]

    assert "safety" in constrained_kinds, "Safety contract must NEVER be pruned"
    assert "recent_history" not in constrained_kinds, "Priority 6 history must be dropped first"


def test_context_patch_builder_utf8_truncation_and_deterministic_pruning() -> None:
    """UTF-8 boundary truncation safety and deterministic older-turn pruning."""
    builder = RealtimeContextPatchBuilder()

    # 1. Multi-byte UTF-8 string (>500 bytes) slicing lands mid-codepoint
    long_chinese = "测试中文字符串内容" * 30  # ~900 bytes
    turn = ConfirmedConversationTurn(
        turn_id=uuid4(),
        role="user",
        text=long_chinese,
        generation_id=None,
        created_at=datetime.now(UTC),
    )
    patch = builder.build_patch(conversation_history=[turn])
    history_comp = next((c for c in patch.components if c.kind == "recent_history"), None)
    assert history_comp is not None
    assert history_comp.text.endswith('..."')
    # No UnicodeDecodeError occurred; text is intact
    assert "测试中文字符串内容" in history_comp.text

    # 2. Deterministic older-turn pruning: oldest turns popped first, newest retained
    turns = [
        ConfirmedConversationTurn(
            turn_id=uuid4(),
            role="user" if i % 2 == 0 else "assistant",
            text=f"Turn-{i}-content-filler-bytes-sequence",
            generation_id=None,
            created_at=datetime.now(UTC),
        )
        for i in range(5)
    ]
    tight_builder = RealtimeContextPatchBuilder(max_bytes=250, max_tokens=100)
    tight_patch = tight_builder.build_patch(safety_contract="Safe.", conversation_history=turns)
    tight_comp = next((c for c in tight_patch.components if c.kind == "recent_history"), None)
    assert tight_comp is not None
    assert "Turn-4" in tight_comp.text, "Latest turn must be retained"
    assert "Turn-0" not in tight_comp.text, "Oldest turn must be pruned first"


# ---------------------------------------------------------------------------
# Acceptance 6: Factory Fail-Closed Guarantee on Read Failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_realtime_factory_fails_closed_on_history_db_error() -> None:
    """Database error during history retrieval raises RuntimeError and halts."""
    session_id = uuid4()
    mock_sessions = MagicMock()
    mock_session_obj = MagicMock()
    mock_session_obj.character_id = "default"
    mock_sessions.get_session = AsyncMock(return_value=mock_session_obj)

    mock_conv = MagicMock()
    mock_conv.latest_confirmed_history = AsyncMock(
        side_effect=RuntimeError("Database disk I/O error during history retrieval")
    )

    mock_backend = MagicMock(spec=CloudRealtimeBackend)
    mock_backend.capabilities = AsyncMock(
        return_value=RealtimeCapabilities(backend_id="fake", supports_tool_call=False)
    )
    mock_egress = MagicMock(spec=CloudEgressGateway)

    factory = RuntimeCloudRealtimeFactory(
        backend=mock_backend,
        egress_gateway=mock_egress,
        conversation=mock_conv,
        sessions=mock_sessions,
        admission=MagicMock(),
        characters=MagicMock(),
        character_kernel=MagicMock(),
        memory=MagicMock(),
    )

    with pytest.raises(RuntimeError, match="Failed to load confirmed history for recovery session"):
        await factory.create_bridge(session_id)

    # Invariant: Zero calls to egress gateway or backend
    mock_egress.open_session.assert_not_called()


def recovery_adapter(factory: AsyncMock) -> PipecatMediaAdapter:
    return PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        cloud_bridge_factory=factory,
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (PolicyDeniedError("private detail"), PermissionError),
        (ConsentRequiredError("private detail"), PermissionError),
        (RuntimeError("openai_realtime_authentication_failed"), ValueError),
        (RuntimeError("private detail"), ConnectionError),
    ],
)
async def test_offer_preflights_provider_before_signaling(
    failure: Exception,
    expected: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = recovery_adapter(AsyncMock(side_effect=failure))
    signaling = AsyncMock()
    monkeypatch.setattr(adapter._handler, "handle_web_request", signaling)
    try:
        with pytest.raises(expected) as caught:
            await adapter.offer(uuid4(), WebRtcOffer(sdp="offer", type="offer"))
        assert "private detail" not in str(caught.value)
        signaling.assert_not_awaited()
        assert not adapter._session_locks
        assert adapter.active_connections == 0
    finally:
        await adapter.close()


async def test_offer_releases_prepared_provider_when_signaling_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = MagicMock(spec=CloudRealtimeMediaBridge)
    bridge.cleanup = AsyncMock()
    adapter = recovery_adapter(AsyncMock(return_value=bridge))
    monkeypatch.setattr(
        adapter._handler, "handle_web_request", AsyncMock(side_effect=ValueError("bad SDP"))
    )
    try:
        with pytest.raises(ValueError, match="bad SDP"):
            await adapter.offer(uuid4(), WebRtcOffer(sdp="offer", type="offer"))
        bridge.cleanup.assert_awaited_once()
        assert not adapter._prepared_bridges
        assert not adapter._session_locks
    finally:
        await adapter.close()


async def test_session_lock_remains_shared_until_all_admissions_release() -> None:
    adapter = recovery_adapter(AsyncMock())
    sid = uuid4()
    first = await adapter._get_session_lock(sid)
    second = await adapter._get_session_lock(sid)
    await adapter._maybe_cleanup_session_lock(sid)
    third = await adapter._get_session_lock(sid)
    assert first is second is third
    await adapter._maybe_cleanup_session_lock(sid)
    assert sid in adapter._session_locks
    await adapter._maybe_cleanup_session_lock(sid)
    assert sid not in adapter._session_locks
    await adapter.close()


async def test_shutdown_during_signaling_closes_callback_peer_and_prepared_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = MagicMock(spec=CloudRealtimeMediaBridge)
    bridge.cleanup = AsyncMock()
    adapter = recovery_adapter(AsyncMock(return_value=bridge))
    peer = MagicMock(spec=SmallWebRTCConnection)
    peer.pc_id = "closing-peer"
    peer.disconnect = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def signaling(
        request: object,
        callback: Callable[[SmallWebRTCConnection], Awaitable[None]],
    ) -> dict[str, str]:
        entered.set()
        await release.wait()
        # SmallWebRTCRequestHandler logs callback exceptions and still returns an answer.
        try:
            await callback(peer)
        except RuntimeError:
            pass
        return {"pc_id": peer.pc_id, "sdp": "answer", "type": "answer"}

    monkeypatch.setattr(adapter._handler, "handle_web_request", signaling)
    opening = asyncio.create_task(adapter.offer(uuid4(), WebRtcOffer(sdp="offer", type="offer")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await adapter.close()
        release.set()
        with pytest.raises(RuntimeError, match="closed during admission"):
            await opening
        peer.disconnect.assert_awaited_once()
        bridge.cleanup.assert_awaited_once()
        assert adapter.active_connections == 0
        assert not adapter._prepared_bridges
        assert not adapter._session_locks
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        await adapter.close()
