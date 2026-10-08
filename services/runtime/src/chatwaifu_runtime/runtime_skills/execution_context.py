"""Trusted execution lineage, scoped to one async skill worker."""

from contextvars import ContextVar
from uuid import UUID

from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext

authorized_task: ContextVar[UUID | None] = ContextVar("authorized_task", default=None)
authorized_generation: ContextVar[GenerationSkillContext | None] = ContextVar(
    "authorized_generation", default=None
)
