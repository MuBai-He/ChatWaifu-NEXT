"""Validate and update ChatWaifu's independent Web/Desktop release trains."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

ProductName = Literal["web", "desktop"]
PRODUCT_NAMES: tuple[ProductName, ...] = ("web", "desktop")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractError(ValueError):
    """The checked release metadata is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class ProductRelease:
    name: ProductName
    version: str
    tag_prefix: str
    frontend_mode: str
    frontend_output: str

    @property
    def tag(self) -> str:
        return f"{self.tag_prefix}{self.version}"


def load_products(root: Path = ROOT) -> dict[ProductName, ProductRelease]:
    manifest_path = root / "release/products.json"
    parsed = cast(object, json.loads(manifest_path.read_text(encoding="utf-8")))
    if not isinstance(parsed, dict):
        raise ReleaseContractError("release/products.json must be an object")
    raw = cast(dict[str, object], parsed)
    if raw.get("schema_version") != "1.0":
        raise ReleaseContractError("release/products.json must use schema_version 1.0")
    parsed_products = raw.get("products")
    if not isinstance(parsed_products, dict):
        raise ReleaseContractError("release manifest products must be an object")
    raw_products = cast(dict[str, object], parsed_products)
    if set(raw_products) != set(PRODUCT_NAMES):
        raise ReleaseContractError("release manifest must define exactly web and desktop")

    products: dict[ProductName, ProductRelease] = {}
    for name in PRODUCT_NAMES:
        parsed_value = raw_products.get(name)
        if not isinstance(parsed_value, dict):
            raise ReleaseContractError(f"release product {name} must be an object")
        value = cast(dict[str, object], parsed_value)
        fields: dict[str, str] = {}
        for field in ("version", "tag_prefix", "frontend_mode", "frontend_output"):
            field_value = value.get(field)
            if not isinstance(field_value, str) or not field_value:
                raise ReleaseContractError(f"release product {name}.{field} must be a string")
            fields[field] = field_value
        product = ProductRelease(
            name=name,
            version=fields["version"],
            tag_prefix=fields["tag_prefix"],
            frontend_mode=fields["frontend_mode"],
            frontend_output=fields["frontend_output"],
        )
        _validate_product(product)
        products[name] = product

    if len({product.tag_prefix for product in products.values()}) != len(products):
        raise ReleaseContractError("release tag prefixes must be unique")
    if len({product.frontend_output for product in products.values()}) != len(products):
        raise ReleaseContractError("release frontend outputs must be unique")
    return products


def verify_release(product_name: ProductName, tag: str | None, root: Path = ROOT) -> str:
    product = load_products(root)[product_name]
    _verify_version_mirrors(product, root)
    if tag is not None and tag != product.tag:
        raise ReleaseContractError(
            f"tag {tag!r} does not match {product_name} release {product.tag!r}"
        )
    return product.tag


def set_version(product_name: ProductName, version: str, root: Path = ROOT) -> None:
    if not SEMVER.fullmatch(version):
        raise ReleaseContractError(f"invalid SemVer: {version!r}")
    manifest_path = root / "release/products.json"
    manifest = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    products = cast(dict[str, object], manifest["products"])
    product = cast(dict[str, object], products[product_name])
    product["version"] = version

    updates: dict[Path, str] = {
        manifest_path: _json_text(manifest),
    }
    if product_name == "web":
        package_path = root / "apps/web/package.json"
        package = cast(dict[str, object], json.loads(package_path.read_text(encoding="utf-8")))
        package["version"] = version
        updates[package_path] = _json_text(package)
    else:
        for relative in ("apps/desktop/package.json", "apps/desktop/src-tauri/tauri.conf.json"):
            path = root / relative
            value = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
            value["version"] = version
            updates[path] = _json_text(value)
        cargo_path = root / "apps/desktop/src-tauri/Cargo.toml"
        cargo = cargo_path.read_text(encoding="utf-8")
        updates[cargo_path] = re.sub(
            r'(?m)^(version\s*=\s*")[^"]+("\s*)$',
            rf"\g<1>{version}\g<2>",
            cargo,
            count=1,
        )

    _replace_files(updates)
    verify_release(product_name, None, root)


def _validate_product(product: ProductRelease) -> None:
    if not SEMVER.fullmatch(product.version):
        raise ReleaseContractError(f"{product.name} version is not strict SemVer")
    if product.frontend_mode != product.name:
        raise ReleaseContractError(f"{product.name} frontend_mode must equal its product name")
    if not product.tag_prefix.endswith("-v"):
        raise ReleaseContractError(f"{product.name} tag_prefix must end with '-v'")
    output = Path(product.frontend_output)
    if output.is_absolute() or ".." in output.parts:
        raise ReleaseContractError(
            f"{product.name} frontend_output must stay inside the repository"
        )


def _verify_version_mirrors(product: ProductRelease, root: Path) -> None:
    mirrors = (
        [root / "apps/web/package.json"]
        if product.name == "web"
        else [
            root / "apps/desktop/package.json",
            root / "apps/desktop/src-tauri/tauri.conf.json",
        ]
    )
    for path in mirrors:
        value = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        if value.get("version") != product.version:
            raise ReleaseContractError(
                f"{path.relative_to(root)} version does not match {product.name} {product.version}"
            )
    if product.name == "desktop":
        cargo_path = root / "apps/desktop/src-tauri/Cargo.toml"
        match = re.search(
            r'(?m)^version\s*=\s*"([^"]+)"\s*$', cargo_path.read_text(encoding="utf-8")
        )
        if not match or match.group(1) != product.version:
            raise ReleaseContractError(
                "apps/desktop/src-tauri/Cargo.toml version does not match desktop release"
            )


def _json_text(value: object) -> str:
    return f"{json.dumps(value, ensure_ascii=False, indent=2)}\n"


def _replace_files(updates: dict[Path, str]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, content in updates.items():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", dir=destination.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, destination))
        for temporary, destination in staged:
            temporary.replace(destination)
    finally:
        for temporary, _destination in staged:
            temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--product", choices=PRODUCT_NAMES, required=True)
    verify.add_argument("--tag")
    set_version_parser = subparsers.add_parser("set-version")
    set_version_parser.add_argument("--product", choices=PRODUCT_NAMES, required=True)
    set_version_parser.add_argument("--version", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        product_name = cast(ProductName, arguments.product)
        if arguments.command == "verify":
            print(verify_release(product_name, cast(str | None, arguments.tag)))
        else:
            set_version(product_name, cast(str, arguments.version))
            print(verify_release(product_name, None))
    except (OSError, KeyError, json.JSONDecodeError, ReleaseContractError) as error:
        print(f"release contract error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
