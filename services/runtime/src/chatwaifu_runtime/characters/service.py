"""Load validated, renderer-independent character manifests."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    tagline: str = Field(min_length=1, max_length=240)
    greeting: str = Field(min_length=1, max_length=1000)
    system_prompt: str = Field(min_length=1, max_length=10_000)
    accent_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class CharacterService:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._profiles: dict[str, CharacterProfile] = {}

    def start(self) -> None:
        profiles: dict[str, CharacterProfile] = {}
        for path in sorted(self._root.glob("*/character.json")):
            profile = CharacterProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
            expected_id = path.parent.name
            if profile.character_id != expected_id:
                raise ValueError(
                    f"character id {profile.character_id!r} does not match "
                    f"directory {expected_id!r}"
                )
            profiles[profile.character_id] = profile
        if "default" not in profiles:
            raise RuntimeError("default character manifest is required")
        self._profiles = profiles

    def get(self, character_id: str) -> CharacterProfile | None:
        return self._profiles.get(character_id)

    def list(self) -> list[CharacterProfile]:
        return list(self._profiles.values())
