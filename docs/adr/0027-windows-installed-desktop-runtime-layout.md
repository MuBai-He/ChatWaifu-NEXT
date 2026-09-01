# ADR 0027: Windows x64 installed Desktop layout and local-data boundary

- Status: Accepted
- Date: 2026-08-30
- Validation state: Unsigned owner-only native Windows x64 installed-path, foreground-window, and data-preserving NSIS uninstall replay passed on 2026-09-01; public release, signing, and installed AppContainer profile/ACL reconciliation remain pending

## Context

ADR 0005 keeps the Tauri host thin, ADR 0025 adds a trusted Windows AppContainer launcher, and ADR
0026 gives Desktop its own release profile. The Windows development path can already run the x64
host and a source-tree Runtime, but an installed application cannot depend on `uv`, pnpm, a checkout,
compile-time repository paths, or mutable files beside the executable.

The release also has two different classes of content. Runtime code, built-in character definitions,
built-in Skills, and pinned language resources are immutable product resources. Conversations,
memory, provider configuration, secrets, generated audio, installed plugins, model caches, and logs
are user-owned mutable data. Mixing the two would make current-user installation, updates,
AppContainer grants, backup, and uninstall behavior unsafe.

The local Ningning Live2D model, game-derived voice data, trained checkpoints, and cloned voices are
private research assets without reviewed redistribution permission. CUDA model weights are also
large, independently versioned, and machine-specific. A useful base installer must therefore work
without silently redistributing either class of asset.

## Decision

### Installer and target

The first Windows installer is an x64 NSIS executable built on Windows with the Rust target
`x86_64-pc-windows-msvc` and a `win-amd64` Python environment. It uses NSIS `currentUser` mode,
does not require administrator elevation, and rejects downgrades. The WebView2 download bootstrapper
is the first-release prerequisite strategy, so first installation may require network access when a
suitable WebView2 Runtime is absent.

MSI is not the first supported delivery artifact. A future MSI may be added for managed deployment
only if it preserves this layout and data policy, pins a stable WiX `upgradeCode`, and passes the same
installed-path and uninstall suite. Producing an MSI file is not allowed to weaken the NSIS gate or
make MSI the implicit definition of a complete Windows build.

### Installed resource layout

`$RESOURCE` means the directory returned by Tauri's `app.path().resource_dir()` for the installed
application. Rust resolves it at runtime; no code may assume a checkout, `Program Files`, a current
working directory, or a literal LocalAppData path.

The v1 layout is:

```text
$RESOURCE/
  runtime-sidecar/
    chatwaifu-runtime.exe
    _internal/
      config/
      characters/
      skills/
      nltk_data/
      ... frozen Python modules and native libraries ...
  bin/
    chatwaifu-appcontainer-host.exe
```

The Desktop frontend is embedded by the ordinary Tauri `frontendDist` pipeline. The Runtime is a
PyInstaller `onedir` tree mapped recursively through Tauri `bundle.resources`. The AppContainer
helper is built and architecture-checked from the target-suffixed staging file
`chatwaifu-appcontainer-host-x86_64-pc-windows-msvc.exe`, then mapped to the fixed installed name
shown above. The target suffix is a staging convention and is not part of the Runtime/helper lookup
contract.

This release deliberately uses `resources` rather than `externalBin` for both native components.
That gives the Runtime executable and all of its onedir support files one explicit tree and gives the
Rust release resolver one platform-neutral, testable path contract. Tauri `externalBin` remains an
available future mechanism for a standalone sidecar, but it must not be mixed into this layout
without a superseding ADR and installed-path migration tests.

Release builds strictly validate both fixed paths. They never fall back to `uv`, system Python, the
source-tree launcher, or a PATH lookup when a packaged component is absent. The development build
may continue to use the repository launcher. Missing, wrong-architecture, or unreadable release
resources place Runtime in an actionable offline/failure state rather than silently changing the
security or provider boundary.

### Frozen Runtime contract

The Runtime is frozen as `onedir`, not `onefile`. NSIS still presents one installer to the user, while
onedir avoids extracting a large Python graph into a temporary directory on every launch and keeps
the interpreter/native-library roots stable for AppContainer read/execute grants.

The frozen tree includes only the Runtime dependencies and immutable resources required by the base
Desktop product. Its build specification explicitly collects Pipecat VAD data, the pinned local
NLTK tokenizer data, package metadata, dynamic imports, and native libraries. Startup binds a free
loopback port, emits the existing versioned bootstrap record on stdout, sends diagnostics to stderr,
and watches the owning Tauri PID. Stdout remains a protocol boundary and must not contain secrets.

