"""Install a user-supplied Ayachi Nene Live2D archive into the ignored web vendor path."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = Path.home() / "Downloads" / "AYACHI NENE.7z"
DESTINATION = ROOT / "apps" / "web" / "public" / "vendor" / "live2d" / "model"
SOURCE_DIRECTORY = "綾地寧々"

MOTIONS = {
    "idle-1.motion3.json": "待机动作1.motion3.json",
    "idle-2.motion3.json": "待机动作3.motion3.json",
    "headpat.motion3.json": "摸头.motion3.json",
    "stare.motion3.json": "......(盯).......motion3.json",
    "flustered.motion3.json": "0721.motion3.json",
    "sing.motion3.json": "唱歌.motion3.json",
}


def _parameter(parameter_id: str, value: float) -> dict[str, object]:
    return {"Id": parameter_id, "Value": value, "Blend": "Add"}


EXPRESSIONS: dict[str, list[dict[str, object]]] = {
    "neutral": [],
    "happy": [
        _parameter("ParamCheek", 0.35),
        _parameter("ParamCheek12", 0.65),
        _parameter("ParamEyeLSmile", 0.55),
        _parameter("ParamEyeRSmile", 0.55),
        _parameter("ParamMouthForm", 0.45),
    ],
    "curious": [
        _parameter("ParamCheek8", 0.45),
        _parameter("ParamCheek18", 0.35),
        _parameter("ParamBrowLY", 0.25),
        _parameter("ParamBrowRY", 0.25),
    ],
    "shy": [
        _parameter("ParamCheek", 0.55),
        _parameter("ParamCheek5", 0.8),
        _parameter("ParamCheek18", 0.35),
        _parameter("ParamMouthForm", 0.2),
    ],
    "sad": [
        _parameter("ParamCheek7", 1.0),
        _parameter("ParamCheek14", 0.8),
        _parameter("ParamCheek17", 0.7),
    ],
    "angry": [
        _parameter("ParamCheek13", 0.8),
        _parameter("ParamBrowLAngle", -0.45),
        _parameter("ParamBrowRAngle", 0.45),
    ],
    "surprised": [
        _parameter("ParamCheek8", 0.8),
        _parameter("ParamCheek18", 0.55),
        _parameter("ParamEyeLOpen", 0.25),
        _parameter("ParamEyeROpen", 0.25),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the local-only Ayachi Nene Live2D model")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    arguments = parser.parse_args()
    archive = arguments.archive.expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Ayachi Nene archive not found: {archive}")
    if shutil.which("bsdtar") is None:
        raise RuntimeError("bsdtar is required to read the local 7z archive")

    with tempfile.TemporaryDirectory(prefix="chatwaifu-nene-") as temporary:
        extracted = Path(temporary)
        subprocess.run(
            ["bsdtar", "-xf", str(archive), "-C", str(extracted)],
            check=True,
        )
        source = extracted / SOURCE_DIRECTORY
        _validate_source(source)
        _install(source)

    print(f"Installed local-only Ayachi Nene model from {archive}")
    print(f"Model path: {DESTINATION / 'avatar.model3.json'}")
    print("Character assets remain ignored and must not be force-added to Git.")
    return 0


def _validate_source(source: Path) -> None:
    required = [
        source / "綾地寧々.moc3",
        source / "綾地寧々.physics3.json",
        source / "綾地寧々.cdi3.json",
        source / "綾地寧々.8192" / "texture_00.png",
        *(source / "motion" / name for name in MOTIONS.values()),
    ]
    missing = [path for path in required if not path.is_file()]
    if missing:
        rendered = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"Ayachi Nene archive is incomplete:\n{rendered}")


def _install(source: Path) -> None:
    texture_directory = DESTINATION / "texture"
    motion_directory = DESTINATION / "motions"
    expression_directory = DESTINATION / "expressions"
    texture_directory.mkdir(parents=True, exist_ok=True)
    motion_directory.mkdir(parents=True, exist_ok=True)
    expression_directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source / "綾地寧々.moc3", DESTINATION / "avatar.moc3")
    shutil.copy2(source / "綾地寧々.physics3.json", DESTINATION / "avatar.physics3.json")
    shutil.copy2(source / "綾地寧々.cdi3.json", DESTINATION / "avatar.cdi3.json")
    shutil.copy2(
        source / "綾地寧々.8192" / "texture_00.png",
        texture_directory / "texture_00.png",
    )

    for destination_name, source_name in MOTIONS.items():
        motion = _read_json(source / "motion" / source_name)
        if destination_name not in {"idle-1.motion3.json", "idle-2.motion3.json"}:
            motion.setdefault("Meta", {})["Loop"] = False
        _write_json(motion_directory / destination_name, motion)

    expression_references: list[dict[str, str]] = []
    for name, parameters in EXPRESSIONS.items():
        file_name = f"{name}.exp3.json"
        _write_json(
            expression_directory / file_name,
            {"Type": "Live2D Expression", "Parameters": parameters},
        )
        expression_references.append({"Name": name.title(), "File": f"expressions/{file_name}"})

    original_model = _read_json(source / "綾地寧々.model3.json")
    model = {
        "Version": 3,
        "FileReferences": {
            "Moc": "avatar.moc3",
            "Textures": ["texture/texture_00.png"],
            "Physics": "avatar.physics3.json",
            "DisplayInfo": "avatar.cdi3.json",
            "Expressions": expression_references,
            "Motions": {
                "Idle": [
                    {"File": "motions/idle-1.motion3.json"},
                    {"File": "motions/idle-2.motion3.json"},
                ],
                "ChatWaifuAction": [
                    {"File": "motions/headpat.motion3.json"},
                    {"File": "motions/stare.motion3.json"},
                    {"File": "motions/flustered.motion3.json"},
                    {"File": "motions/sing.motion3.json"},
                ],
            },
        },
        "Groups": original_model.get("Groups", []),
        "HitAreas": [
            {"Id": "ArtMesh175", "Name": "Head"},
            {"Id": "ArtMesh332", "Name": "Body"},
        ],
    }
    _write_json(DESTINATION / "avatar.model3.json", model)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
