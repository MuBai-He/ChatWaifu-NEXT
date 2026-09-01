# ADR 0028: Versioned local AI Worker Packs

- Status: Accepted
- Date: 2026-08-30
- Validation state: Contract plus native Windows x64 CPU/CUDA builds, real inference, offline installation, and installed supervision validated on 2026-09-01; public distribution/signing and the remaining full-product release matrix are pending

## Context

ADR 0006 keeps model SDKs outside Runtime, and ADR 0027 deliberately excludes CUDA, PyTorch,
Qwen3-TTS, faster-whisper, weights, and private character assets from the base Windows installer.
Source-tree launchers can create development environments, but an installed product cannot depend
on a checkout, `uv`, system Python, a fixed port, or a mutable virtual environment.

The first useful Windows local-model slice needs two very different dependency graphs:

- faster-whisper `base`, running as a CPU `int8` final-transcription worker; and
- the locally trained Ningning Qwen3-TTS 0.6B CustomVoice checkpoint, running through the official
  PyTorch CUDA adapter with SDPA.

Putting either graph into the base installer would make every update large, couple application and
model release cadence, and risk redistributing private or unreviewed assets. Accepting an arbitrary
executable path or environment block would instead turn model installation into an unsafe generic
process launcher.

## Decision

### One strict pack contract

An optional local model process is installed as a `.cwpack` Zip64 archive. The archive root contains
one `manifest.json`; every other regular file is listed exactly once by normalized POSIX-relative
path, byte size, SHA-256, and role. Unknown fields, duplicate or case-colliding paths, traversal,
absolute paths, symlinks, reparse escapes, encrypted members, undeclared files, mutable databases,
secret files, and repository metadata are rejected.

The versioned Pydantic contract lives in `chatwaifu-model-worker-sdk`, not in a particular provider
or frontend. It describes:

- pack ID and SemVer;
- operating system, `x86_64` or `arm64`, accelerator, accelerator version, and Python ABI;
- one STT or TTS worker with provider-neutral identity;
- a direct executable plus bounded arguments, working directory, health and capabilities paths;
- only kind-scoped Worker environment settings; and
- payload hashes plus license metadata.

`HOST`, `PORT`, and `TOKEN` are always Supervisor-owned. A manifest cannot add PATH, provider keys,
LLM secrets, shell commands, or unrelated environment variables. The only substitutions are
`${PACK_ROOT}`, `${DATA_ROOT}`, and `${CONFIG_ROOT}`. Windows native files in an x64 pack must have
PE Machine `0x8664`; an ARM64 pack must use `0xAA64`.

### Installation and activation

The offline installer fully validates the archive before extraction, extracts through an
owner-private same-volume staging directory, rechecks bytes while writing, then atomically renames
the version into:

```text
app_local_data_dir/runtime/worker-packs/<pack-id>/<version>/
```

It writes an `install-receipt.json` containing the archive and manifest hashes, installation time,
identity, and verified file count. Existing versions are never overwritten. Activation is a separate
atomic update to:

```text
app_config_dir/runtime/local-ai-selection.json
```

The receipt proves that local bytes still match a previously verified archive; it does not prove
publisher authenticity. The first owner-only offline flow trusts the archive explicitly selected by
the owner. A public download catalog requires signatures and license review before it may install
without an explicit local-file action.

The only exception to immutable exact-version installation is an explicit owner-requested repair
after Runtime verification has already proved that exact installed target invalid. Repair accepts
only the original archive whose identity and hashes match the surviving receipt/manifest metadata,
verifies and stages it before moving the target, performs a same-volume atomic swap, re-verifies the
replacement, and restores the original directory on failure. It refuses a valid target, missing or
damaged identity metadata, a different archive, a running product graph, and any reparse/redirected
path. Normal install and application startup never silently overwrite a pack.

### Runtime supervision

The frozen Runtime discovers and validates compatible installed packs before loading immutable
Settings. With no activation file, it selects the newest compatible SemVer for each worker kind so a
first offline install is immediately usable. Once an activation file exists, only its exact choices
are authoritative.

For each selected pack Runtime:

1. verifies the receipt, manifest identity, platform, and entrypoint hash;
2. assigns a free loopback port and a fresh random bearer token;
3. launches the direct entrypoint with a minimal environment and offline Hugging Face policy;
4. waits for authenticated `/v1/health` and `/v1/capabilities`;
5. compares returned provider/model identity with the manifest; and
6. injects only the negotiated endpoint into the existing STT or generic TTS adapter settings.