Python stdio MCP entrypoints use the frozen executable's explicit `--plugin-python` role. They do not
treat `sys.executable` as a fresh Runtime startup command. Required-sandbox entrypoints execute under
the installed AppContainer helper and receive read/execute access only to the installed Runtime and
plugin package roots plus write access to their dedicated data root.

The base frozen Runtime truthfully starts with local STT disabled and deterministic/fake TTS when no
separately installed worker is available. Demo and configured cloud/OpenAI-compatible providers
remain usable. It does not pretend that CUDA acceleration or a local neural voice worker is present.

### Immutable and mutable paths

Tauri is the authority for writable roots and passes them to Runtime before importing provider or
Pipecat modules:

| Data class                                                                                   | Root                         | Policy                                            |
| -------------------------------------------------------------------------------------------- | ---------------------------- | ------------------------------------------------- |
| Product code, built-in characters/Skills, NLTK and VAD data                                  | `$RESOURCE/runtime-sidecar`  | Immutable; replaced only by an application update |
| Runtime/provider configuration and write-only secret stores                                  | `app_config_dir/runtime`     | Per-user, mutable, never packaged                 |
| SQLite, generated audio, installed plugin packages/data/trash, future model manifests/caches | `app_local_data_dir/runtime` | Per-user, mutable, never written into `$RESOURCE` |
| Runtime sidecar diagnostics                                                                  | `app_log_dir`                | Per-user, bounded and rotated                     |
| WebView profile/cache                                                                        | Tauri platform path          | Owned by Tauri, not by Runtime                    |

The packaging staging area must reject `.env`, `.local`, database/WAL files, generated audio, provider
tokens, downloaded user plugins, training data, and model caches. Default configuration is immutable
input; saved settings and migrations always target the user directories.

### Models and private character assets

The base installer contains no CUDA runtime, PyTorch environment, Qwen/GPT-SoVITS/faster-whisper
weights, trained Ningning checkpoints, voice-clone reference audio, or game-derived data. ADR 0028
defines the independently versioned, checksummed Worker Pack path now used for owner-installed local
Qwen3-TTS and faster-whisper assets. Removing or updating the base app must not destroy that
user-owned cache.

The redistributable base also contains no private Ningning Live2D/Core assets. Missing vendor assets
use the deterministic safe avatar fallback. The Windows build may accept an explicit ignored
`Live2DSource` overlay for a local owner-only test. Such an output is private, must not enter Git,
CI artifacts, tags, or public releases, and does not satisfy the asset-license gate.

### Update and uninstall behavior

NSIS installs and updates application resources only. Uninstall removes the installed application,
shortcuts, and immutable resource tree but deliberately retains `app_config_dir`,
`app_local_data_dir`, model caches, memories, and logs. Destructive personal-data removal belongs to
an explicit in-product action with scope disclosure and confirmation, not to a silent uninstaller.

Before release acceptance, uninstall must also reconcile and revoke AppContainer profiles and owned
ACL entries defined by ADR 0025 without deleting plugin data or unrelated ACL entries. A later
"remove my data" installer option would be a separate explicit feature and must default off.

### Release acceptance

The Windows build stages from a clean checkout, freezes Runtime with x64 Python, builds the x64 Rust
host/helper, assembles Tauri resources, and produces the NSIS installer. The build verifies PE
machine `0x8664` for the host, Runtime, and helper and records the installer SHA-256.

An artifact is only an installer candidate until a clean current-user installation proves:

- startup succeeds without Python, uv, pnpm, source files, or a writable install directory;
- Runtime health, Demo/cloud chat, settings persistence, SQLite/FTS5, generated audio, and safe avatar
  fallback work from installed paths;
- the installed helper completes the AppContainer smoke and a Python MCP entrypoint does not recurse;
- config, data, and logs appear only in their Tauri per-user roots;
- normal exit, forced host exit, and uninstall leave no Runtime, plugin, or listener orphan;
- uninstall removes immutable files, retains user data, and revokes owned AppContainer state; and
- a reinstall/update reuses the retained data without a schema or path split.

### Observed owner-only basic installed smoke

