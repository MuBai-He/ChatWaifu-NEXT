# Windows x64 local AI Worker Packs

The base Desktop installer deliberately excludes CUDA, PyTorch, model weights, and Python model
environments.  Qwen3-TTS and faster-whisper are distributed as independently versioned `.cwpack`
archives instead.  Each archive contains its own relocatable x64 CPython 3.12 runtime, worker code,
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
  wheel.  An emulated ARM VM can check the x64 build path, but cannot replace native CUDA acceptance.

Do not put a ModelScope/Hugging Face token, character checkpoint, or smoke recording in the
repository.  The build scans staging and rejects common secret, database, VCS, IDE, cache, and
symlink paths.

## faster-whisper Base CPU int8

Supply a real, short PCM16 speech recording for the release smoke.  Mono or stereo WAV at 8-48 kHz
is accepted.  With no `-ModelSource`, the build machine downloads the revision-pinned public model,
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

Pass the extracted CustomVoice checkpoint directory.  The builder verifies that `config.json`
declares `qwen3_tts` / `custom_voice` and contains the selected speaker before copying it.  The
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
SDPA, and `cuda:0`.  Its smoke launches the installed pack without the builder environment and
synthesizes fixed Chinese and Japanese lines.  Both results must be non-silent PCM16 WAV.  The
official Torch wrapper currently generates the full waveform before emitting it, so this provider
truthfully advertises `native_streaming=false` even though the worker boundary supports PCM v2.

## Outputs and acceptance

Successful builds write the immutable versioned archive and SHA-256 sidecar under
`dist\windows\worker-packs\`.  Smoke logs, Qwen Chinese/Japanese WAVs, or the Whisper transcript are
written below the adjacent `smoke\` directory.  Both builders also:

1. inspect every `.exe`, `.dll`, and `.pyd` as PE machine `0x8664`;
2. build and fully verify the canonical checksummed Zip64 archive;
3. install it atomically into a temporary pack root;
4. start the bundled entry point with an ephemeral loopback port and bearer token;
5. check authenticated health and capabilities, run real inference, unload it, terminate it, and
   confirm the listener closes.

`-SkipModelSmoke` exists only for diagnosing build machines.  It leaves an explicitly unverified
pack and is not a release acceptance result.  A release candidate still needs a clean native Windows
x64 target run; Qwen additionally needs the intended CUDA laptop and driver.

The Runtime sidecar owns installation, activation, dynamic host/port/token injection, supervision,
and restart.  Pack manifests contain only their namespaced static `CHATWAIFU_STT_WORKER_*` or
`CHATWAIFU_NEURAL_TTS_WORKER_*` settings and never store endpoint credentials.

## Install into ChatWaifu NEXT

Install the base x64 NSIS package first.  Then run the repository helper from a normal PowerShell
session; administrator privileges are not required because both the application and model packs are
per-user.  The helper resolves the installed Runtime from the current-user uninstall registry,
proves that it is an x64 PE, fully verifies the archive, installs it atomically, and activates that
exact version.

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack

.\tools\windows\install_worker_pack_x64.ps1 `
    -ArchivePath .\dist\windows\worker-packs\chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack
```

Restart ChatWaifu NEXT after installation.  On the next frozen-Runtime boot both selected workers
start on ephemeral authenticated loopback ports.  If Whisper cannot load or Qwen's real CUDA
execution probe fails, Runtime keeps the corresponding built-in/network fallback instead of
advertising a broken local provider.  `-VerifyOnly` validates an archive without changing the active
selection; `-RuntimePath` can target a reviewed portable Runtime during release testing.
