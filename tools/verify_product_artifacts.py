"""Verify that Web and Desktop bundles contain only their owned product surfaces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, cast

ProductName = Literal["web", "desktop"]
ROOT = Path(__file__).resolve().parents[1]

REQUIRED_MODULES: dict[ProductName, tuple[str, ...]] = {
    "web": (
        "src/product/web/WebProductApp.tsx",
        "src/features/chat/ChatDemoPage.tsx",
        "src/features/avatar-lab/AvatarLabPage.tsx",
    ),
    "desktop": (
        "src/product/desktop/DesktopProductApp.tsx",
        "src/features/desktop-pet/DesktopPetPage.tsx",
        "src/features/desktop-settings/DesktopSettingsPage.tsx",
    ),
}
FORBIDDEN_PREFIXES: dict[ProductName, tuple[str, ...]] = {
    "web": ("src/features/desktop-pet/", "src/features/desktop-settings/"),
    "desktop": ("src/features/avatar-lab/", "src/features/chat/ChatDemoPage.tsx"),
}


def verify_product_artifact(product: ProductName, root: Path = ROOT) -> None:
    products = cast(
        dict[str, object],
        json.loads((root / "release/products.json").read_text(encoding="utf-8")),
    )
    profile = cast(dict[str, object], cast(dict[str, object], products["products"])[product])
    output = root / cast(str, profile["frontend_output"])
    manifest_path = output / "chatwaifu-product.json"
    manifest = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    if manifest.get("schema_version") != "1.0" or manifest.get("product") != product:
        raise ValueError(f"{manifest_path} does not identify the {product} product")
    if manifest.get("version") != profile["version"]:
        raise ValueError(f"{manifest_path} version does not match release/products.json")
    parsed_modules = manifest.get("modules")
    if not isinstance(parsed_modules, list):
        raise ValueError(f"{manifest_path} modules must be a string array")
    raw_modules = cast(list[object], parsed_modules)
    if not all(isinstance(item, str) for item in raw_modules):
        raise ValueError(f"{manifest_path} modules must be a string array")
    modules = set(cast(list[str], raw_modules))
    for required in REQUIRED_MODULES[product]:
        if required not in modules:
            raise ValueError(f"{product} artifact is missing required module {required}")
    for prefix in FORBIDDEN_PREFIXES[product]:
        leaked = sorted(module for module in modules if module.startswith(prefix))
        if leaked:
            raise ValueError(f"{product} artifact contains foreign modules: {', '.join(leaked)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("web", "desktop"), required=True)
    arguments = parser.parse_args()
    try:
        verify_product_artifact(cast(ProductName, arguments.product))
    except (OSError, KeyError, json.JSONDecodeError, ValueError) as error:
        print(f"product artifact error: {error}", file=sys.stderr)
        return 2
    print(f"{arguments.product} product artifact is isolated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
