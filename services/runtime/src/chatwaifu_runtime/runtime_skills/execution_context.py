"""Trusted execution lineage, scoped to one async skill worker."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext

authorized_task: ContextVar[UUID | None] = ContextVar("authorized_task", default=None)
authorized_generation: ContextVar[GenerationSkillContext | None] = ContextVar(
    "authorized_generation", default=None
)

# Captured by child skill workers; models cannot choose this operation identity.
authorized_action: ContextVar[UUID | None] = ContextVar("authorized_action", default=None)


@contextmanager
def operation_scope(identifier: UUID) -> Generator[None]:
    token = authorized_action.set(identifier)
    try:
        yield
    finally:
        authorized_action.reset(token)
