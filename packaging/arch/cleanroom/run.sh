#!/bin/bash
# Runs inside a throwaway archlinux container: build, install, first start as a brand-new user.
set -euo pipefail
pacman -Syu --noconfirm --needed base-devel python pyside6 qt6-multimedia qt6-svg qt6-webengine python-httpx python-yaml dotnet-runtime dotnet-sdk aspnet-targeting-pack \
    libevdev xorg-server-xvfb ttf-dejavu >/tmp/pacman.log 2>&1 || { tail -20 /tmp/pacman.log; exit 1; }
echo "== dotnet: $(dotnet --list-sdks | head -1)"
useradd -m builder && useradd -m newuser
mkdir -p /build && cp /work/PKGBUILD /work/waystone-0.1.0.tar.gz /build/ && chown -R builder /build
su builder -c 'cd /build && makepkg -f --noconfirm' >/tmp/makepkg.log 2>&1 || { tail -30 /tmp/makepkg.log; exit 1; }
echo "== built: $(ls /build/*.pkg.tar.zst)"
pacman -U --noconfirm /build/*.pkg.tar.zst >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
echo "== installed files: $(pacman -Ql waystone | wc -l); launcher: $(command -v waystone)"
ls -la /usr/lib/udev/rules.d/70-waystone-input.rules /usr/share/applications/waystone.desktop
# app folder must stay untouched by running it
find /usr/lib/waystone -type f -newer /usr/bin/waystone > /tmp/before.txt || true
su newuser -c 'cd ~ && xvfb-run -a timeout 60 waystone > /tmp/app.log 2>&1; echo "app exit $?" >> /tmp/app.log' || true
echo "== app log (last lines):"; grep -v "JPEG\|portal" /tmp/app.log | tail -25
echo "== new user's data:"; su newuser -c 'find ~/.local ~/.config -maxdepth 3 2>/dev/null | head -30'
echo "== library db:"; su newuser -c 'ls -la ~/.local/share/waystone/library.sqlite3 2>&1'
echo "== files written into /usr/lib/waystone by running it:"; find /usr/lib/waystone -newer /usr/bin/waystone -type f | grep -v -x -f /tmp/before.txt | head || true
echo "== CLEANROOM DONE"