Worker stdout and stderr go to bounded per-user logs, never into the sidecar bootstrap protocol.
Workers inherit the frozen Runtime's Windows kill-on-close Job. Orderly shutdown terminates and then
force-kills after the declared bound. A pack that cannot start leaves the base Runtime on disabled
STT/fake TTS; a previously ready worker that crashes makes the service stack exit so Tauri's bounded
supervisor can recover the complete graph instead of leaving a dead provider selected.

Worker-native caches are mutable per-launch data outside the verified pack tree. Each launch owns an
exact `worker-cache/<pack-id>/<version>/launch-*` directory with a byte-range OS lock and a ready
marker. Orderly stop removes only its own cache after Worker exit is confirmed. After a crash, a later
Runtime may delete a directory only when it can acquire the prior owner lock and the ready marker,
exact depth, physical root, and non-reparse checks all pass. A live lock, missing/corrupt marker,
unready launch, unverifiable state, symlink, junction, or other reparse point is preserved. Parent
pack/version namespaces are never recursively removed, so concurrent Runtime instances cannot erase
one another's active Numba/native cache.

Tauri remains unaware of Qwen, Whisper, Python, CUDA, and model paths. It continues to own exactly one
Runtime sidecar and receives only sanitized worker names in the versioned bootstrap record.

### Initial provider profiles

The initial faster-whisper pack contains an x64 standalone worker environment and a materialized
CTranslate2 `base` model. Pack configuration sets CPU `int8`, automatic language detection, and
`local_files_only=true`. The engine resolves the actual package directory rather than treating it as
a download cache, and requires the model, config, and tokenizer before loading.

The initial Qwen pack contains an x64 standalone environment, CUDA-matched PyTorch/torchaudio, the
official Qwen3-TTS package, and an explicitly supplied local checkpoint. It defaults to CUDA 12.6,
SDPA, lazy model loading, and the `ayachi_nene_local` CustomVoice speaker. The pack is owner-only
while that checkpoint's redistribution rights remain unreviewed.

The official Qwen Python wrapper currently returns a complete waveform. Its Worker capabilities
therefore declare `native_streaming=false` and no PCM v2 protocol. Runtime can still split the final
WAV for the existing playback path, but that must not be described as provider-native streaming.

### Build and acceptance

Each pack is built on Windows x64 (or x64 emulation for compatibility testing) from a pinned CPython
3.12 environment. The resulting pack must be relocatable and run without target-machine `uv`,
Python, a checkout, or network access. Build tooling must validate architecture, import the native
dependency graph, launch on an ephemeral port, authenticate health/capabilities, perform a real
inference smoke, stop cleanly, build the strict archive, and re-verify it.

Host-independent tests cover contract validation, archive attacks, hashes, atomic installation,
activation, discovery, minimal environment projection, identity negotiation, cancellation, and
offline model path resolution. Release acceptance additionally requires a clean native Windows x64
CPU/CUDA machine, non-silent Chinese and Japanese Qwen output, multilingual Whisper transcription,
repeated interruption, process cleanup, retained packs across base-app uninstall, and a no-network
cold start.

### Native Windows x64 acceptance record

The first native acceptance run completed on 2026-09-01 on an RTX 3090 (compute capability 8.6)
with NVIDIA driver 616.56. PyTorch reported `2.7.1+cu126`, CUDA 12.6 available, and both the model
tensors and inference device at `cuda:0`; no CUDA/PyTorch dependency was added to the base Runtime.

The resulting immutable artifacts were:

| Pack                                                  | Archive bytes | Archive SHA-256                                                    | Expanded bytes | Verified files / native PE files |
| ----------------------------------------------------- | ------------: | ------------------------------------------------------------------ | -------------: | -------------------------------: |
| `chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack`         | 5,443,989,887 | `af33a0f7afb105eeacd6c7a7de7071819afbf4916ba5d85a11a7817f146c00e9` |  8,159,811,077 |                     31,223 / 393 |
| `chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack` |   250,542,825 | `86cf28dc4d07e32587c1be29751e11d5d682f0d461e0d808808b78d894bd4d96` |    447,862,039 |                      5,103 / 159 |

