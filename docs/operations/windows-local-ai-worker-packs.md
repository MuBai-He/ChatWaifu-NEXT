# Windows x64 local AI Worker Packs

The base Desktop installer deliberately excludes CUDA, PyTorch, model weights, and Python model
environments. Qwen3-TTS and faster-whisper are distributed as independently versioned `.cwpack`
archives instead. Each archive contains its own relocatable x64 CPython 3.12 runtime, worker code,
native libraries, and fully materialized local model, so the target Windows account does not need
Python, `uv`, Git, or network access to start it.

The archive and installed layout follow [ADR 0028](../adr/0028-versioned-local-ai-worker-packs.md).
`tools/worker_packs.py` and `chatwaifu_model_worker.pack_installer` are the only pack contract and
installer; the Windows builders do not maintain a second archive format.

## Build requirements

- Windows x64, or Windows 11 ARM running the complete toolchain under x64 emulation.
- A repository checkout bootstrapped with `tools\windows\bootstrap_x64.ps1`; its `.venv` must report
  `win-amd64`.
- `uv` is required on the build machine only.
- Enough free space for the staging tree, archive, extracted smoke installation, and model source.
  Allow at least three times the expected final pack size.
- The Qwen smoke requires a real NVIDIA GPU whose installed driver supports the CUDA 12.6 PyTorch
  wheel. An emulated ARM VM can check the x64 build path, but cannot replace native CUDA acceptance.

Do not put a ModelScope/Hugging Face token, character checkpoint, or smoke recording in the
repository. The build scans staging and rejects common secret, database, VCS, IDE, cache, and
symlink paths.

## faster-whisper Base CPU int8

Supply a real, short PCM16 speech recording for the release smoke. Mono or stereo WAV at 8-48 kHz
is accepted. With no `-ModelSource`, the build machine downloads the revision-pinned public model,
copies every required file into the pack, removes download caches, then launches the installed pack
with all Hugging Face/Transformers offline flags enabled.

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\build_faster_whisper_worker_pack_x64.ps1 `
    -SmokeWav C:\validation\speech.wav `
    -PackVersion 0.1.0
```

To reuse an already reviewed local snapshot, add `-ModelSource C:\models\faster-whisper-base`.
The default pack is `faster-whisper==1.2.1`, Base, CPU `int8`, and local-files-only.

## Qwen3-TTS CUDA 12.6 with the local Nene checkpoint

Pass the extracted CustomVoice checkpoint directory. The builder verifies that `config.json`
declares `qwen3_tts` / `custom_voice` and contains the selected speaker before copying it. The
checkpoint is owner-only and must not be uploaded or attached to a public release without a separate
license review.

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\build_qwen3_tts_worker_pack_x64.ps1 `
    -ModelSource C:\models\nene-qwen3-tts\checkpoint-epoch-0 `
    -Voice ayachi_nene_local `
    -PackVersion 0.1.0
