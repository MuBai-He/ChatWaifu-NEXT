# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.compat import is_win
from PyInstaller.utils.hooks import collect_submodules, copy_metadata, get_package_paths


ROOT = Path.cwd().resolve()
PIPECAT_ROOT = Path(get_package_paths("pipecat")[1])
WINDOWS_RUNTIME_ICON = ROOT / "apps" / "desktop" / "src-tauri" / "icons" / "icon.ico"
WINDOWS_RUNTIME_VERSION = ROOT / "packaging" / "windows" / "runtime-version.txt"

datas = [
    (str(ROOT / "config"), "config"),
    (str(ROOT / "characters"), "characters"),
    (str(ROOT / "skills"), "skills"),
    (str(ROOT / ".local" / "nltk_data"), "nltk_data"),
    (
        str(PIPECAT_ROOT / "audio" / "vad" / "data" / "silero_vad.onnx"),
        "pipecat/audio/vad/data",
    ),
]
for distribution in ("pipecat-ai", "httpx2", "httpcore2"):
    datas += copy_metadata(distribution)

hiddenimports = []
for package in (
    "chatwaifu_runtime",
    "chatwaifu_protocol",
    "chatwaifu_model_worker",
    "uvicorn",
):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(ROOT / "tools" / "run_packaged_runtime.py")],
    pathex=[
        str(ROOT / "services" / "runtime" / "src"),
        str(ROOT / "packages" / "protocol-python" / "src"),
        str(ROOT / "packages" / "model-worker-sdk-python" / "src"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "pyinstaller-hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chatwaifu-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(WINDOWS_RUNTIME_ICON) if is_win else None,
    version=str(WINDOWS_RUNTIME_VERSION) if is_win else None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="runtime-sidecar",
)
