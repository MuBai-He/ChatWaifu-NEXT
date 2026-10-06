"""Persistence-independent read contract for owner interaction diagnostics."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.diagnostics import InteractionTraceDetail, InteractionTracePage


class InteractionDiagnosticsReader(Protocol):
    async def list_interactions(
        self,
        session_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 50,
        include_nonparticipation: bool = False,
    ) -> InteractionTracePage: ...

    async def read_interaction(
        self,
        session_id: UUID,
        interaction_id: UUID,
        *,
        visible_namespaces: Sequence[str],
        after_sequence: int = 0,
        limit: int = 200,
    ) -> InteractionTraceDetail | None: ...
