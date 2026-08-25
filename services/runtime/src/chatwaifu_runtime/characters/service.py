"""Load validated, renderer-independent character manifests."""

import json
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CharacterVoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    voice_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    language: str = Field(min_length=2, max_length=32)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    speaker_id: int = Field(ge=0, le=1024)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    license: str = Field(min_length=1, max_length=128)


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    tagline: str = Field(min_length=1, max_length=240)
    greeting: str = Field(min_length=1, max_length=1000)
    system_prompt: str = Field(min_length=1, max_length=10_000)
    accent_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    voice_profile: CharacterVoiceProfile
    content_notice: str = Field(min_length=1, max_length=1000)
    style: dict[str, float | str] = Field(default_factory=dict)
    boundaries: dict[str, bool] = Field(default_factory=dict)
    relationship_policy: dict[str, Any] = Field(default_factory=dict)
    avatar_capabilities: dict[str, list[str]] = Field(default_factory=dict)
    lexicon: dict[str, Any] = Field(default_factory=dict)


class CharacterService:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._profiles: dict[str, CharacterProfile] = {}

    def start(self) -> None:
        profiles: dict[str, CharacterProfile] = {}
        directories = sorted(path for path in self._root.iterdir() if path.is_dir())
        for directory in directories:
            yaml_path = directory / "character.yaml"
            json_path = directory / "character.json"
            if yaml_path.exists():
                profile = self._load_package(directory)
            elif json_path.exists():
                profile = CharacterProfile.model_validate(
                    json.loads(json_path.read_text(encoding="utf-8"))
                )
            else:
                continue
            expected_id = directory.name
            if profile.character_id != expected_id:
                raise ValueError(
                    f"character id {profile.character_id!r} does not match "
                    f"directory {expected_id!r}"
                )
            profiles[profile.character_id] = profile
        if "default" not in profiles:
            raise RuntimeError("default character manifest is required")
        self._profiles = profiles

    def _load_package(self, directory: Path) -> CharacterProfile:
        character = _read_yaml(directory / "character.yaml")
        voice = _read_yaml(directory / "voice.yaml")
        avatar = _read_yaml(directory / "avatar.yaml")
        relationship = _read_yaml(directory / "relationship-policy.yaml")
        lexicon = _read_yaml(directory / "lexicon.yaml")
        persona = (directory / "persona.md").read_text(encoding="utf-8").strip()
        return CharacterProfile.model_validate(
            {
                **character,
                "system_prompt": persona,
                "voice_profile": voice,
                "relationship_policy": relationship,
                "avatar_capabilities": avatar,
                "lexicon": lexicon,
            }
        )

    def get(self, character_id: str) -> CharacterProfile | None:
        return self._profiles.get(character_id)

    def list(self) -> list[CharacterProfile]:
        return list(self._profiles.values())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"character package file is missing: {path.name}")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"character package file must contain an object: {path.name}")
    return cast(dict[str, Any], value)