On 2026-08-30 an unsigned owner-only candidate with an explicit private Live2D overlay passed the
automated basic installed smoke on a Windows 11 ARM VM while the Host and frozen Runtime ran as x64
under Windows emulation; the installed helper was present and independently verified as x64 but was
not executed. The run proved current-user registration and installation below `LOCALAPPDATA`, the
Start Menu shortcut target, installed resource presence, `0x8664` PE headers for the Host, Runtime,
and helper, branded Runtime `VERSIONINFO`, dynamic-loopback
Runtime health with a ready database, Tauri-owned config/data/log roots and SQLite creation, Runtime
and listener cleanup after forced Host termination, removal of the registry entry, shortcut, and
immutable install tree during uninstall, and byte-identical retention of test-owned config/data
markers plus the per-user roots.

That result is compatibility evidence for the packaged path, not completion of the release list
above. It did not prove a clean account without a checkout/toolchain, foreground product UX,
Demo/cloud conversation, settings/memory/audio persistence, normal exit, reinstall/update reuse, or
execution and profile/ACL reconciliation of the installed AppContainer helper. It also does not
replace native Windows x64/CUDA-laptop validation, asset/license review, executable and installer
signing, or the remote release gate. Because the candidate contains a private overlay, it remains a
local owner-only artifact and cannot be redistributed.

### Observed native Windows x64 owner-only acceptance

On 2026-09-01 the owner-only path was repeated on native AMD64 Windows 11 Pro 25H2 build
26200.9168 with an NVIDIA RTX 3090. The private-overlay NSIS candidate was
`ChatWaifu NEXT_0.2.0_x64-setup.exe`, 128,212,119 bytes, with SHA-256
`ba50b28735a7c67e57be0646568b406e18982846349ef0b73a11f99a16f9d53f`. That pre-fix candidate
exposed stale NSIS manufacturer metadata during uninstall. The rebuilt final candidate containing the
post-uninstall correction, playback-handoff fix, single-instance guard, Live2D render-scale
compensation, and directory-staged AppContainer helper is 128,213,645 bytes with SHA-256
`9a5bd8d962d4adc32b3599ebb03762bcd8111d82a71e235f2f17c8fe39e7698b`. This hash identifies
only a local owner candidate; it does not make its private overlay redistributable.

Automated artifact and installed-path inspection proved a `win-amd64` frozen Python Runtime, the
`x86_64-pc-windows-msvc` Tauri target, PE Machine `0x8664` for the installed Host, Runtime, and
AppContainer helper, 294 installed native product files, and all 552 independently installed Worker
Pack EXE/DLL/PYD files. The only observed `0x014c` PE is Tauri/NSIS's generated `uninstall.exe` stub;
it is not the Host, Runtime, helper, or a Python/Worker payload. A current-user install
placed immutable resources under the physical LocalAppData installation root and created the
Start Menu shortcut without elevation. Runtime and both selected Worker Packs bound independently
assigned loopback ports; one recorded installed boot used Runtime port 12557 and Worker ports 14351
and 14353, but those values are observations rather than configuration or stable API.

Repeated Start Menu activation of the final candidate was also exercised while the first installed
graph was running. The Tauri single-instance callback made the existing avatar visible and left
exactly one product Host, one supervisor, and one Runtime; it did not create a second service graph.
The supervisor intentionally uses the same Host executable with an explicit internal role argument
and is not a second application instance.

Writable roots were verified against the physical Windows Known Folders rather than trusting a
possibly package-redirected environment spelling:

```text
LocalAppData/ChatWaifu NEXT
RoamingAppData/local.chatwaifu.next/runtime
LocalAppData/local.chatwaifu.next/runtime
```

The offline pack installer now resolves RoamingAppData and LocalAppData with
`KF_FLAG_NO_PACKAGE_REDIRECTION`, rejects Package `LocalCache` spellings and reparse-point parents,
and checks a temporary file's final handle path before verifying or activating a pack. Database
recovery applies the same fail-closed namespace principle to UNC inputs, hard-link aliases, SQLite
sidecars, and Package `LocalCache` layers. These checks prevent a packaged shell or tool host from
silently splitting settings, selection, model receipts, or SQLite state across two Windows
namespaces.

A real foreground Tauri-window observation, not a build inference, confirmed the private Ningning
Live2D model rendered and animated over the transparent pet surface, settings opened and scrolled,
Runtime and the selected local providers reached ready, text chat updated progressively, and a
stored memory survived reinstall and restart. The installed CUDA graph needed about 151 seconds on
one cold boot and about 443 seconds on a later complete-pack verification boot under the former
always-rehash policy. Native supervision
intentionally allows 300 seconds for selected Workers, 120 seconds
for the Runtime server, and 30 seconds of supervisor grace; the Web resolver now waits 455 seconds,
five seconds beyond that complete 450-second native bound, instead of abandoning a healthy CUDA
start at the former 125-second cutoff.

