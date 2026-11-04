"""Nonsecret model cost telemetry with coroutine-local, Runtime-issued attribution."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import Literal
from uuid import UUID, uuid4

from chatwaifu_runtime.providers.contracts import LlmUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelTrace:
    owner: str
    task_id: UUID | None = None
    work_item_id: str | None = None
    observation_id: UUID | None = None
    observation_ids: tuple[UUID, ...] = ()


model_trace: ContextVar[ModelTrace | None] = ContextVar("model_cost_trace", default=None)


@contextmanager
def model_scope(trace: ModelTrace) -> Generator[None]:
    token = model_trace.set(trace)
    try:
        yield
    finally:
        model_trace.reset(token)


@dataclass(frozen=True)
class ModelUsageSample:
    request_id: UUID
    trace: ModelTrace
    role: str
    model: str
    duration_ms: int
    state: Literal["completed", "failed", "cancelled"]
    usage: LlmUsage | None
    synthetic: bool
    observed_at: int


UsageSink = Callable[[ModelUsageSample], Awaitable[None]]


class ModelMeter:
    def __init__(
        self, sink: UsageSink | None, role: str, model: str, *, synthetic: bool = False
    ) -> None:
        self.sink, self.role, self.model = sink, role, model
        self.trace = model_trace.get()
        self.synthetic = synthetic
        self.usage: LlmUsage | None = None
        self.started = 0.0

    async def __aenter__(self) -> ModelMeter:
        self.started = time.monotonic()
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self.trace is not None and self.sink is not None:
            sample = ModelUsageSample(
                uuid4(),
                self.trace,
                self.role,
                self.model[:256],
                max(0, int((time.monotonic() - self.started) * 1000)),
                "cancelled"
                if kind is not None and issubclass(kind, (asyncio.CancelledError, GeneratorExit))
                else "failed"
                if kind
                else "completed",
                self.usage,
                self.synthetic,
                time.time_ns() // 1000000,
            )
            try:
                await self.sink(sample)
            except Exception as failure:
                # Telemetry cannot make a successful action fail or trigger an RPC retry.
                logger.warning("model.cost_telemetry_failed type=%s", type(failure).__name__)
        return False
