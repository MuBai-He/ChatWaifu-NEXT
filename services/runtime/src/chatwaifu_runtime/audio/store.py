"""Safe local paths for browser-playable generated speech."""

import errno
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioAsset:
    asset_id: UUID
    path: Path

    @property
    def url(self) -> str:
        return f"/v1/audio/{self.asset_id}.wav"


@dataclass(frozen=True, slots=True)
class AudioRemovalCommit:
    deleted_count: int
    pending_count: int
    cleanup_complete: bool


@dataclass(slots=True)
class StagedAudioRemoval:
    directory: Path | None
    entries: tuple[tuple[Path, Path], ...]
    finished: bool = False
    _commit_started: bool = False
    _purged: set[Path] = field(default_factory=lambda: set[Path](), repr=False)

    @property
    def count(self) -> int:
        return len(self.entries)

    def commit(self) -> AudioRemovalCommit:
        if self.finished:
            return AudioRemovalCommit(
                deleted_count=len(self._purged),
                pending_count=0,
                cleanup_complete=True,
            )

        self._commit_started = True
        for _original, staged in self.entries:
            if staged in self._purged:
                continue
            if not staged.exists():
                self._purged.add(staged)
                continue
            try:
                staged.unlink()
            except OSError as error:
                logger.warning(
                    "audio asset purge failed; retaining quarantine entry for retry",
                    extra={
                        "audio_asset_id": staged.stem,
                        "quarantine_batch": (
                            self.directory.name if self.directory is not None else None
                        ),
                        "error": type(error).__name__,
                    },
                    exc_info=True,
                )
            else:
                self._purged.add(staged)

        pending_count = sum(
            staged not in self._purged and staged.exists() for _original, staged in self.entries
        )
        cleanup_complete = pending_count == 0
        if cleanup_complete and self.directory is not None:
            try:
                self.directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as error:
                cleanup_complete = False
                logger.warning(
                    "audio quarantine batch cleanup failed; retaining it for retry",
                    extra={
                        "quarantine_batch": self.directory.name,
                        "error": type(error).__name__,
                    },
                    exc_info=True,
                )

            if not self.directory.exists():
                try:
                    self.directory.parent.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    # Another staged reset can legitimately keep the shared
                    # quarantine root non-empty. Other failures need to remain
                    # visible and will be retried by startup reconciliation.
                    if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                        cleanup_complete = False
                        logger.warning(
                            "audio quarantine root cleanup failed; retaining it for retry",
                            extra={
                                "quarantine_root": self.directory.parent.name,
                                "error": type(error).__name__,
                            },
                            exc_info=True,
                        )

        self.finished = cleanup_complete
        return AudioRemovalCommit(
            deleted_count=len(self._purged),
            pending_count=pending_count,
            cleanup_complete=cleanup_complete,
        )

    def rollback(self) -> None:
        if self.finished:
            return
        if self._commit_started:
            raise RuntimeError("cannot restore audio assets after purge has started")
        errors: list[Exception] = []
        for original, staged in reversed(self.entries):
            if not staged.exists():
                continue
            try:
                staged.replace(original)
            except Exception as error:
                errors.append(error)
        if self.directory is not None:
            try:
                self.directory.rmdir()
                self.directory.parent.rmdir()
            except Exception as error:
                if self.directory.exists():
                    errors.append(error)
        self.finished = True
        if errors:
            raise ExceptionGroup("failed to restore staged audio assets", errors)


class AudioAssetStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def start(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def recover_staged_removals(self, referenced_asset_ids: Iterable[UUID]) -> None:
        """Reconcile crash-left reset batches against durable playback ownership.

        A process can stop after files were hidden but before the matching SQLite
        reset commits. Referenced assets must therefore be restored; only assets
        no longer present in SQLite may be purged.
        """

        referenced = set(referenced_asset_ids)
        quarantine = self._root / "reset-quarantine"
        if not quarantine.is_dir():
            return
        for batch in quarantine.iterdir():
            if not batch.is_dir():
                raise RuntimeError("audio reset quarantine contains an invalid entry")
            for staged in batch.iterdir():
                if not staged.is_file() or staged.suffix != ".wav":
                    raise RuntimeError("audio reset quarantine contains an invalid asset")
                try:
                    asset_id = UUID(staged.stem)
                except ValueError as error:
                    raise RuntimeError("audio reset quarantine asset has an invalid ID") from error
                if asset_id in referenced:
                    original = self._root / staged.name
                    if original.exists():
                        staged.unlink()
                    else:
                        staged.replace(original)
                else:
                    staged.unlink()
            batch.rmdir()
        quarantine.rmdir()

    def allocate(self) -> AudioAsset:
        asset_id = uuid4()
        return AudioAsset(asset_id=asset_id, path=self._root / f"{asset_id}.wav")

    def resolve(self, asset_id: UUID) -> Path | None:
        path = self._root / f"{asset_id}.wav"
        return path if path.is_file() else None

    def remove(self, asset_ids: Iterable[UUID]) -> int:
        """Remove only explicitly owned assets; unrelated session audio is preserved."""

        removed = 0
        for asset_id in set(asset_ids):
            path = self._root / f"{asset_id}.wav"
            if not path.is_file():
                continue
            path.unlink()
            removed += 1
        return removed

    def stage_remove(self, asset_ids: Iterable[UUID]) -> StagedAudioRemoval:
        """Atomically hide owned assets until the matching DB reset commits."""

        paths = [
            self._root / f"{asset_id}.wav"
            for asset_id in set(asset_ids)
            if (self._root / f"{asset_id}.wav").is_file()
        ]
        if not paths:
            return StagedAudioRemoval(None, ())
        directory = self._root / "reset-quarantine" / str(uuid4())
        directory.mkdir(parents=True)
        entries: list[tuple[Path, Path]] = []
        batch = StagedAudioRemoval(directory, ())
        try:
            for original in paths:
                staged = directory / original.name
                original.replace(staged)
                entries.append((original, staged))
            return StagedAudioRemoval(directory, tuple(entries))
        except BaseException:
            batch.entries = tuple(entries)
            batch.rollback()
            raise

    def clear(self) -> int:
        removed = 0
        for path in self._root.glob("*.wav"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed
