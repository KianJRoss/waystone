#!/usr/bin/env bash
# Cleanroom container verification for the Waystone AppImage on Ubuntu 24.04 and Fedora.
# Runs ONE container at a time (--rm) to keep memory usage low.
# Inside each container:
#   - Does NOT install python3-pyside6, dotnet-runtime, or libevdev (proving the AppImage's bundled runtimes are used)
#   - Creates a brand-new user (newuser)
#   - Places the AppImage in a writable directory (/opt/appimage-holder) owned by newuser
#   - Runs the AppImage under xvfb-run for 60 seconds
#   - Asserts the app stayed alive for the full 60s, created ~/.local/share/waystone/library.sqlite3,
#     and wrote ZERO files next to the AppImage itself.
#
# Usage:
#   bash tools/appimage_cleanroom_test.sh [--appimage /path/to/Waystone-0.1.0-x86_64.AppImage]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPIMAGE_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --appimage)
      APPIMAGE_ARG="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--appimage PATH]" >&2
      exit 2
      ;;
  esac
done

WORK="$(mktemp -d "${TMPDIR:-/tmp}/waystone-appimage-cleanroom.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

if [[ -n "$APPIMAGE_ARG" ]]; then
  cp -f "$APPIMAGE_ARG" "$WORK/Waystone.AppImage"
else
  bash "$ROOT/packaging/appimage/build_appimage.sh" --output-dir "$WORK" --version 0.1.0
  mv "$WORK"/Waystone-*-x86_64.AppImage "$WORK/Waystone.AppImage"
fi
chmod 0755 "$WORK/Waystone.AppImage"

cat > "$WORK/inner_check.sh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
DISTRO="$1"
echo "== [$DISTRO] Installing minimal Xvfb + X11/OpenGL display libraries (no PySide6, no .NET, no libevdev)..."
if [[ "$DISTRO" == "ubuntu" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/tmp/pkg.log 2>&1
  apt-get install -y --no-install-recommends \
    xvfb xauth libglib2.0-0t64 libgssapi-krb5-2 libpulse0 libegl1 libgl1 libfontconfig1 libdbus-1-3 \
    libx11-6 libx11-xcb1 libxcb1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 \
    libasound2t64 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
    libatomic1 procps ca-certificates >/tmp/pkg.log 2>&1 || { tail -30 /tmp/pkg.log; exit 1; }
elif [[ "$DISTRO" == "fedora" ]]; then
  dnf install -y -q \
    xorg-x11-server-Xvfb xorg-x11-xauth glib2 krb5-libs pulseaudio-libs mesa-libEGL mesa-libGL fontconfig dbus-libs \
    libX11 libX11-xcb libxcb libxkbcommon libxkbcommon-x11 \
    xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil \
    alsa-lib nss libXcomposite libXdamage libXrandr libXtst \
    libatomic findutils procps-ng util-linux >/tmp/pkg.log 2>&1 || { tail -30 /tmp/pkg.log; exit 1; }
fi

useradd -m newuser
mkdir -p /opt/appimage-holder
cp /work/Waystone.AppImage /opt/appimage-holder/Waystone.AppImage
chown -R newuser:newuser /opt/appimage-holder
chmod 0755 /opt/appimage-holder/Waystone.AppImage

BEFORE_SHA="$(sha256sum /opt/appimage-holder/Waystone.AppImage | awk '{print $1}')"
find /opt/appimage-holder -mindepth 1 | sort > /tmp/before-files.txt

echo "== [$DISTRO] Launching Waystone.AppImage as newuser under xvfb-run for 60s..."
set +e
su - newuser -c '
  export APPIMAGE_EXTRACT_AND_RUN=1
  export WAYSTONE_GAME_SCOPES=0
  cd /opt/appimage-holder
  xvfb-run -a timeout 60 /opt/appimage-holder/Waystone.AppImage > /tmp/app.log 2>&1
'
EXIT_CODE=$?
set -e
echo "== [$DISTRO] AppImage exit code after 60s timeout: $EXIT_CODE"
grep -v "JPEG\|portal" /tmp/app.log | tail -25 || true

# timeout returns 124 when the application ran for the full 60 seconds without crashing
if [[ "$EXIT_CODE" -ne 124 ]]; then
  echo "ERROR [$DISTRO]: AppImage exited early with code $EXIT_CODE instead of staying alive for 60s (124)" >&2
  cat /tmp/app.log >&2
  exit 1
fi

echo "== [$DISTRO] Verifying newuser XDG library & state..."
su - newuser -c 'ls -la ~/.local/share/waystone/library.sqlite3'
su - newuser -c 'find ~/.local ~/.config -maxdepth 3 2>/dev/null | sort'

echo "== [$DISTRO] Verifying nothing was written next to Waystone.AppImage..."
find /opt/appimage-holder -mindepth 1 | sort > /tmp/after-files.txt
if [[ "$(cat /tmp/before-files.txt)" != "$(cat /tmp/after-files.txt)" ]]; then
  echo "ERROR [$DISTRO]: Unexpected files written next to Waystone.AppImage:" >&2
  cat /tmp/after-files.txt >&2
  exit 1
fi
AFTER_SHA="$(sha256sum /opt/appimage-holder/Waystone.AppImage | awk '{print $1}')"
[[ "$BEFORE_SHA" == "$AFTER_SHA" ]]

echo "== [$DISTRO] CLEANROOM APPIMAGE TEST PASSED"
EOF
chmod 0755 "$WORK/inner_check.sh"
chmod -R a+rX "$WORK"

# Ensure no stale cleanroom containers are running before starting
docker rm -f waystone-appimage-ubuntu waystone-appimage-fedora >/dev/null 2>&1 || true

echo "=================================================================="
echo "1/2 Running AppImage cleanroom test in Ubuntu 24.04 (sequential)..."
echo "=================================================================="
docker run --rm --name waystone-appimage-ubuntu -v "$WORK:/work:ro" ubuntu:24.04 bash /work/inner_check.sh ubuntu

echo "=================================================================="
echo "2/2 Running AppImage cleanroom test in Fedora 41 (sequential)..."
echo "=================================================================="
docker run --rm --name waystone-appimage-fedora -v "$WORK:/work:ro" fedora:41 bash /work/inner_check.sh fedora

echo "==> ALL APPIMAGE CLEANROOM CONTAINER TESTS (UBUNTU 24.04 & FEDORA) PASSED"
