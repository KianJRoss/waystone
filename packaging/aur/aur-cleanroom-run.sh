#!/bin/bash
# Inside a throwaway archlinux container: build the AUR recipe like an AUR user (download + checksum from GitHub),
# lint it, install it, and start Waystone as a brand-new user.
set -euo pipefail
deps=$(sed -n 's/^\t\(make\)\?depends = //p' /work/pkg/.SRCINFO | tr '\n' ' ')
pacman -Syu --noconfirm --needed base-devel git namcap xorg-server-xvfb ttf-dejavu $deps >/tmp/pacman.log 2>&1 || { tail -20 /tmp/pacman.log; exit 1; }
useradd -m builder && useradd -m newuser
mkdir -p /build && cp /work/pkg/PKGBUILD /work/pkg/.SRCINFO /build/ && chown -R builder /build
su builder -c 'cd /build && makepkg -f --noconfirm' >/tmp/makepkg.log 2>&1 || { tail -30 /tmp/makepkg.log; exit 1; }
grep -E "Validating|Passed|FAILED" /tmp/makepkg.log | head -3
echo "== namcap PKGBUILD:"; namcap /build/PKGBUILD || true
echo "== namcap package:"; namcap /build/*.pkg.tar.zst 2>&1 | grep -v "dependency .* detected and implicitly satisfied" | head -25 || true
pacman -U --noconfirm /build/*.pkg.tar.zst >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
echo "== installed: $(pacman -Q waystone-launcher); command: $(command -v waystone); app: $(ls -d /usr/lib/waystone)"
su newuser -c 'cd ~ && xvfb-run -a timeout 60 waystone > /tmp/app.log 2>&1; echo "app exit $?" >> /tmp/app.log' || true
echo "== app log:"; grep -v "JPEG\|portal" /tmp/app.log | tail -8
su newuser -c 'ls ~/.local/share/waystone/'
echo "== AUR TEST DONE"
