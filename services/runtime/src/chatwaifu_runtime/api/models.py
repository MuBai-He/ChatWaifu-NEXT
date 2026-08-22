"""Runtime HTTP request and status models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(default="default", min_length=1, max_length=128)


class RuntimeHealth(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ready", "error"]
    subscribers: int
    dropped_events: int
