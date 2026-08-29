from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.product_release import ReleaseContractError, set_version, verify_release
from tools.verify_product_artifacts import verify_product_artifact

ROOT = Path(__file__).resolve().parents[2]


def test_current_release_contract_accepts_exact_independent_tags() -> None:
    assert verify_release("web", "web-v0.2.0") == "web-v0.2.0"
    assert verify_release("desktop", "desktop-v0.2.0") == "desktop-v0.2.0"


@pytest.mark.parametrize(
    ("product", "tag"),
    [
        ("web", "desktop-v0.2.0"),
        ("web", "web-v0.2.1"),
        ("desktop", "desktop-0.2.0"),
    ],
)
def test_release_contract_rejects_wrong_or_cross_product_tags(product: str, tag: str) -> None:
    with pytest.raises(ReleaseContractError):
        verify_release(product, tag)  # type: ignore[arg-type]


def test_set_version_updates_only_the_selected_product_train(tmp_path: Path) -> None:
    for relative in (
        "release/products.json",
        "apps/web/package.json",
        "apps/desktop/package.json",
        "apps/desktop/src-tauri/tauri.conf.json",
        "apps/desktop/src-tauri/Cargo.toml",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    set_version("desktop", "0.3.0-beta.1", tmp_path)

    assert verify_release("desktop", "desktop-v0.3.0-beta.1", tmp_path) == ("desktop-v0.3.0-beta.1")
    assert verify_release("web", "web-v0.2.0", tmp_path) == "web-v0.2.0"


@pytest.mark.parametrize("product", ["web", "desktop"])
def test_artifact_verifier_accepts_only_owned_modules(product: str, tmp_path: Path) -> None:
    release = json.loads((ROOT / "release/products.json").read_text(encoding="utf-8"))
    output = tmp_path / release["products"][product]["frontend_output"]
    output.mkdir(parents=True)
    required = {
        "web": [
            "src/product/web/WebProductApp.tsx",
            "src/features/chat/ChatDemoPage.tsx",
            "src/features/avatar-lab/AvatarLabPage.tsx",
        ],
        "desktop": [
            "src/product/desktop/DesktopProductApp.tsx",
            "src/features/desktop-pet/DesktopPetPage.tsx",
            "src/features/desktop-settings/DesktopSettingsPage.tsx",
        ],
    }
    (tmp_path / "release").mkdir()
    (tmp_path / "release/products.json").write_text(json.dumps(release), encoding="utf-8")
    (output / "chatwaifu-product.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product": product,
                "version": release["products"][product]["version"],
                "modules": required[product],
            }
        ),
        encoding="utf-8",
    )

    verify_product_artifact(product, tmp_path)  # type: ignore[arg-type]


def test_artifact_verifier_rejects_cross_product_module(tmp_path: Path) -> None:
    release = json.loads((ROOT / "release/products.json").read_text(encoding="utf-8"))
    output = tmp_path / release["products"]["web"]["frontend_output"]
    output.mkdir(parents=True)
    (tmp_path / "release").mkdir()
    (tmp_path / "release/products.json").write_text(json.dumps(release), encoding="utf-8")
    (output / "chatwaifu-product.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product": "web",
                "version": "0.2.0",
                "modules": [
                    "src/product/web/WebProductApp.tsx",
                    "src/features/chat/ChatDemoPage.tsx",
                    "src/features/avatar-lab/AvatarLabPage.tsx",
                    "src/features/desktop-pet/DesktopPetPage.tsx",
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="foreign modules"):
        verify_product_artifact("web", tmp_path)
