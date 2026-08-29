# Web and Desktop product profiles

ChatWaifu NEXT uses one repository and one `main` branch, but produces two independently versioned
frontend graphs.

| Product | Owned surfaces | Build command | Frontend artifact | Tag |
| --- | --- | --- | --- | --- |
| Web | Galgame conversation, Avatar Lab | `make build-web` | `apps/web/dist/web` | `web-vX.Y.Z` |
| Desktop | Desktop pet, control center | `make build-desktop-ui` | `apps/web/dist/desktop` | `desktop-vX.Y.Z` |

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
installer contains neither private Live2D/voice assets nor CUDA model weights and safely falls back
when local model workers are absent.

Creating `dist\windows\installer\*.exe` is not the Desktop release gate. A `desktop-v*` artifact may
be described as installable only after a clean Windows account installs it, launches the embedded
Runtime without developer tools, exercises settings/data persistence and AppContainer execution,
exits without orphan processes, uninstalls while retaining user data, and passes license/signing
policy. Until that installed-path smoke is recorded, the output is an unsigned local installer
candidate rather than a distributable release.

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