```

The first profile pins the official Qwen3-TTS source revision, PyTorch/torchaudio 2.7.1 `cu126`,
SDPA, and `cuda:0`. Its smoke launches the installed pack without the builder environment and
synthesizes fixed Chinese and Japanese lines. Both results must be non-silent PCM16 WAV. The
official Torch wrapper currently generates the full waveform before emitting it, so this provider
truthfully advertises `native_streaming=false` even though the worker boundary supports PCM v2.

## Outputs and acceptance

Successful builds write the immutable versioned archive and SHA-256 sidecar under
`dist\windows\worker-packs\`. Smoke logs, Qwen Chinese/Japanese WAVs, or the Whisper transcript are
written below the adjacent `smoke\` directory. Both builders also:

1. inspect every `.exe`, `.dll`, and `.pyd` as PE machine `0x8664`;
2. build and fully verify the canonical checksummed Zip64 archive;
3. install it atomically into a temporary pack root;
4. start the bundled entry point with an ephemeral loopback port and bearer token;
5. check authenticated health and capabilities, run real inference, unload it, terminate it, and
   confirm the listener closes.

`-SkipModelSmoke` exists only for diagnosing build machines. It leaves an explicitly unverified
pack and is not a release acceptance result. A release candidate still needs a clean native Windows
x64 target run; Qwen additionally needs the intended CUDA laptop and driver.

The Runtime sidecar owns installation, activation, dynamic host/port/token injection, supervision,
and restart. Pack manifests contain only their namespaced static `CHATWAIFU_STT_WORKER_*` or
`CHATWAIFU_NEURAL_TTS_WORKER_*` settings and never store endpoint credentials.

## Release distribution and user choice

Publish the base NSIS installer and each licensed Worker Pack as separate release artifacts. The
download page should list the pack's purpose, supported OS/architecture/accelerator, expanded size,
license, version, byte size, and SHA-256 sidecar. Never put an owner-only checkpoint or voice pack in
a public release merely because its `.cwpack` build passed acceptance.

Users do not need a Worker Pack to install or start ChatWaifu NEXT. They can use text chat and
configured cloud providers, skip every local model during onboarding, and add local capabilities
later from **Settings → Data → Worker Pack management → Select and install**. The native picker
accepts one `.cwpack`; the Host stops the active Runtime graph, invokes the frozen Runtime's strict
installer, fully verifies and atomically activates the archive, then starts Runtime again. Reinstall
and normal uninstall preserve installed packs as user data. The downloaded archive is not needed for
normal use after a successful install, although retaining or re-downloading that exact archive is
required for an explicit repair of the same version.

The base EXE must not silently fetch or install CUDA, PyTorch, model weights, or a voice pack. A
future signed catalog may streamline downloads, but publisher authentication, license review,
hardware compatibility, explicit user choice, progress/cancellation, and the same strict installer
remain release gates.

## Install into ChatWaifu NEXT

For normal users, install the base x64 NSIS package, open Settings → Data, and use **Select and
install**. Administrator privileges are not required because both the application and model packs
are per-user.

Release engineering and recovery can instead run the repository helper from a normal PowerShell
session. It resolves the installed Runtime from the current-user uninstall registry, proves that it
is an x64 PE, fully verifies the archive, installs it atomically, and activates that exact version.

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack

.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack
```

The Settings action restarts Runtime automatically; the repository helper still requires restarting
ChatWaifu NEXT afterward. On the next frozen-Runtime boot both selected workers start on ephemeral
authenticated loopback ports. If Whisper cannot load or Qwen's real CUDA
execution probe fails, Runtime keeps the corresponding built-in/network fallback instead of
advertising a broken local provider. Runtime routes Numba's native cache to a unique per-launch
directory outside the verified pack and removes it during orderly shutdown; the build smoke fully
re-verifies the installed tree after real inference and shutdown. This matters because `-B` and
`PYTHONDONTWRITEBYTECODE` stop CPython bytecode writes but do not stop libraries such as Numba from
creating their own cache directories.

Each `launch-*` cache carries an owner byte-range lock and a ready marker. A later Runtime reaps it
only after the old lock is provably available and exact-depth, physical-root, and reparse checks pass.
Live, unready, missing/corrupt-marker, unverifiable, symlink, junction, and other reparse entries are
preserved; shared pack/version cache namespaces are never recursively deleted. After a forced Host
exit, it is therefore normal to see the exact crash-owned launch directories briefly remain until a
subsequent Runtime performs this conservative recovery.

`-VerifyOnly` validates an archive without changing the active selection; `-RuntimePath` can target
a reviewed portable Runtime during release testing. Normal installation never overwrites an
existing version. If a full Runtime re-verification has already rejected that version, restore it
only from the exact original archive with the explicit repair path:

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack `
    -RepairInvalid
```

Repair verifies and stages the archive before moving anything, refuses to overwrite a valid pack,
refuses a different archive or damaged identity metadata, swaps the invalid directory on the same
volume, verifies the replacement, and restores the original directory if replacement verification
fails. Before invoking repair, completely exit ChatWaifu NEXT and verify that the Host, hidden
supervisor, Frozen Runtime, both Worker processes, and their listeners have stopped. Repair is not a
live-update mechanism and must fail rather than mutate a pack owned by a running product graph.
Restart ChatWaifu NEXT afterward.

## Physical per-user roots and redirected shells

Do not derive the installed-pack destination by concatenating the current process's `%APPDATA%` or
`%LOCALAPPDATA%`. A packaged terminal, IDE, or automation host can expose a Package `LocalCache`
view even while the installed Desktop product uses the physical user profile. The installer helper
resolves physical RoamingAppData and LocalAppData through `SHGetKnownFolderPath` with
`KF_FLAG_NO_PACKAGE_REDIRECTION`; the expected roots are therefore:

```text
RoamingAppData/local.chatwaifu.next/runtime
LocalAppData/local.chatwaifu.next/runtime
```

