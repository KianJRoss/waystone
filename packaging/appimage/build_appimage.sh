#!/usr/bin/env bash
# Build a reproducible, self-contained Waystone x86_64 AppImage from the curated public tree.
# Bundles:
#   - Standalone CPython 3.13 (via uv python)
#   - PySide6 (QtQuick, QtMultimedia, QtSvg, QtWebEngine), httpx, PyYAML, websockets, mcp
#   - libevdev.so.2 (and XCB cursor/util helper libraries when present on build host)
#   - Self-contained .NET 10 UnifiedController.Headless engine
#
# Usage:
#   bash packaging/appimage/build_appimage.sh [--output-dir DIR] [--version VER]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${WAYSTONE_VERSION:-0.1.0}"
OUT_DIR="$ROOT/dist"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --version)
      VERSION="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--output-dir DIR] [--version VER]" >&2
      exit 2
      ;;
  esac
done

for cmd in uv dotnet mksquashfs curl sha256sum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "build_appimage.sh: required command '$cmd' not found on PATH" >&2
    exit 3
  fi
done

RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
RUNTIME_SHA256="617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/waystone-build"
mkdir -p "$CACHE_DIR" "$OUT_DIR"
OUT_DIR_ABS="$(cd "$OUT_DIR" && pwd)"

RUNTIME_BIN="$CACHE_DIR/type2-runtime-x86_64"
if [[ ! -f "$RUNTIME_BIN" ]] || ! echo "$RUNTIME_SHA256  $RUNTIME_BIN" | sha256sum -c - >/dev/null 2>&1; then
  echo "==> Downloading AppImage type2-runtime-x86_64..."
  curl -fsSL -o "$RUNTIME_BIN.tmp" "$RUNTIME_URL"
  echo "$RUNTIME_SHA256  $RUNTIME_BIN.tmp" | sha256sum -c -
  chmod +x "$RUNTIME_BIN.tmp"
  mv "$RUNTIME_BIN.tmp" "$RUNTIME_BIN"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/waystone-appimage-build.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

PUBLIC_SRC="$WORK_DIR/public-src"
APPDIR="$WORK_DIR/Waystone.AppDir"

echo "==> Exporting curated public tree..."
if [ -f "$ROOT/tools/export_public.sh" ]; then
  bash "$ROOT/tools/export_public.sh" "$PUBLIC_SRC"   # private repo: build from the curated public tree
else
  # already the public repo (e.g. GitHub Actions): use the checkout as-is, without git metadata
  mkdir -p "$PUBLIC_SRC" && rsync -a --exclude .git --exclude dist "$ROOT/" "$PUBLIC_SRC"/
fi

echo "==> Preparing AppDir layout..."
mkdir -p \
  "$APPDIR/usr/share/waystone" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor" \
  "$APPDIR/usr/share/metainfo" \
  "$APPDIR/usr/lib/udev/rules.d" \
  "$APPDIR/usr/lib"

cp -a "$PUBLIC_SRC/." "$APPDIR/usr/share/waystone/"

