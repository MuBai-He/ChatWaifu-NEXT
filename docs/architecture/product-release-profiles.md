# Web and Desktop product profiles

ChatWaifu NEXT uses one repository and one `main` branch, but produces two independently versioned
frontend graphs.

| Product | Owned surfaces                   | Build command           | Frontend artifact       | Tag              |
| ------- | -------------------------------- | ----------------------- | ----------------------- | ---------------- |
| Web     | Galgame conversation, Avatar Lab | `make build-web`        | `apps/web/dist/web`     | `web-vX.Y.Z`     |
| Desktop | Desktop pet, control center      | `make build-desktop-ui` | `apps/web/dist/desktop` | `desktop-vX.Y.Z` |

Both artifacts contain shared conversation, Runtime-client, Live2D, voice, memory, and settings
modules only when their owned surface needs them. `chatwaifu-product.json` records the actual source
modules in each bundle. Verify it after a build:

```bash
uv run python tools/verify_product_artifacts.py --product web
uv run python tools/verify_product_artifacts.py --product desktop
```

The canonical product versions are in `release/products.json`. Update one release train without
changing the other:

```bash
uv run python tools/product_release.py set-version --product web --version 0.2.1
uv run python tools/product_release.py verify --product web --tag web-v0.2.1
```

The updater synchronizes the required package/Tauri/Cargo mirrors. Runtime, protocol, and worker
component versions remain independent.

