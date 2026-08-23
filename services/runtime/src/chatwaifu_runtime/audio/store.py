"""Safe local paths for browser-playable generated speech."""

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


class AudioAssetStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def start(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def allocate(self) -> AudioAsset:
        asset_id = uuid4()
        return AudioAsset(asset_id=asset_id, path=self._root / f"{asset_id}.wav")

    def resolve(self, asset_id: UUID) -> Path | None:
        path = self._root / f"{asset_id}.wav"
        return path if path.is_file() else None

    def clear(self) -> int:
        removed = 0
        for path in self._root.glob("*.wav"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed
