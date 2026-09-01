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

## Install into ChatWaifu NEXT

Install the base x64 NSIS package first. Then run the repository helper from a normal PowerShell
session; administrator privileges are not required because both the application and model packs are
per-user. The helper resolves the installed Runtime from the current-user uninstall registry,
proves that it is an x64 PE, fully verifies the archive, installs it atomically, and activates that
exact version.

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack

.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack
```

Restart ChatWaifu NEXT after installation. On the next frozen-Runtime boot both selected workers
start on ephemeral authenticated loopback ports. If Whisper cannot load or Qwen's real CUDA
execution probe fails, Runtime keeps the corresponding built-in/network fallback instead of
advertising a broken local provider. `-VerifyOnly` validates an archive without changing the active
selection; `-RuntimePath` can target a reviewed portable Runtime during release testing.

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
| `ChatWaifu NEXT_0.2.0_x64-setup.exe` (owner-only)     |   128,195,690 | `9e699509510241afd574fad6105d60250f9174b85cb78833a83035bad4e549f3` |

Qwen used Torch `2.7.1+cu126` on `cuda:0` and verified its model tensors on the RTX 3090. The direct
Worker smoke's first controlled post-load Chinese inference and subsequent warm Japanese inference
produced non-silent, unclipped 1.84-second and 1.92-second PCM16 WAVs in 14,691.977 ms and
4,471.212 ms. The first inference left 2,164,438,016 CUDA bytes allocated and
2,302,672,896 reserved. faster-whisper used the fixed model revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`, CPU `int8`, and `local_files_only=true`; it returned a
non-empty, coherent Japanese transcript for the 21.455-second smoke WAV in 876.589 ms. Both packs
passed cancellation, unload, child-process exit, and listening-port closure.

All 393 native Qwen files, all 159 native Whisper files, and the installed Host, frozen Runtime, and
AppContainer helper were PE Machine `0x8664`. The installed graph selected independent ephemeral
ports for Runtime and both Workers. A two-pack cold boot reached ready in about 151 seconds, beyond
the former 125-second frontend cutoff but within the native 300-second Worker + 120-second Runtime +
30-second supervisor bound. Desktop Web resolution now waits 455 seconds, five seconds longer than
that full 450-second native window. Do not replace health/capability readiness with `Start-Sleep`.

### Evidence boundary and remaining release work

- Automated evidence covers hashes, archive/manifest verification, offline materialization, PE
  architecture, CUDA tensor placement and memory, waveform structure/levels, Whisper transcript
  non-emptiness, authenticated protocol calls, cancellation/unload, and process/port cleanup.
- Actual foreground-window observation covers Ningning rendering/animation, transparency, settings
  layout/scrolling, Runtime/provider ready state, progressive text chat, and retained memory across
  reinstall/restart. These are not inferred from a successful build.
- Speaker playback and objective WAV checks do not constitute human-ear approval. Voice identity,
  pronunciation, speed, clipping perception, and reference-sample similarity require an explicit
  human listening record; microphone/VAD acceptance is also tracked separately.
- The owner-only NSIS artifact is neither signed nor publishable. Installed AppContainer/MCP
  execution and uninstall profile/owned-ACL reconciliation remain pending.
- The exercised pre-fix candidate exposed stale `HKCU\Software\MuBai\ChatWaifu NEXT` manufacturer
  metadata after data-preserving uninstall. The rebuilt final candidate's post-uninstall hook passed
  a native real-machine replay: both standard uninstall registry views, Start Menu/Desktop shortcuts,
  the immutable product tree, and manufacturer product metadata were clear afterward, while AppData,
  local-AI selection, and both Worker Packs remained. Installed AppContainer profile/owned-ACL
  reconciliation is still separate; do not equate this NSIS result with that security-state gate.
