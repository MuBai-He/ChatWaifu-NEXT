"""Versioned contracts for index rebuild orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DomainRebuildState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DomainRebuildStatus:
    domain: str
    state: DomainRebuildState
    total_count: int
    indexed_count: int
    failed_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "state": str(self.state),
            "total_count": self.total_count,
            "indexed_count": self.indexed_count,
            "failed_count": self.failed_count,
            "error": self.error,
        }


class OverallRebuildState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class IndexRebuildStatus:
    job_id: str
    state: OverallRebuildState
    domains: dict[str, DomainRebuildStatus]
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "state": str(self.state),
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }
