"""Prepare ignored local Live2D Core, sample sources, shaders, and model assets."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SDK_DIRECTORY = "CubismSdkForWeb-5-r.5"
DEFAULT_SAMPLE_MODEL = "Natori"
PUBLIC_VENDOR = ROOT / "apps" / "web" / "public" / "vendor" / "live2d"
SAMPLE_SOURCE_TARGET = ROOT / "vendor" / "live2d" / "CubismWebSamples" / "src"
FRAMEWORK_SHADERS = ROOT / "vendor" / "live2d" / "CubismWebFramework" / "Shaders"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local Live2D vendor artifacts")
    parser.add_argument(
        "--sdk-dir",
        type=Path,
        help=f"path to the extracted {EXPECTED_SDK_DIRECTORY} directory",
    )
    parser.add_argument("--model", default=DEFAULT_SAMPLE_MODEL, help="official sample model name")
    arguments = parser.parse_args()

    sdk_directory = _resolve_sdk_directory(arguments.sdk_dir)
    _validate_sdk(sdk_directory, arguments.model)
    _copy_core(sdk_directory)
    _copy_sample_sources(sdk_directory)
    _copy_framework_shaders()
    _copy_model(sdk_directory, arguments.model)

    print(f"Prepared Live2D SDK R5 vendor inputs from {sdk_directory}")
    print(f"Installed local test model: {arguments.model}")
    print("Core and sample model remain ignored by Git and retain Live2D's license terms.")
    return 0


def _resolve_sdk_directory(requested: Path | None) -> Path:
    configured = os.environ.get("CHATWAIFU_LIVE2D_SDK_DIR")
    candidates = [
        requested,
        Path(configured).expanduser() if configured else None,
        Path.home() / "Downloads" / EXPECTED_SDK_DIRECTORY,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.expanduser().is_dir():
            return candidate.expanduser().resolve()
    raise FileNotFoundError(
        f"Could not find {EXPECTED_SDK_DIRECTORY}. Pass --sdk-dir or set CHATWAIFU_LIVE2D_SDK_DIR."
    )


def _validate_sdk(sdk_directory: Path, model: str) -> None:
    required = [
        sdk_directory / "Core" / "live2dcubismcore.min.js",
        sdk_directory / "Core" / "RedistributableFiles.txt",
        sdk_directory / "Framework" / "src" / "live2dcubismframework.ts",
        sdk_directory / "Samples" / "TypeScript" / "Demo" / "src" / "lappmodel.ts",
        sdk_directory / "Samples" / "Resources" / model / f"{model}.model3.json",
        FRAMEWORK_SHADERS / "WebGL",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        rendered = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"Live2D SDK input is incomplete:\n{rendered}")


def _copy_core(sdk_directory: Path) -> None:
    destination = PUBLIC_VENDOR / "live2dcubismcore.min.js"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sdk_directory / "Core" / destination.name, destination)


def _copy_sample_sources(sdk_directory: Path) -> None:
    source = sdk_directory / "Samples" / "TypeScript" / "Demo" / "src"
    shutil.copytree(source, SAMPLE_SOURCE_TARGET, dirs_exist_ok=True)
    define_path = SAMPLE_SOURCE_TARGET / "lappdefine.ts"
    contents = define_path.read_text()
    original = "export const ShaderPath = '../../Framework/Shaders/WebGL/';"
    replacement = "export const ShaderPath = '/vendor/live2d/framework/Shaders/WebGL/';"
    if original not in contents and replacement not in contents:
        raise RuntimeError(f"Unexpected ShaderPath declaration in {define_path}")
    define_path.write_text(contents.replace(original, replacement))


def _copy_framework_shaders() -> None:
    destination = PUBLIC_VENDOR / "framework" / "Shaders"
    shutil.copytree(FRAMEWORK_SHADERS, destination, dirs_exist_ok=True)


def _copy_model(sdk_directory: Path, model: str) -> None:
    source = sdk_directory / "Samples" / "Resources" / model
    destination = PUBLIC_VENDOR / "model"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    shutil.copy2(source / f"{model}.model3.json", destination / "avatar.model3.json")


if __name__ == "__main__":
    raise SystemExit(main())
