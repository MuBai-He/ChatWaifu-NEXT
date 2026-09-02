#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIRECTORY/../.." && pwd)"
PACKAGING_ENVIRONMENT="$REPOSITORY_ROOT/.local/envs/runtime-packaging-macos-arm64"
PACKAGING_PYTHON="$PACKAGING_ENVIRONMENT/bin/python"
APP_BUNDLE="$REPOSITORY_ROOT/target/release/bundle/macos/ChatWaifu NEXT.app"
DMG_DIRECTORY="$REPOSITORY_ROOT/target/release/bundle/dmg"
OUTPUT_DIRECTORY="$REPOSITORY_ROOT/dist/macos/package"
BUILD_MARKER="$(mktemp -t chatwaifu-macos-package.XXXXXX)"
OWNER_LIVE2D_MANIFEST="$REPOSITORY_ROOT/apps/web/public/vendor/live2d/model/avatar.model3.json"
OWNER_LIVE2D_ROOT="$REPOSITORY_ROOT/apps/web/public/vendor/live2d/model"
DESKTOP_LIVE2D_ROOT="$REPOSITORY_ROOT/apps/web/dist/desktop/vendor/live2d/model"
DMG_BACKGROUND="$REPOSITORY_ROOT/apps/desktop/src-tauri/assets/dmg-background.png"

