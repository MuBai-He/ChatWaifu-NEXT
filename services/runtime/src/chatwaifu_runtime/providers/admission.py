"""Per-route bounded model admission with a reserved foreground slot."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Literal

work_class: ContextVar[Literal["interactive", "background", "historical"]] = ContextVar(
    "model_work_class", default="interactive"
)


class ModelAdmission:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._routes: dict[str, list[int]] = {}

    @asynccontextmanager
    async def enter(
        self, route: str, kind: Literal["interactive", "background", "historical"] | None = None
    ) -> AsyncGenerator[None]:
        mode = kind or work_class.get()
        background = mode != "interactive"
        historical = mode == "historical"
        async with self._condition:
            counts = self._routes.setdefault(route, [0, 0, 0, 0])
            counts[3] += int(not background)
            try:
                await self._condition.wait_for(
                    lambda: (
                        counts[0] < 4
                        and (not background or counts[1] < 3)
                        and (not historical or counts[2] < 1)
                        and (not background or counts[3] == 0)
                    )
                )
            finally:
                counts[3] -= int(not background)
                self._condition.notify_all()
            counts[0] += 1
            counts[1] += int(background)
            counts[2] += int(historical)
        try:
            yield
        finally:
            async with self._condition:
                counts[0] -= 1
                counts[1] -= int(background)
                counts[2] -= int(historical)
                self._condition.notify_all()