The 443-second path was within the contract but not an acceptable steady-state user experience. ADR
0028 now assigns ordinary startup a bounded receipt/manifest plus entrypoint check and assigns the
complete exact-tree/hash/PE audit to an explicit Data-settings operation. Installation, activation,
repair, and release smoke remain fail-closed and fully verified; the startup change does not weaken
those artifact gates or replace authenticated readiness with a delay.

Automated process inspection proved final-candidate forced Host termination removed the complete
15-process descendant tree (Host, WebView2, supervisor, Runtime, both Workers, and console helpers)
and all three listeners. Reinstall preserved the physical per-user database, model-pack
receipts, activation selection, and the test memory. The recorded foreground observation does not
convert objective WAV checks into a human listening result: human-ear sound-quality judgment and
the microphone/VAD path must be reported separately from automated waveform, GPU, protocol, and
window evidence.

The same forced-exit run intentionally left two exact per-launch Worker cache directories after the
Windows Job terminated Python without `finally`. On the next Runtime boot both were reclaimed by
their released byte-range owner locks before new leases were created. Live, unready, malformed,
unverifiable, and reparse cache entries remain fail-closed; no shared pack/version namespace is
recursively removed. This recovery belongs to Runtime, not the Tauri Host, and never changes the
immutable Worker Pack install tree.

This native run is still not the public Desktop release gate. The first data-preserving uninstall
exposed that Tauri's generated NSIS flow left `HKCU\Software\MuBai\ChatWaifu NEXT` pointing at the
removed installation. A repository-owned `NSIS_HOOK_POSTUNINSTALL` now deletes only that installer
metadata on a real uninstall, leaves AppData and Worker Packs untouched, and source/smoke tests cover
both standard registry views plus Start Menu and Desktop shortcuts. The rebuilt final candidate was
then installed, reached dynamic Runtime health, survived the forced-exit cleanup check, and uninstalled
on the native machine: the immutable product tree, both shortcuts, uninstall entries, and manufacturer
metadata were absent afterward, while settings/data roots, pack selection, and both Worker Packs
remained. Installed AppContainer/MCP execution and uninstall-time profile/owned-ACL reconciliation
remain unproved, as do licensing, third-party notices,
signatures, and public-release automation. None of those gaps may be hidden by the successful
owner-only UI, CUDA, or process-cleanup evidence.

Unsigned local candidates are permitted for owner testing and will carry the expected Windows trust
warning. A public `desktop-v*` release additionally requires a selected project license, reviewed
third-party notices and assets, signatures on every shipped executable/library and the installer,
and the remote release gate. A successful build alone is not evidence of any of those conditions.

## Consequences

The installed application has one deterministic resource contract and cannot accidentally import a
developer checkout or write beside its executable. NSIS remains a single user-facing installer even
though the Runtime uses the operationally safer onedir form. Updates and uninstall preserve user
memory and model investment, while AppContainer cleanup remains independently auditable.

The base installer will be smaller and license-safe but will not provide local CUDA voice or STT by
itself. Users who need those capabilities require a separately managed worker/model pack. Current-
user installation also means each Windows account owns its own app and data.

## Alternatives

Use PyInstaller `onefile`; copy an entire development `.venv`; require a system Python or uv; place
the Runtime database under the installation directory; package model weights and private Live2D
assets in the base installer; delete all user data during ordinary uninstall; use a per-machine
elevated installer; or expose both `externalBin` and resource copies of the same executable. These
options increase startup cost, architecture drift, privilege, accidental data loss, license risk, or
path ambiguity.

## References

- [ADR 0005: React renderer with a thin Tauri host](0005-react-tauri-live2d.md)
- [ADR 0025: Windows AppContainer Runtime Skill launcher](0025-windows-appcontainer-runtime-skill-launcher.md)
- [ADR 0026: Monorepo product release profiles](0026-monorepo-product-release-profiles.md)
- [ADR 0028: Versioned local AI Worker Packs](0028-versioned-local-ai-worker-packs.md)
- [Tauri Windows installers](https://v2.tauri.app/distribute/windows-installer/)
- [Tauri application resources](https://v2.tauri.app/develop/resources/)
- [Tauri application paths](https://v2.tauri.app/reference/javascript/api/namespacepath/)