Tauri development and builds always invoke the Desktop Vite profile and consume
`apps/web/dist/desktop`. The ordinary `build` command remains the unsigned no-bundle developer host.
On Windows, an x64 installer candidate is assembled separately from a `win-amd64` Python environment:

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\build_installer_x64.ps1
```

The packaging command freezes Runtime as PyInstaller onedir, stages the x64 AppContainer helper,
builds the Desktop profile, creates an NSIS current-user installer, verifies all three shipped PE
executables as machine `0x8664`, smoke-starts the frozen Runtime, and copies the candidate plus its
SHA-256 to `dist\windows\installer\`. It does not require or include a source checkout on the target
machine. ADR 0027 is authoritative for the installed resource and user-data layout.

An owner-only build can explicitly overlay ignored local Live2D assets:

```powershell
.\tools\windows\build_installer_x64.ps1 -Live2DSource "C:\path\to\private\live2d"
```

That candidate is private and must not be uploaded to CI, a tag, or a public release. The base
installer contains neither private Live2D/voice assets nor a CUDA runtime, PyTorch environment,
local Qwen/GPT-SoVITS TTS or faster-whisper workers, or their model weights. It safely falls back
when local model workers are absent; those capabilities require a separately versioned Worker/Model
Pack or a future user-approved Model Manager download.

Creating `dist\windows\installer\*.exe` is not the Desktop release gate. A `desktop-v*` artifact may
be described as installable only after a clean Windows account installs it, launches the embedded
Runtime without developer tools, exercises settings/data persistence and AppContainer execution,
exits without orphan processes, uninstalls while retaining user data, and passes license/signing
policy. The recorded owner-only smokes below cover only subsets of that matrix, so their outputs
remain unsigned local installer candidates rather than distributable releases.

### Automated basic installed smoke

On a Windows test account with no existing ChatWaifu installation or process, run:

```powershell
$installer = Get-ChildItem .\dist\windows\installer\*-setup.exe | Select-Object -First 1
.\tools\windows\smoke_installed_x64.ps1 -InstallerPath $installer.FullName
```

This smoke performs a real current-user install, validates the registry entry, Start Menu shortcut,
installed resources, x64 Host/Runtime/helper, Runtime identity and health, Tauri user roots and
SQLite, forces the Host to prove Runtime/listener cleanup, uninstalls, and verifies removal of the
immutable product plus retention of config/data/log roots and test-owned markers. It is destructive
to the test installation and deliberately refuses to run when a ChatWaifu install or process already
exists.

An unsigned owner-only private-overlay candidate passed this smoke on 2026-08-30 in a Windows 11 ARM
VM using x64 emulation. The automated smoke is not a clean-account product acceptance and does not
exercise foreground UI/chat/settings/memory/audio, normal exit, reinstall/update, installed
AppContainer/MCP execution or profile/ACL cleanup, native x64/CUDA hardware, licensing, signing, or
publication. Those remain part of the full run below.

### Native Windows x64/CUDA owner-only record

On 2026-09-01 an owner-only private-overlay candidate was built, installed, and exercised on native
AMD64 Windows 11 Pro 25H2 build 26200.9168 with an RTX 3090. The final rebuilt candidate was
`ChatWaifu NEXT_0.2.0_x64-setup.exe`, 128,195,690 bytes, SHA-256
`9e699509510241afd574fad6105d60250f9174b85cb78833a83035bad4e549f3`. It remains a local artifact
because it contains an explicitly supplied private Live2D overlay and is unsigned.

Automated inspection proved the Host, frozen Runtime, helper, and all inspected bundled EXE/DLL/PYD
files were PE Machine `0x8664`. The current-user install required no elevation, resolved the physical
LocalAppData/RoamingAppData Known Folders, created the Start Menu shortcut, started Runtime and two
Worker Packs on dynamic authenticated loopback ports, and reused retained settings, SQLite memory,
pack receipts, and activation selection after reinstall. The Worker Pack helper now explicitly
rejects Package `LocalCache`, reparse, or final-handle path redirection instead of trusting a
packaged caller's environment variables.

The independently installed pack artifacts were:

| Pack                                                  |         Bytes | SHA-256                                                            |
| ----------------------------------------------------- | ------------: | ------------------------------------------------------------------ |
| `chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack`         | 5,443,989,887 | `af33a0f7afb105eeacd6c7a7de7071819afbf4916ba5d85a11a7817f146c00e9` |
| `chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack` |   250,542,825 | `86cf28dc4d07e32587c1be29751e11d5d682f0d461e0d808808b78d894bd4d96` |

Torch `2.7.1+cu126` reported CUDA 12.6 available and ran Qwen tensors on `cuda:0`. The pack generated
non-silent, unclipped Chinese and Japanese WAVs; faster-whisper produced a non-empty, coherent
Japanese CPU-`int8` transcript with its fixed revision and fully offline model directory. Both pack
smokes covered cancellation, unload, process exit, and port closure. One installed two-pack cold
start took about 151 seconds, so the Web resolver was corrected from 125 seconds to 455 seconds,
beyond the native bounded startup window of 300 seconds for Workers, 120 seconds for Runtime, and
30 seconds of supervisor grace.

Actual Tauri-window observation separately confirmed the private Ningning model rendered and
animated over the transparent pet window, settings opened and scrolled, local providers reached
ready, text responses updated progressively, and a persisted memory survived reinstall/restart.
This is stronger evidence than build success, but it does not make automated waveform statistics or
speaker playback a human-ear judgment. Human voice/reference comparison and microphone/VAD evidence
must be labeled separately in a final acceptance report.

This run still leaves release gates open. The exercised pre-fix candidate exposed stale
`HKCU\Software\MuBai\ChatWaifu NEXT` manufacturer metadata after data-preserving uninstall. The
rebuilt final candidate's narrowly scoped post-uninstall hook then passed a native real-machine
replay: both standard uninstall registry views, Start Menu/Desktop shortcuts, the immutable product
tree, and manufacturer metadata were clear afterward, while AppData, local-AI selection, and both
Worker Packs remained. Installed AppContainer/MCP execution and profile/owned-ACL reconciliation
remain unproved. Licensing, notices, executable/installer signing, clean-account base-candidate
testing, and remote publication remain mandatory before a `desktop-v*` artifact may be described as
distributable.

### Windows installer acceptance run

Use a clean Windows account or disposable VM and the base candidate without a private Live2D
overlay. A release acceptance run must not use `-SkipChecks`.

1. Confirm Python, uv, pnpm, the source checkout, and prior ChatWaifu listeners are not supplying the
   application. Install the NSIS candidate as the current user without elevation.
2. Launch from the Start menu. Confirm the pet and control center open, Runtime reaches ready on a
   dynamic loopback port, and the Runtime log is under the Tauri per-user log directory rather than
   the install directory.
3. Complete one Demo or configured-cloud chat turn, save one non-secret setting and one test memory,
   then restart. Confirm SQLite/FTS5, setting, memory, and generated-audio writes are under Tauri's
   config/local-data roots and survive restart.
4. Install or enable the Local Echo Python MCP example and run its read operation through the
   installed AppContainer helper. Confirm the frozen executable uses `--plugin-python` rather than
   starting a second Runtime. Run the applicable ADR 0025 installed-layout probes.
5. Exit normally, then repeat with a forced host exit. Confirm no desktop host, Runtime, MCP child,
   or listening port remains after the bounded shutdown window.
6. Uninstall from Windows Apps. Confirm the installation/resource tree and shortcuts are removed,
   ChatWaifu-owned AppContainer profiles/ACL entries are reconciled, and unrelated ACL entries plus
   per-user config/data/model/log roots remain.
7. Reinstall the same or next allowed version. Confirm the retained setting and test memory reopen
   without a second data root or migration error; remove the test record through the product UI.

Record the installer SHA-256, Windows version, native CPU architecture, host/Runtime/helper PE
machine values, WebView2 version, installed resource root, per-user data roots, and every observed
failure. An x64-emulated Windows-on-ARM pass is useful compatibility evidence but does not replace a
native Windows x64/CUDA-laptop run.
