"""Safe local paths for browser-playable generated speech."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class AudioAsset:
    asset_id: UUID
    path: Path

    @property
    def url(self) -> str:
        return f"/v1/audio/{self.asset_id}.wav"


@dataclass(slots=True)
class StagedAudioRemoval:
    directory: Path | None
    entries: tuple[tuple[Path, Path], ...]
    finished: bool = False

    @property
    def count(self) -> int:
        return len(self.entries)

    def commit(self) -> int:
        if self.finished:
            return self.count
        for _original, staged in self.entries:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                # It is already outside the public asset namespace.  Startup
                # cleanup retries an interrupted or permission-blocked purge.
                pass
        if self.directory is not None:
            try:
                self.directory.rmdir()
                self.directory.parent.rmdir()
            except OSError:
                pass
        self.finished = True
        return self.count

    def rollback(self) -> None:
        if self.finished:
            return
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
