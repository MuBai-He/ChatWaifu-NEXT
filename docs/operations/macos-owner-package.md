# macOS ARM64 owner package

This path builds the smallest currently functional native ChatWaifu NEXT package for an Apple
Silicon Mac. It contains the Desktop frontend, Tauri host, base Python Runtime, conversation,
memory, Runtime Skills/MCP, cloud-provider configuration, WebRTC, SQLite, system-keychain support,
and the small Silero VAD asset required by the base realtime path.

It deliberately does **not** contain PyTorch, Transformers, Qwen3-TTS, GPT-SoVITS,
faster-whisper, MLX, voice checkpoints, training data, or local model weights. Local TTS/STT remains
an optional separately installed capability; configured cloud LLM and cloud TTS providers continue
to work. The base Runtime can also start with its deterministic/fake fallbacks.

If `apps/web/public/vendor/live2d` exists in the checkout, the local character artwork is embedded.
Those assets are owner-only and have not passed redistribution review, so the resulting package must
not be uploaded to a public release.

## Build

Requirements are Apple Silicon macOS 14 or later, Python 3.12 through `uv`, Node/pnpm dependencies,
Rust, Xcode command-line tools, and the Tauri DMG tooling. From the repository root:

```bash
make build-macos-owner-package
```

The command creates an isolated environment at
`.local/envs/runtime-packaging-macos-arm64`, rebuilds and smoke-tests the frozen Runtime, builds the
Desktop frontend, bundles the app, and runs a complete packaged lifecycle smoke. The smoke launches
the actual Tauri executable, waits for its embedded Runtime health endpoint, exits the Host, and
fails if the Runtime survives.

Artifacts are written to:

```text
dist/macos/package/ChatWaifu-NEXT_0.2.0_macos-arm64.dmg
dist/macos/package/ChatWaifu-NEXT_0.2.0_macos-arm64.app.zip
dist/macos/package/ChatWaifu-NEXT_0.2.0_macos-arm64.sha256
```

The version comes from `release/products.json`. The DMG is convenient for local installation; the
zip preserves the `.app` bundle for direct transfer or inspection.

## Install and first launch

Open the DMG and drag ChatWaifu NEXT into Applications. This owner candidate is not Developer ID
signed or notarized. A locally built copy normally opens directly; after transferring it to another
Mac, use Finder's **Open** context action if Gatekeeper asks for confirmation.

The application stores configuration, memory, generated audio, plugins, and logs in macOS per-user
application directories. These files are not inside the app bundle and replacing the app does not
erase them. API keys are entered in the settings UI and are not copied from the build checkout.

## Scope and remaining release gates

This is an unsigned owner-testing candidate, not a public macOS release. A distributable release
still requires a clean-account installed UX pass, private-asset removal or licensing, dependency
notices, Developer ID signing, hardened-runtime/entitlement review, notarization, stapling, update
behavior, and a second-machine acceptance run. Intel and universal builds are not produced by this
ARM64 command because the embedded Python Runtime must be frozen natively for each architecture.
