"""Enforce the architecture boundaries selected for the current CI gate.

This is intentionally a focused guard, not a claim that every architecture rule
can be proven statically. It protects the boundaries made explicit in the current
hardening slice: heavy model SDKs stay out of Runtime, conversation and Runtime
Skills use repository ports, and Web never embeds direct provider integrations.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "services/runtime/src/chatwaifu_runtime"
RUNTIME_PYPROJECT = PROJECT_ROOT / "services/runtime/pyproject.toml"
WEB_ROOT = PROJECT_ROOT / "apps/web/src"
WEB_PACKAGE_JSON = PROJECT_ROOT / "apps/web/package.json"

HEAVY_PROVIDER_MODULES = {
    "GPT_SoVITS",
    "TTS",
    "diffusers",
    "faster_whisper",
    "mlx",
    "mlx_audio",
    "onnxruntime",
    "qwen_tts",
    "sherpa_onnx",
    "torch",
    "transformers",
}
HEAVY_RUNTIME_PACKAGES = {
    "coqui-tts",
    "faster-whisper",
    "gpt-sovits",
    "mlx",
    "mlx-audio",
    "onnxruntime",
    "qwen-tts",
    "sherpa-onnx",
    "torch",
    "transformers",
}
FORBIDDEN_FRONTEND_PACKAGES = {
    "@alicloud/dashscope-sdk",
    "@anthropic-ai/sdk",
    "@google/generative-ai",
    "dashscope",
    "openai",
}
FORBIDDEN_PROVIDER_HOSTS = (
    "api.anthropic.com",
    "api-inference.modelscope.cn",
    "api.openai.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope.aliyuncs.com",
    "generativelanguage.googleapis.com",
    "open.bigmodel.cn",
)


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    detail: str


def main() -> int:
    violations = [
        *forbidden_python_imports(),
        *heavy_runtime_dependencies(),
        *provider_urls_in_frontend(),
        *provider_sdks_in_frontend(),
    ]
    if not violations:
        print("Architecture boundary checks passed.")
        return 0
    for violation in sorted(violations, key=lambda item: (str(item.path), item.line)):
        relative = violation.path.relative_to(PROJECT_ROOT)
        print(f"{relative}:{violation.line}: {violation.detail}", file=sys.stderr)
    return 1


def forbidden_python_imports(runtime_root: Path = RUNTIME_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    persistence_isolated_domains = (
        runtime_root / "conversation",
        runtime_root / "runtime_skills",
    )
    for path in runtime_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            violations.append(
                Violation(path, error.lineno or 1, f"cannot inspect invalid Python: {error.msg}")
            )
            continue
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                continue
            for module in modules:
                root = module.split(".", 1)[0]
                if root in HEAVY_PROVIDER_MODULES:
                    violations.append(
                        Violation(path, node.lineno, f"heavy provider SDK import {module!r}")
                    )
                if inside_any(path, persistence_isolated_domains) and (
                    module == "aiosqlite"
                    or module == "sqlite3"
                    or module.startswith("chatwaifu_runtime.persistence")
                    or (
                        isinstance(node, ast.ImportFrom)
                        and node.level > 0
                        and (module == "persistence" or module.startswith("persistence."))
                    )
                ):
                    violations.append(
                        Violation(
                            path,
                            node.lineno,
                            f"domain imports persistence implementation {module!r}; "
                            "use a repository port",
                        )
                    )
    return violations


def heavy_runtime_dependencies(pyproject: Path = RUNTIME_PYPROJECT) -> list[Violation]:
    if not pyproject.exists():
        return []
    try:
        document = cast(
            dict[str, object],
            tomllib.loads(pyproject.read_text(encoding="utf-8")),
        )
    except (tomllib.TOMLDecodeError, OSError) as error:
        return [Violation(pyproject, 1, f"cannot inspect Runtime manifest: {error}")]
    project_value = document.get("project", {})
    project = cast(dict[str, object], project_value) if isinstance(project_value, dict) else {}
    dependencies_value = project.get("dependencies", [])
    dependencies = (
        cast(list[object], dependencies_value) if isinstance(dependencies_value, list) else []
    )
    declared = {
        normalize_requirement_name(value) for value in dependencies if isinstance(value, str)
    }
    return [
        Violation(
            pyproject,
            1,
            f"Runtime declares heavy model package {package!r}; install it in a worker",
        )
        for package in sorted(declared & HEAVY_RUNTIME_PACKAGES)
    ]


def provider_urls_in_frontend(web_root: Path = WEB_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    for path in web_root.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(host in line for host in FORBIDDEN_PROVIDER_HOSTS):
                violations.append(
                    Violation(
                        path,
                        line_number,
                        "frontend contains a direct model-provider endpoint",
                    )
                )
    return violations


def provider_sdks_in_frontend(package_json: Path = WEB_PACKAGE_JSON) -> list[Violation]:
    if not package_json.exists():
        return []
    try:
        document_value: object = json.loads(package_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        return [Violation(package_json, 1, f"cannot inspect package manifest: {error}")]
    if not isinstance(document_value, dict):
        return [Violation(package_json, 1, "package manifest must contain a JSON object")]
    document = cast(dict[str, object], document_value)
    declared: set[str] = set()
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        values = document.get(section, {})
        if isinstance(values, dict):
            declared.update(str(name) for name in cast(dict[str, object], values))
    return [
        Violation(
            package_json,
            1,
            f"frontend declares provider SDK {package!r}; call the Runtime adapter instead",
        )
        for package in sorted(declared & FORBIDDEN_FRONTEND_PACKAGES)
    ]


def inside_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def normalize_requirement_name(requirement: str) -> str:
    name = re.split(r"[<>=!~; @\[]", requirement, maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower()


if __name__ == "__main__":
    raise SystemExit(main())