Before it reads the archive, the helper rejects Package `LocalCache` spellings, junctions, symlinks,
other reparse-point parents, and a probe whose final handle path does not exactly match the physical
Known Folder. It also refuses to leave a failed probe behind. If that check asks for a standalone
shell, close the packaged task terminal and run the same command from ordinary PowerShell or Windows
Terminal; do not bypass the check or copy the pack tree manually. Database inspection or recovery
must likewise use the physical local-drive namespace and keep each SQLite main/WAL/SHM family
together.

## 2026-09-01 native x64/CUDA acceptance record

The following owner-only artifacts were built and re-verified on native AMD64 Windows 11 with an
RTX 3090. The Qwen checkpoint and the installer's private Live2D overlay remain non-redistributable;
only the resulting measurements and hashes are recorded here.

| Artifact                                              |         Bytes | SHA-256                                                            |
| ----------------------------------------------------- | ------------: | ------------------------------------------------------------------ |
| `chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack`         | 5,443,989,887 | `af33a0f7afb105eeacd6c7a7de7071819afbf4916ba5d85a11a7817f146c00e9` |
| `chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack` |   250,542,825 | `86cf28dc4d07e32587c1be29751e11d5d682f0d461e0d808808b78d894bd4d96` |
| `ChatWaifu NEXT_0.2.0_x64-setup.exe` (owner-only)     |   128,213,645 | `9a5bd8d962d4adc32b3599ebb03762bcd8111d82a71e235f2f17c8fe39e7698b` |

Qwen used Torch `2.7.1+cu126` on `cuda:0` and verified its model tensors on the RTX 3090. The final
post-inference integrity smoke's first controlled post-load Chinese inference and subsequent warm
Japanese inference produced non-silent, unclipped 2.08-second and 2.16-second PCM16 WAVs in
15,129.724 ms and 4,665.135 ms. The first inference left 2,164,438,016 CUDA bytes allocated and
2,302,672,896 reserved. faster-whisper used the fixed model revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`, CPU `int8`, and `local_files_only=true`; it returned a
non-empty, coherent Japanese transcript for the 21.455-second smoke WAV in 862.951 ms. Both packs
passed cancellation, unload, child-process exit, and listening-port closure.

All 393 native Qwen files, all 159 native Whisper files, and the installed Host, frozen Runtime, and
AppContainer helper were PE Machine `0x8664`. The installed graph selected independent ephemeral
ports for Runtime and both Workers. Historical two-pack cold boots reached ready in about 151 seconds
and, on a later full-verification boot, about 443 seconds. Runtime startup now reads and validates the
receipt/manifest identity and hashes the selected entrypoint, but does not stat or hash every declared
payload. Use **设置 → 数据 → Worker Pack 完整性 → 开始完整校验** whenever a complete on-disk audit is
needed; it verifies the exact tree, every SHA-256, reparse rejection, and PE architecture in a Runtime
worker thread. Installation, activation, repair, and release smoke also continue to require that full
check. The 455-second Desktop wait remains a readiness bound for genuinely slow model initialization,
not a reason to add `Start-Sleep`.

### Evidence boundary and remaining release work

- Automated evidence covers hashes, archive/manifest verification, offline materialization, PE
  architecture, CUDA tensor placement and memory, waveform structure/levels, Whisper transcript
  non-emptiness, authenticated protocol calls, cancellation/unload, and process/port cleanup.
- Actual foreground-window observation covers Ningning rendering/animation, transparency, settings
  layout/scrolling, Runtime/provider ready state, progressive text chat, and retained memory across
  reinstall/restart. These are not inferred from a successful build.
- Speaker playback and objective WAV checks do not constitute human-ear approval. Voice identity,
  pronunciation, speed, clipping perception, and reference-sample similarity require an explicit
  human listening record. One installed 2,920 ms / 93,440-byte microphone utterance completed VAD
  and returned a real faster-whisper transcript, but recognition quality was poor; the user did not
  speak during the final playback-time interruption attempt, so voice barge-in remains unaccepted.
- The owner-only NSIS artifact is neither signed nor publishable. Installed AppContainer/MCP
  execution and uninstall profile/owned-ACL reconciliation remain pending.
- The exercised pre-fix candidate exposed stale `HKCU\Software\MuBai\ChatWaifu NEXT` manufacturer
  metadata after data-preserving uninstall. The rebuilt final candidate's post-uninstall hook passed
  a native real-machine replay: both standard uninstall registry views, Start Menu/Desktop shortcuts,
  the immutable product tree, and manufacturer product metadata were clear afterward, while AppData,
  local-AI selection, and both Worker Packs remained. Installed AppContainer profile/owned-ACL
  reconciliation is still separate; do not equate this NSIS result with that security-state gate.
