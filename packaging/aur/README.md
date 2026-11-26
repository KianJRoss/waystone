# AUR package `waystone-launcher` (ready, not submitted yet)

`waystone` on the AUR is a different project (a Path of Exile 2 overlay), hence `waystone-launcher`, with
`conflicts=('waystone')`. These are the exact files for the AUR repository: `PKGBUILD` (release tarball SHA-256 pinned)
and `.SRCINFO`. Tested in a throwaway archlinux container (`aur-cleanroom-run.sh`): GitHub download + checksum,
build, namcap clean, install to /usr/lib/waystone, first start as a new user.

To publish (needs an AUR account with the public key from `~/.ssh/aur.pub` on the maintainer's machine; AUR
registration was paused on 2026-11-26):

    git clone ssh://aur@aur.archlinux.org/waystone-launcher.git
    cp PKGBUILD .SRCINFO LICENSE waystone-launcher/ && cd waystone-launcher
    git add -A && git commit -m "waystone-launcher 0.1.0-1" && git push

New release: bump pkgver, set the new tarball sha256sum, `makepkg --printsrcinfo > .SRCINFO`, rerun the clean-room test.
