#!/usr/bin/env bash
# Build .deb and .rpm packages for Waystone using nfpm with the exact same file layout as packaging/arch/PKGBUILD.
#
# Usage:
#   bash packaging/nfpm/build_deb_rpm.sh [--output-dir DIR] [--version VER]
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

NFPM_VERSION="2.49.0"
NFPM_URL="https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz"
NFPM_SHA256="2afaa5ffb5e3493402e31e7c67a5a1fb42133e3f66fc315ca0051934125561c7"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/waystone-build"
mkdir -p "$CACHE_DIR" "$OUT_DIR"
OUT_DIR_ABS="$(cd "$OUT_DIR" && pwd)"

NFPM_BIN="$(command -v nfpm || true)"
if [[ -z "$NFPM_BIN" ]]; then
  NFPM_BIN="$CACHE_DIR/nfpm-${NFPM_VERSION}/nfpm"
  if [[ ! -x "$NFPM_BIN" ]]; then
    echo "==> Downloading nfpm v${NFPM_VERSION}..."
    TARBALL="$CACHE_DIR/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz"
    curl -fsSL -o "$TARBALL.tmp" "$NFPM_URL"
    echo "$NFPM_SHA256  $TARBALL.tmp" | sha256sum -c -
    mv "$TARBALL.tmp" "$TARBALL"
    mkdir -p "$CACHE_DIR/nfpm-${NFPM_VERSION}"
    tar -xzf "$TARBALL" -C "$CACHE_DIR/nfpm-${NFPM_VERSION}" nfpm
    chmod +x "$NFPM_BIN"
  fi
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/waystone-nfpm-build.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

SRCDIR="$WORK_DIR/src"
PKGDIR="$WORK_DIR/pkg"
mkdir -p "$SRCDIR" "$PKGDIR"

echo "==> Exporting curated public tree..."
if [ -f "$ROOT/tools/export_public.sh" ]; then
  bash "$ROOT/tools/export_public.sh" "$SRCDIR/waystone-${VERSION}"   # private repo: build from the curated public tree
else
  # already the public repo (e.g. GitHub Actions): use the checkout as-is, without git metadata
  mkdir -p "$SRCDIR/waystone-${VERSION}" && rsync -a --exclude .git --exclude dist "$ROOT/" "$SRCDIR/waystone-${VERSION}"/
fi

echo "==> Staging package tree using packaging/arch/PKGBUILD build() and package()..."
(
  set -euo pipefail
  cd "$ROOT/packaging/arch"
  srcdir="$SRCDIR"
  pkgdir="$PKGDIR"
  # shellcheck disable=SC1091
  source "$ROOT/packaging/arch/PKGBUILD"
  pkgver="$VERSION"
  build
  package
)

export WAYSTONE_VERSION="$VERSION"
cd "$WORK_DIR"

DEB_OUT="$OUT_DIR_ABS/waystone_${VERSION}-1_amd64.deb"
RPM_OUT="$OUT_DIR_ABS/waystone-${VERSION}-1.x86_64.rpm"

echo "==> Building .deb package..."
"$NFPM_BIN" package --config "$ROOT/packaging/nfpm/nfpm.yaml" --packager deb --target "$DEB_OUT"

echo "==> Building .rpm package..."
"$NFPM_BIN" package --config "$ROOT/packaging/nfpm/nfpm.yaml" --packager rpm --target "$RPM_OUT"

echo "==> Built packages:"
ls -lh "$DEB_OUT" "$RPM_OUT"
sha256sum "$DEB_OUT" "$RPM_OUT"