Every listed native executable, DLL, and PYD reported PE Machine `0x8664`. The Qwen manifest hash was
`c899f6f570b90b618e207563128c411c1b85e981681dc56ebb150f33c4b70f4c`; the Whisper manifest hash
was `82ffc2af56c5593aeae878c2da1d5875aec833483ccf0094bc2dcce0a9b220b9`. The Qwen source was
pinned to commit `022e286b98fbec7e1e916cb940cdf532cd9f488e`, and the Whisper model to revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`.

In the final post-inference integrity Worker smoke, the first controlled post-load Chinese inference
generated a 2.08-second WAV in 15,129.724 ms, and the subsequent warm Japanese inference generated a
2.16-second WAV in 4,665.135 ms. Both were well-formed PCM16 containers with expected nonzero duration
and tail metrics, non-silent and unclipped; their RMS levels were -21.142 dBFS and -20.058 dBFS
respectively. CUDA memory measured
2,164,438,016 allocated and 2,302,672,896 reserved bytes after inference, with a 2,321,547,264-byte
drop in free GPU memory. These measurements prove real CUDA execution and viable waveforms, not a
human judgment about voice identity, pronunciation, speed, or subjective audio quality.

The fixed-revision faster-whisper pack ran CPU `int8` with `local_files_only=true`, transcribed the
21.455-second Japanese smoke recording to non-empty, coherent text in 862.951 ms, and started from
the fully materialized model directory with network caches disabled. The exact private-validation
transcript and generated WAVs remain local smoke artifacts and are not part of the archive or
repository.

Both installed-pack smokes authenticated health/capabilities on dynamically assigned loopback
ports, verified identity, exercised inference, cancellation and unload, and confirmed process and
listener closure. Full installed cold starts with both selected packs were observed at about 151
seconds and about 443 seconds; the latter spent most of its time re-verifying the Qwen pack's 31,223
files and multi-gigabyte payload. The
native startup contract therefore remains bounded at 300 seconds for Workers, 120 seconds for the
Runtime server, and 30 seconds of supervisor grace; Desktop Web resolution waits 455 seconds so its
timeout exceeds the complete 450-second native bound. This is a bounded readiness contract, not an
arbitrary synchronization sleep. The near-bound 443-second path is nevertheless a product usability
risk; a future optimization must retain fail-closed integrity, for example through a trustworthy
receipt-backed verification cache, rather than skipping checks.

During native installation, a packaged tool host exposed that `%APPDATA%` and `%LOCALAPPDATA%` may
name a Package `LocalCache` layer instead of the physical user's Known Folders. The pack helper now
uses `SHGetKnownFolderPath(..., KF_FLAG_NO_PACKAGE_REDIRECTION)`, rejects reparse or Package
`LocalCache` roots, and proves a probe file's final handle path before archive verification and
activation. Consequently an ambiguous or redirected caller fails closed rather than installing a
valid pack into a namespace the installed Runtime cannot discover.

## Consequences

The base app stays small, safe, and useful while large local models become independently installable,
updatable, and removable. Provider SDKs remain isolated, endpoint secrets are ephemeral, and adding a
new local STT/TTS implementation does not change Tauri or frontend provider calls.

The first archive duplicates model bytes inside the worker pack for simple offline portability. A
future signed catalog may split immutable worker and model packs, deduplicate shared CUDA/Python
payloads, support resumable downloads, and expose rollback/removal in the settings UI. That extension
must preserve this manifest, receipt, activation, and supervision boundary or supersede this ADR.

## Alternatives

Bundle every model in NSIS; copy a development `.venv`; require users to install Python/CUDA tools
manually; launch arbitrary user commands; let workers choose fixed ports or tokens; load provider SDKs
inside Runtime; or silently download weights on first speech. These approaches increase distribution
size, path drift, privilege, secret exposure, startup races, architecture ambiguity, or privacy risk.

## References

- [ADR 0006: Worker process protocol](0006-worker-process-protocol.md)
- [ADR 0014: Unified selectable neural TTS](0014-unified-selectable-neural-tts.md)
- [ADR 0020: Worker Protocol v2 PCM streaming](0020-worker-protocol-v2-pcm-streaming.md)
- [ADR 0027: Windows installed Desktop layout](0027-windows-installed-desktop-runtime-layout.md)
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [PyTorch local installation](https://pytorch.org/get-started/locally/)
