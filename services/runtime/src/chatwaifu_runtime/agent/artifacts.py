"""Content-addressed immutable artifacts with scope and checksum validation."""

import asyncio
import hashlib
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from chatwaifu_protocol.agent import ArtifactRef

from chatwaifu_runtime.agent.artifact_ports import ArtifactRepository
from chatwaifu_runtime.runtime_skills.execution_context import authorized_task
from chatwaifu_runtime.sessions.service import SessionService

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


class ArtifactService:
    def __init__(
        self, root: Path, repository: ArtifactRepository, sessions: SessionService
    ) -> None:
        self.root = root
        self.repository = repository
        self.sessions = sessions

    async def scope(self, session_id: UUID) -> str:
        session = await self.sessions.get_session(session_id)
        if session is None:
            raise KeyError("session not found")
        return session.user_scope

    async def save(
        self,
        session_id: UUID,
        name: str,
        media_type: str,
        content: bytes,
        *,
        validation_status: str = "structural",
        task_id: UUID | None = None,
    ) -> ArtifactRef:
        scope = await self.scope(session_id)
        if not 0 < len(content) <= MAX_ARTIFACT_BYTES:
            raise ValueError("artifact size outside limit")
        if not name or len(name) > 128 or any(c in name for c in "/\\\x00\r\n"):
            raise ValueError("artifact name must be a filename")
        identity = uuid4()
        digest = hashlib.sha256(content).hexdigest()
        relative = f"{identity.hex}/{digest}"
        destination = self.root / relative
        write = asyncio.create_task(asyncio.to_thread(_write, destination, content))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            destination.unlink(missing_ok=True)
            destination.parent.rmdir()
            raise
        artifact = ArtifactRef(
            artifact_id=identity,
            task_id=task_id or authorized_task.get(),
            session_id=session_id,
            name=name,
            media_type=media_type,
            byte_length=len(content),
            sha256=digest,
            created_at=datetime.now(UTC),
        ).model_copy(update={"validation_status": validation_status})
        try:
            # Once publication begins, cancellation cannot orphan committed metadata.
            publication = asyncio.create_task(self.repository.put(scope, artifact, relative))
            try:
                await asyncio.shield(publication)
            except asyncio.CancelledError:
                await publication
                raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return artifact

    async def resolve(self, session_id: UUID, artifact_id: UUID) -> tuple[ArtifactRef, Path]:
        scope = await self.scope(session_id)
        found = await self.repository.get(scope, artifact_id)
        if found is None:
            raise KeyError("artifact not visible")
        artifact, relative = found
        path = self.root / relative
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(self.root)):
            raise ValueError("artifact ancestor is a symlink")
        if not path.resolve().is_relative_to(self.root.resolve()) or path.is_symlink():
            raise ValueError("artifact path escaped storage")
        if not path.is_file() or path.stat().st_size != artifact.byte_length:
            raise ValueError("artifact content changed")
        content = await asyncio.to_thread(_read_bounded, path, artifact.byte_length)
        if (
            len(content) != artifact.byte_length
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise ValueError("artifact content changed")
        return artifact, path

    async def read(self, session_id: UUID, artifact_id: UUID) -> tuple[ArtifactRef, bytes]:
        artifact, path = await self.resolve(session_id, artifact_id)
        content = await asyncio.to_thread(_read_bounded, path, artifact.byte_length)
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ValueError("artifact content changed")
        return artifact, content


def _read_bounded(path: Path, expected: int) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected:
            raise ValueError("artifact content changed")
        content = stream.read(min(expected, MAX_ARTIFACT_BYTES) + 1)
        if len(content) != expected:
            raise ValueError("artifact content changed")
        return content


def _write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=False)
    with destination.open("xb") as stream:
        stream.write(content)
