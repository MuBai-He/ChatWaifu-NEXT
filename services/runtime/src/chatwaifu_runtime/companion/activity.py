"""In-memory current-presence clock shared by resource and ambient policies."""

import time
from collections.abc import Callable
from uuid import UUID


class ActivityTracker:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._global_last = monotonic()
        self._sessions: dict[UUID, float] = {}

    def touch(self, session_id: UUID | None = None) -> None:
        now = self._monotonic()
        self._global_last = now
        if session_id is not None:
            self._sessions[session_id] = now

    def global_idle_seconds(self) -> float:
        return max(0.0, self._monotonic() - self._global_last)

    def session_idle_seconds(self, session_id: UUID) -> float:
        last = self._sessions.setdefault(session_id, self._monotonic())
        return max(0.0, self._monotonic() - last)