cleanup() {
  rm -f "$BUILD_MARKER"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This owner package currently targets an Apple Silicon macOS host (arm64)." >&2
  exit 1
fi

cd "$REPOSITORY_ROOT"

if [[ ! -f "$OWNER_LIVE2D_MANIFEST" ]]; then
  echo "Owner package requires the local Ayachi Nene Live2D model: $OWNER_LIVE2D_MANIFEST" >&2
  exit 1
fi

if [[ ! -s "$DMG_BACKGROUND" ]]; then
  echo "Owner package requires the branded DMG background: $DMG_BACKGROUND" >&2
  exit 1
fi
DMG_BACKGROUND_WIDTH="$(/usr/bin/sips -g pixelWidth "$DMG_BACKGROUND" | awk '/pixelWidth/ { print $2 }')"
DMG_BACKGROUND_HEIGHT="$(/usr/bin/sips -g pixelHeight "$DMG_BACKGROUND" | awk '/pixelHeight/ { print $2 }')"
if [[ "$DMG_BACKGROUND_WIDTH" != "720" || "$DMG_BACKGROUND_HEIGHT" != "480" ]]; then
  echo "DMG background must be exactly 720x480; received ${DMG_BACKGROUND_WIDTH}x${DMG_BACKGROUND_HEIGHT}." >&2
  exit 1
fi

UV_PROJECT_ENVIRONMENT="$PACKAGING_ENVIRONMENT" uv sync \
  --project "$REPOSITORY_ROOT" \
  --python 3.12 \
  --package chatwaifu-runtime \
  --group packaging \
  --no-dev \
  --locked

"$PACKAGING_PYTHON" tools/setup_nltk_data.py
"$PACKAGING_PYTHON" tools/build_runtime_sidecar.py --platform macos
"$PACKAGING_PYTHON" tools/smoke_runtime_sidecar.py \
  --executable dist/macos/runtime-sidecar/chatwaifu-runtime \
  --timeout 180

uv run python tools/run_pnpm.py \
  --filter @chatwaifu/desktop \
  build:macos-package
uv run python tools/verify_product_artifacts.py --product desktop

for relative_asset in avatar.model3.json avatar.moc3 texture/texture_00.png; do
  if [[ ! -s "$DESKTOP_LIVE2D_ROOT/$relative_asset" ]]; then
    echo "Desktop product is missing required Live2D asset: $relative_asset" >&2
    exit 1
  fi
done
for byte_identical_asset in avatar.moc3 texture/texture_00.png; do
  if ! cmp -s \
    "$OWNER_LIVE2D_ROOT/$byte_identical_asset" \
    "$DESKTOP_LIVE2D_ROOT/$byte_identical_asset"; then
    echo "Desktop product changed required Live2D asset: $byte_identical_asset" >&2
    exit 1
  fi
done

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Tauri did not produce the expected app bundle: $APP_BUNDLE" >&2
  exit 1
fi

EMBEDDED_RUNTIME="$APP_BUNDLE/Contents/Resources/runtime-sidecar/chatwaifu-runtime"
HOST_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/chatwaifu-desktop-host"
if [[ ! -x "$EMBEDDED_RUNTIME" || ! -x "$HOST_EXECUTABLE" ]]; then
  echo "The app bundle is missing an executable host or Runtime sidecar." >&2
  exit 1
fi

EMBEDDED_OWNER_ASSET_NOTICE="$APP_BUNDLE/Contents/Resources/OWNER_ASSET_NOTICE.md"
if ! grep -aFq "/vendor/live2d/model/avatar.model3.json" "$HOST_EXECUTABLE" || \
  ! grep -aFq "/vendor/live2d/model/texture/texture_00.png" "$HOST_EXECUTABLE"; then
  echo "Tauri Host did not embed the required Ayachi Nene Live2D frontend assets." >&2
  exit 1
fi
if [[ ! -f "$EMBEDDED_OWNER_ASSET_NOTICE" ]] || ! grep -q "涂抹一画" "$EMBEDDED_OWNER_ASSET_NOTICE"; then
  echo "Owner package is missing the required Live2D creator attribution notice." >&2
  exit 1
fi
if ! file "$EMBEDDED_RUNTIME" | grep -q "arm64"; then
  echo "Embedded Runtime is not an arm64 Mach-O executable." >&2
  exit 1
fi
if ! file "$HOST_EXECUTABLE" | grep -q "arm64"; then
  echo "Desktop host is not an arm64 Mach-O executable." >&2
  exit 1
fi

INTERNAL_ROOT="$APP_BUNDLE/Contents/Resources/runtime-sidecar/_internal"
for forbidden_package in torch torchaudio transformers qwen_tts faster_whisper mlx mlx_lm; do
  if [[ -n "$(find "$INTERNAL_ROOT" -maxdepth 1 -iname "$forbidden_package*" -print -quit)" ]]; then
    echo "Base package unexpectedly contains local-model dependency: $forbidden_package" >&2
    exit 1
  fi
done

FORBIDDEN_WEIGHT="$(find "$APP_BUNDLE" -type f \( \
  -iname '*.safetensors' -o \
  -iname '*.pt' -o \
  -iname '*.pth' -o \
  -iname '*.ckpt' -o \
  -iname '*.gguf' \
\) -print -quit)"
if [[ -n "$FORBIDDEN_WEIGHT" ]]; then
  echo "Base package unexpectedly contains a model weight: $FORBIDDEN_WEIGHT" >&2
  exit 1
fi

FORBIDDEN_PRIVATE_DATA="$(find "$APP_BUNDLE" -type f \( \
  -name '.env' -o \
  -iname '*.sqlite' -o \
  -iname '*.sqlite3' -o \
  -iname '*.db-wal' -o \
  -iname '*.db-shm' \
\) -print -quit)"
if [[ -n "$FORBIDDEN_PRIVATE_DATA" ]]; then
  echo "Base package unexpectedly contains mutable or private data: $FORBIDDEN_PRIVATE_DATA" >&2
  exit 1
fi

"$PACKAGING_PYTHON" tools/smoke_runtime_sidecar.py \
  --executable "$EMBEDDED_RUNTIME" \
  --timeout 180
"$PACKAGING_PYTHON" tools/macos/smoke_packaged_app.py \
  --app "$APP_BUNDLE" \
  --timeout 120

DMG_CANDIDATES=()
while IFS= read -r -d '' candidate; do
  DMG_CANDIDATES+=("$candidate")
done < <(find "$DMG_DIRECTORY" -maxdepth 1 -type f -name '*.dmg' -newer "$BUILD_MARKER" -print0)
if [[ "${#DMG_CANDIDATES[@]}" -ne 1 ]]; then
  echo "Expected one fresh DMG, received ${#DMG_CANDIDATES[@]}." >&2
  exit 1
fi

DESKTOP_VERSION="$("$PACKAGING_PYTHON" -c \
  'import json; print(json.load(open("release/products.json"))["products"]["desktop"]["version"])')"
mkdir -p "$OUTPUT_DIRECTORY"
FINAL_DMG="$OUTPUT_DIRECTORY/ChatWaifu-NEXT_${DESKTOP_VERSION}_macos-arm64.dmg"
FINAL_APP_ZIP="$OUTPUT_DIRECTORY/ChatWaifu-NEXT_${DESKTOP_VERSION}_macos-arm64.app.zip"
CHECKSUM_FILE="$OUTPUT_DIRECTORY/ChatWaifu-NEXT_${DESKTOP_VERSION}_macos-arm64.sha256"
rm -f "$FINAL_DMG" "$FINAL_APP_ZIP" "$CHECKSUM_FILE"
cp "${DMG_CANDIDATES[0]}" "$FINAL_DMG"
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$FINAL_APP_ZIP"
(
  cd "$OUTPUT_DIRECTORY"
  shasum -a 256 "$(basename "$FINAL_DMG")" "$(basename "$FINAL_APP_ZIP")" \
    > "$(basename "$CHECKSUM_FILE")"
)

echo "macOS owner package: $FINAL_DMG"
echo "Portable app archive: $FINAL_APP_ZIP"
echo "Checksums: $CHECKSUM_FILE"
