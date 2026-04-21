#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="$SCRIPT_DIR"
VENV="$BUILD_ROOT/.venv-pkg"
SRC="$ROOT_DIR/video_player.py"
METADATA="$ROOT_DIR/video_player_metadata.py"
BUILD_REQUIREMENTS="$ROOT_DIR/requirements-build.txt"
ICON_SWIFT="$BUILD_ROOT/make_icon.swift"
ICON_PNG="$BUILD_ROOT/AppIcon-1024.png"
ICONSET="$BUILD_ROOT/AppIcon.iconset"
ICON_ICNS="$BUILD_ROOT/AppIcon.icns"
DIST_DIR="$BUILD_ROOT/dist"
WORK_DIR="$BUILD_ROOT/work"
SPEC_DIR="$BUILD_ROOT/spec"
RELEASE_DIR="$BUILD_ROOT/release"
STAGE_DIR="$RELEASE_DIR/dmg-stage"

INSTALL_APP=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install)
      INSTALL_APP=0
      ;;
    --install)
      INSTALL_APP=1
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--skip-install]" >&2
      exit 1
      ;;
  esac
  shift
done

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required tool: $1" >&2
    exit 1
  fi
}

require_tool python3
require_tool swift
require_tool sips
require_tool iconutil
require_tool hdiutil
require_tool ditto
require_tool codesign

if [[ ! -f "$SRC" || ! -f "$METADATA" || ! -f "$ICON_SWIFT" || ! -f "$BUILD_REQUIREMENTS" ]]; then
  echo "Missing source or build inputs under $ROOT_DIR" >&2
  exit 1
fi

eval "$(
  PYTHONPATH="$ROOT_DIR" python3 - <<'PY'
import shlex
from video_player_metadata import (
    APP_BUILD,
    APP_CATEGORY,
    APP_IDENTIFIER,
    APP_MIN_SYSTEM_VERSION,
    APP_NAME,
    APP_VERSION,
)

for key, value in {
    "APP_NAME": APP_NAME,
    "APP_IDENTIFIER": APP_IDENTIFIER,
    "APP_VERSION": APP_VERSION,
    "APP_BUILD": APP_BUILD,
    "APP_CATEGORY": APP_CATEGORY,
    "APP_MIN_SYSTEM_VERSION": APP_MIN_SYSTEM_VERSION,
}.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

APP_BUNDLE="$DIST_DIR/${APP_NAME}.app"
INSTALL_PATH="/Applications/${APP_NAME}.app"
VERSIONED_STEM="${APP_NAME// /-}-${APP_VERSION}-macOS"
ZIP_PATH="$RELEASE_DIR/${VERSIONED_STEM}.zip"
DMG_PATH="$RELEASE_DIR/${VERSIONED_STEM}.dmg"
CODESIGN_IDENTITY="${VIDEO_PLAYER_CODESIGN_IDENTITY:--}"

mkdir -p "$BUILD_ROOT" "$DIST_DIR" "$WORK_DIR" "$SPEC_DIR" "$RELEASE_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV"
fi

"$VENV/bin/python" -m pip install --disable-pip-version-check --quiet -r "$BUILD_REQUIREMENTS"
"$VENV/bin/python" -m py_compile \
  "$SRC" \
  "$METADATA" \
  "$ROOT_DIR/video_player_mcp/server.py"

/usr/bin/swift "$ICON_SWIFT" "$ICON_PNG"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

for size in 16 32 64 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
done

/bin/cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
/bin/cp "$ICONSET/icon_64x64.png" "$ICONSET/icon_32x32@2x.png"
/bin/cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
/bin/cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_256x256@2x.png"
/usr/bin/sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
/usr/bin/iconutil -c icns "$ICONSET" -o "$ICON_ICNS"

rm -rf "$APP_BUNDLE" "$WORK_DIR" "$SPEC_DIR" "$STAGE_DIR"
rm -f "$ZIP_PATH" "$DMG_PATH"

"$VENV/bin/pyinstaller" \
  --clean \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_ICNS" \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  --specpath "$SPEC_DIR" \
  --osx-bundle-identifier "$APP_IDENTIFIER" \
  "$SRC"

PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" - <<PY
from pathlib import Path
import plistlib

from video_player_metadata import (
    APP_BUILD,
    APP_CATEGORY,
    APP_IDENTIFIER,
    APP_MIN_SYSTEM_VERSION,
    APP_NAME,
    APP_VERSION,
)

info_path = Path("$APP_BUNDLE/Contents/Info.plist")
with info_path.open("rb") as fh:
    plist = plistlib.load(fh)

plist.update(
    {
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "CFBundleIdentifier": APP_IDENTIFIER,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_BUILD,
        "LSApplicationCategoryType": APP_CATEGORY,
        "LSMinimumSystemVersion": APP_MIN_SYSTEM_VERSION,
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Video File",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": [
                    "public.movie",
                    "public.video",
                    "public.mpeg-4",
                    "com.apple.quicktime-movie",
                ],
            }
        ],
    }
)

with info_path.open("wb") as fh:
    plistlib.dump(plist, fh)
PY

sign_bundle() {
  /usr/bin/codesign --force --deep --sign "$CODESIGN_IDENTITY" "$1"
  /usr/bin/codesign --verify --deep --strict "$1"
}

sign_bundle "$APP_BUNDLE"

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

mkdir -p "$STAGE_DIR"
/bin/cp -R "$APP_BUNDLE" "$STAGE_DIR/"
/bin/ln -s /Applications "$STAGE_DIR/Applications"
cat > "$STAGE_DIR/Install Video Player.txt" <<TXT
Video Player $APP_VERSION

1. Drag Video Player.app into Applications.
2. Launch the app from Applications.
3. If macOS warns that the app was downloaded from the internet, use Open from the context menu the first time.
TXT
/usr/bin/hdiutil create \
  -volname "${APP_NAME} ${APP_VERSION}" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

if [[ "$INSTALL_APP" -eq 1 ]]; then
  /usr/bin/pkill -f "/Applications/${APP_NAME}.app" >/dev/null 2>&1 || true
  /bin/rm -rf "$INSTALL_PATH"
  /bin/cp -R "$APP_BUNDLE" "$INSTALL_PATH"
  sign_bundle "$INSTALL_PATH"
fi

echo "Built app: $APP_BUNDLE"
echo "Zip artifact: $ZIP_PATH"
echo "DMG artifact: $DMG_PATH"
if [[ "$INSTALL_APP" -eq 1 ]]; then
  echo "Installed app: $INSTALL_PATH"
else
  echo "Install step skipped"
fi
