from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_rust_ci_builds_the_real_tauri_application_on_every_platform() -> None:
    workflow = (ROOT / ".github/workflows/ci-rust.yml").read_text(encoding="utf-8")

    assert "pnpm --filter @chatwaifu/desktop build\n" in workflow
    assert "pnpm --filter @chatwaifu/desktop build:windows-x64" in workflow
    assert "uv sync --all-packages --all-groups --locked" in workflow


def test_browser_ci_owns_a_deterministic_runtime_smoke() -> None:
    workflow = (ROOT / ".github/workflows/ci-e2e-fake.yml").read_text(encoding="utf-8")

    assert 'CHATWAIFU_E2E_RUNTIME: "1"' in workflow
    assert "uv run python tools/run_runtime.py" in workflow
    assert "CHATWAIFU_LLM__PROVIDER=demo" in workflow
    assert "CHATWAIFU_TTS__PROVIDER=fake" in workflow
    assert "Stop deterministic Runtime" in workflow