echo "==> Publishing self-contained .NET 10 controller engine..."
ENGINE_OUT="$APPDIR/usr/share/waystone/components/controller_studio/headless/publish"
rm -rf "$ENGINE_OUT"
mkdir -p "$ENGINE_OUT"
bash "$PUBLIC_SRC/tools/build_controller_engine.sh" --output "$ENGINE_OUT"
rm -f "$ENGINE_OUT"/*.pdb
rm -rf \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/headless/bin" \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/headless/obj" \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/upstream/src/UnifiedController.Core/bin" \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/upstream/src/UnifiedController.Core/obj" \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/upstream/src/UnifiedController.Linux.Platform/bin" \
  "$APPDIR/usr/share/waystone/components/controller_studio/src/upstream/src/UnifiedController.Linux.Platform/obj"

# Bundle the .NET 10 runtime into AppDir/usr/dotnet so the AppImage runs on systems without dotnet-runtime installed
HOST_DOTNET_BIN="$(readlink -f "$(command -v dotnet)")"
HOST_DOTNET_ROOT="$(cd "$(dirname "$HOST_DOTNET_BIN")" && pwd)"
mkdir -p "$APPDIR/usr/dotnet/shared"
cp -a "$HOST_DOTNET_ROOT/dotnet" "$APPDIR/usr/dotnet/"
cp -a "$HOST_DOTNET_ROOT/host" "$APPDIR/usr/dotnet/"
cp -a "$HOST_DOTNET_ROOT/shared/Microsoft.NETCore.App" "$APPDIR/usr/dotnet/shared/"

mv "$ENGINE_OUT/UnifiedController.Headless" "$ENGINE_OUT/UnifiedController.Headless.bin"
cat > "$ENGINE_OUT/UnifiedController.Headless" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOTNET_ROOT="${DOTNET_ROOT:-$(cd "$HERE/../../../../../dotnet" && pwd)}"
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="${DOTNET_SYSTEM_GLOBALIZATION_INVARIANT:-1}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
exec "$HERE/UnifiedController.Headless.bin" "$@"
EOF
chmod 0755 "$ENGINE_OUT/UnifiedController.Headless" "$ENGINE_OUT/UnifiedController.Headless.bin"

echo "==> Provisioning standalone CPython 3.13 + PySide6 runtime..."
uv python install 3.13 >/dev/null
UV_PY_BIN="$(uv python find --managed-python 3.13)"
UV_PY_ROOT="$(cd "$(dirname "$UV_PY_BIN")/.." && pwd)"
mkdir -p "$APPDIR/usr/python"
cp -a "$UV_PY_ROOT/." "$APPDIR/usr/python/"
rm -rf "$APPDIR/usr/python/include" "$APPDIR/usr/python/share"
find "$APPDIR/usr/python/lib" -name 'EXTERNALLY-MANAGED' -delete 2>/dev/null || true
find "$APPDIR/usr/python/lib" -maxdepth 2 -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true

# Install PySide6 (Essentials + Addons for QtQuick, Multimedia, SVG, WebEngine), httpx, PyYAML, websockets, mcp
uv pip install --python "$APPDIR/usr/python/bin/python3" --system --break-system-packages \
  "PySide6==6.12.0" \
  "httpx>=0.27" \
  "PyYAML>=6.0" \
  "websockets>=12.0" \
  "mcp>=1.0"

# Normalize any shebangs in usr/python/bin so no build-host paths remain
while IFS= read -r -d '' script_file; do
  if head -c 2 "$script_file" 2>/dev/null | grep -q '^#!'; then
    sed -i '1s|^#!.*python.*|#!/usr/bin/env python3|' "$script_file" || true
  fi
done < <(find "$APPDIR/usr/python/bin" -maxdepth 1 -type f -print0)

# Remove pip/wheel RECORD or build metadata that could embed temporary build paths, and precompile app .pyc
find "$APPDIR/usr/python" -name 'direct_url.json' -delete 2>/dev/null || true

echo "==> Bundling libevdev.so.2 and XCB helper libraries..."
for libname in \
  libevdev.so.2 \
  libxcb-cursor.so.0 \
  libxcb-icccm.so.4 \
  libxcb-image.so.0 \
  libxcb-keysyms.so.1 \
  libxcb-render-util.so.0 \
  libxcb-shape.so.0 \
  libxcb-util.so.1 \
  libxcb-xinerama.so.0 \
  libxcb-xkb.so.1 \
  libxkbcommon-x11.so.0 \
  libxkbcommon.so.0; do
  found_lib=""
  for search_dir in /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu /usr/lib64 /usr/lib; do
    if [[ -f "$search_dir/$libname" ]]; then
      found_lib="$search_dir/$libname"
      break
    fi
  done
  if [[ -n "$found_lib" ]]; then
    cp -L "$found_lib" "$APPDIR/usr/lib/$libname"
    chmod 0644 "$APPDIR/usr/lib/$libname"
  elif [[ "$libname" == "libevdev.so.2" ]]; then
    echo "build_appimage.sh: ERROR: libevdev.so.2 not found on build host" >&2
    exit 1
  fi
done

echo "==> Installing AppRun, desktop entry, icons, and udev rule..."
install -Dm755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -Dm644 "$PUBLIC_SRC/packaging/arch/waystone.desktop" "$APPDIR/waystone.desktop"
install -Dm644 "$PUBLIC_SRC/packaging/arch/waystone.desktop" "$APPDIR/usr/share/applications/waystone.desktop"
install -Dm644 "$PUBLIC_SRC/packaging/arch/70-waystone-input.rules" "$APPDIR/usr/lib/udev/rules.d/70-waystone-input.rules"

for size in 16 24 32 48 64 128 256 512; do
  install -Dm644 "$PUBLIC_SRC/assets/brand/png/waystone-${size}.png" \
    "$APPDIR/usr/share/icons/hicolor/${size}x${size}/apps/waystone.png"
done
install -Dm644 "$PUBLIC_SRC/assets/brand/waystone-icon.svg" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps/waystone.svg"
install -Dm644 "$PUBLIC_SRC/assets/brand/png/waystone-512.png" "$APPDIR/waystone.png"
ln -sf waystone.png "$APPDIR/.DirIcon"

chmod -R u=rwX,go=rX "$APPDIR/usr/share/waystone"
chmod 0755 \
  "$APPDIR/AppRun" \
  "$APPDIR/usr/share/waystone/open-waystone.sh" \
  "$APPDIR/usr/share/waystone/components/controller_studio/headless/publish/UnifiedController.Headless"

echo "==> Verifying bundled Python imports (PySide6: QtQuick, QtMultimedia, QtSvg, QtWebEngineQuick; httpx; yaml)..."
"$APPDIR/usr/python/bin/python3" -B -c "
import httpx, yaml
from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtMultimedia, QtSvg, QtWebEngineQuick
print('Verified bundled Python', QtCore.__version__)
"

echo "==> Packing SquashFS and assembling AppImage..."
SQUASHFS_IMG="$WORK_DIR/waystone.squashfs"
mksquashfs "$APPDIR" "$SQUASHFS_IMG" -root-owned -noappend -comp zstd -Xcompression-level 15 -quiet

APPIMAGE_PATH="$OUT_DIR_ABS/Waystone-${VERSION}-x86_64.AppImage"
cat "$RUNTIME_BIN" "$SQUASHFS_IMG" > "$APPIMAGE_PATH"
chmod 0755 "$APPIMAGE_PATH"

echo "==> Built AppImage: $APPIMAGE_PATH ($(du -h "$APPIMAGE_PATH" | cut -f1))"
sha256sum "$APPIMAGE_PATH"
