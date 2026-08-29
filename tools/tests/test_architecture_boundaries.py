from __future__ import annotations

import json
from pathlib import Path

from tools.check_architecture_boundaries import (
    forbidden_python_imports,
    heavy_runtime_dependencies,
    provider_sdks_in_frontend,
    provider_urls_in_frontend,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_runtime_rejects_heavy_model_sdk_imports(tmp_path: Path) -> None:
    runtime_root = tmp_path / "chatwaifu_runtime"
    _write(runtime_root / "providers" / "bad.py", "from transformers import Pipeline\n")

    violations = forbidden_python_imports(runtime_root)

    assert [item.detail for item in violations] == ["heavy provider SDK import 'transformers'"]


def test_runtime_rejects_heavy_model_packages(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "runtime"\nversion = "1"\ndependencies = ["Torch>=2", "httpx"]\n',
    )

    violations = heavy_runtime_dependencies(pyproject)

    assert [item.detail for item in violations] == [
        "Runtime declares heavy model package 'torch'; install it in a worker"
    ]


def test_selected_domains_require_repository_ports(tmp_path: Path) -> None:
    runtime_root = tmp_path / "chatwaifu_runtime"
    _write(
        runtime_root / "conversation" / "service.py",
        "from chatwaifu_runtime.persistence.database import Database\n",
    )
    _write(runtime_root / "runtime_skills" / "service.py", "import aiosqlite\n")
    _write(
        runtime_root / "runtime_skills" / "plugins.py",
        "from ..persistence.database import Database\n",
    )
    _write(
        runtime_root / "conversation" / "repository.py",
        "from typing import Protocol\n",
    )

    violations = forbidden_python_imports(runtime_root)

    assert len(violations) == 3
    assert all("use a repository port" in item.detail for item in violations)


def test_frontend_rejects_provider_hosts_but_allows_runtime_loopback(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "web"
    _write(web_root / "runtime.ts", 'const url = "http://127.0.0.1:8765";\n')
    _write(web_root / "provider.ts", 'const url = "https://api.openai.com/v1";\n')

    violations = provider_urls_in_frontend(web_root)

    assert len(violations) == 1
    assert violations[0].path.name == "provider.ts"


def test_frontend_rejects_provider_sdk_dependencies(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        json.dumps(
            {
                "dependencies": {"react": "1", "openai": "1"},
                "devDependencies": {"typescript": "1"},
            }
        ),
        encoding="utf-8",
    )

    violations = provider_sdks_in_frontend(package_json)

    assert [item.detail for item in violations] == [
        "frontend declares provider SDK 'openai'; call the Runtime adapter instead"
    ]
