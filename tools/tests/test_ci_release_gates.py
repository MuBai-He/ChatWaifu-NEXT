from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rust_ci_builds_the_real_tauri_application_on_every_platform() -> None:
    workflow = (ROOT / ".github/workflows/ci-rust.yml").read_text(encoding="utf-8")

    assert "pnpm --filter @chatwaifu/desktop build\n" in workflow
    assert "pnpm --filter @chatwaifu/desktop build:windows-x64" in workflow
    assert "uv sync --all-packages --all-groups --locked" in workflow
    assert "uv build --package chatwaifu-runtime" in workflow
    assert "tools/verify_product_artifacts.py --product desktop" in workflow
    assert "branches: [main]" in workflow
    assert '"apps/desktop/**"' in workflow
    assert '"apps/web/**"' in workflow
    assert '"services/runtime/**"' in workflow


def test_web_ci_builds_only_the_web_product_graph() -> None:
    workflow = (ROOT / ".github/workflows/ci-web.yml").read_text(encoding="utf-8")

    assert "pnpm --filter @chatwaifu/web build:web" in workflow
    assert "tools/verify_product_artifacts.py --product web" in workflow
    assert "@chatwaifu/desktop" not in workflow
    assert "cargo " not in workflow
    assert "branches: [main]" in workflow
    assert '"apps/web/**"' in workflow
    assert '"apps/desktop/**"' not in workflow


def test_browser_ci_owns_a_deterministic_runtime_smoke() -> None:
    workflow = (ROOT / ".github/workflows/ci-e2e-fake.yml").read_text(encoding="utf-8")

    assert 'CHATWAIFU_E2E_RUNTIME: "1"' in workflow
    assert "uv run python tools/run_runtime.py" in workflow
    assert "CHATWAIFU_LLM__PROVIDER=demo" in workflow
    assert "CHATWAIFU_TTS__PROVIDER=fake" in workflow
    assert "Stop deterministic Runtime" in workflow


def test_product_tag_workflows_are_independent_and_not_path_filtered() -> None:
    web = (ROOT / ".github/workflows/release-web.yml").read_text(encoding="utf-8")
    desktop = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")

    assert '"web-v*"' in web
    assert "verify --product web --tag" in web
    assert "build:web" in web
    assert "gh release create" in web
    assert "paths:" not in web

    assert '"desktop-v*"' in desktop
    assert "workflow_dispatch:" in desktop
    assert "desktop-components:\n    if: github.event_name == 'push'" in desktop
    assert "verify --product desktop --tag" in desktop
    assert "@chatwaifu/desktop build" in desktop
    assert "uv build --package chatwaifu-runtime" in desktop
    assert "gh release create" not in desktop
    assert "Desktop candidate only" in desktop
    assert "paths:" not in desktop


def test_desktop_candidate_builds_a_self_contained_unsigned_windows_x64_installer() -> None:
    workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "target: x86_64-pc-windows-msvc" in workflow
    assert "tools/windows/bootstrap_x64.ps1 -RecreateEnvironment" in workflow
    assert 'if ($pythonPlatform -ne "win-amd64")' in workflow
    assert '$rustTargets -notcontains "x86_64-pc-windows-msvc"' in workflow
    assert "tools/windows/build_installer_x64.ps1" in workflow
    assert "Build and smoke frozen Runtime plus unsigned NSIS installer" in workflow
    assert 'Get-ChildItem -Path "dist/windows/installer" -Filter "*-setup.exe"' in workflow
    assert "Get-FileHash -Path $installer.FullName -Algorithm SHA256" in workflow
    assert "Upload unsigned Windows x64 installer" in workflow
    assert "${{ env.WINDOWS_INSTALLER_SHA256 }}" in workflow
    assert "gh release create" not in workflow


def test_desktop_candidate_rejects_private_character_and_model_overlays() -> None:
    workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")

    assert "Reject private Live2D and local model overlays" in workflow
    assert '"apps/web/public/vendor/live2d"' in workflow
    assert '".local/live2d"' in workflow
    assert '".local/models"' in workflow
    assert '".local/training"' in workflow
    assert '".local/vendors"' in workflow
    assert "-Live2DSource" not in workflow
