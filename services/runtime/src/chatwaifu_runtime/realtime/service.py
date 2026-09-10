"""Runtime-facing realtime media service with ChatWaifu-owned DTOs."""

from typing import Protocol
from uuid import UUID

from chatwaifu_runtime.realtime.pipecat.session import (
    WebRtcCandidate,
    WebRtcOffer,
)


class BackendMediaAdapter(Protocol):
    @property
    def active_connections(self) -> int: ...

    async def offer(self, session_id: UUID, offer: WebRtcOffer) -> dict[str, str]: ...

    async def patch(self, pc_id: str, candidates: list[WebRtcCandidate]) -> None: ...

    async def close_session(self, session_id: UUID, pc_id: str | None = None) -> int: ...

    async def close(self) -> None: ...


class VoiceMediaService:
    def __init__(self, adapter: BackendMediaAdapter) -> None:
        self._adapter = adapter

    @property
    def active_connections(self) -> int:
        return self._adapter.active_connections

    async def offer(
        self,
        session_id: UUID,
        *,
        sdp: str,
        type: str,
        pc_id: str | None,
        restart_pc: bool,
        activation_mode: str,
    ) -> dict[str, str]:
        return await self._adapter.offer(
            session_id,
            WebRtcOffer(
                sdp=sdp,
                type=type,
                pc_id=pc_id,
                restart_pc=restart_pc,
                activation_mode=activation_mode,
            ),
        )

    async def patch(
        self,
        *,
        pc_id: str,
        candidates: list[tuple[str, str, int]],
    ) -> None:
        await self._adapter.patch(
            pc_id,
            [
                WebRtcCandidate(
                    candidate=candidate,
                    sdp_mid=sdp_mid,
                    sdp_mline_index=sdp_mline_index,
                )
                for candidate, sdp_mid, sdp_mline_index in candidates
            ],
        )

    async def close_session(self, session_id: UUID, pc_id: str | None = None) -> int:
        return await self._adapter.close_session(session_id, pc_id=pc_id)

    async def close(self) -> None:
        await self._adapter.close()
