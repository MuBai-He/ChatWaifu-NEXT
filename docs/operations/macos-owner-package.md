# macOS ARM64 owner package

This path builds the smallest currently functional native ChatWaifu NEXT package for an Apple
Silicon Mac. It contains the Desktop frontend, Tauri host, base Python Runtime, conversation,
memory, Runtime Skills/MCP, cloud-provider configuration, WebRTC, SQLite, system-keychain support,
and the small Silero VAD asset required by the base realtime path.

It deliberately does **not** contain PyTorch, Transformers, Qwen3-TTS, GPT-SoVITS,
faster-whisper, MLX, voice checkpoints, training data, or local model weights. Local TTS/STT remains
an optional separately installed capability; configured cloud LLM and cloud TTS providers continue
to work. The base Runtime can also start with its deterministic/fake fallbacks.

This owner-package command requires `apps/web/public/vendor/live2d/model/avatar.model3.json` and
embeds that local Ayachi Nene character model. Its creator is Bilibili **涂抹一画**; the project
maintainer confirms permission to use the model and redistribute it as part of a complete
ChatWaifu NEXT package. The package also embeds
`Contents/Resources/OWNER_ASSET_NOTICE.md`, and the build fails if either the model or attribution
notice is missing. The permission requires preserving the notice and creator attribution and is not
an open-source license; standalone extraction, reuse in another product, modification, sale, and
commercial use remain outside the confirmed scope. The Live2D asset itself is therefore no longer
a blocker to distributing a complete non-commercial package within this scope, but the package is
not yet a public release because the repository license, original-character rights boundary,
signing, notarization, and clean-machine acceptance remain unresolved.

Tauri 2 embeds the Desktop frontend, including the Live2D files, into the native Host executable;
those assets do not appear as loose files under `Contents/Resources`. Packaging first verifies the
Desktop product's manifest, moc, and texture (including byte identity for the binary assets), then
checks the final Host's embedded asset map. The human-readable attribution notice remains a separate
file at `Contents/Resources/OWNER_ASSET_NOTICE.md` so it can be inspected without unpacking the
executable.

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
fails if the Runtime survives. Packaging also verifies that the embedded Live2D manifest and author
notice are both present. The DMG uses the version-controlled
`apps/desktop/src-tauri/assets/dmg-background.png` artwork and a fixed 720 x 480 Finder layout, with
the application and Applications-folder icons aligned to the two installation targets. Packaging
fails before the expensive Runtime build if that background is missing or has the wrong dimensions.

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
still requires a clean-account installed UX pass, a project distribution license, review of the
original-character/non-commercial boundary, complete dependency notices, Developer ID signing,
hardened-runtime/entitlement review, notarization, stapling, update behavior, and a second-machine
acceptance run. The included Ningning Live2D model already has the separately recorded attribution
and complete-package redistribution permission above. Intel and universal builds are not produced
by this ARM64 command because the embedded Python Runtime must be frozen natively for each
architecture.
