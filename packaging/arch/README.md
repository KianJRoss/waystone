# Arch package draft

`PKGBUILD` builds Waystone from the tagged GitHub release (`makepkg -si` here). It is not in the AUR yet; an AUR copy would pin the release tarball's SHA-256 and regenerate `.SRCINFO`. `tools/cleanroom_test.sh` (maintainers) checks a clean-user build and install in a throwaway Arch container. Arch package-source conventions call for package recipe files to carry a separate 0BSD license; [`LICENSE`](LICENSE) covers only the files in this packaging directory, not Waystone itself (AGPL-3.0).

The package installs a read-only app under `/usr/lib/waystone`, its launcher, desktop entry, hicolor icons, a targeted `uaccess` udev rule, and an optional user service (not enabled by the package). Provider helpers are downloaded by Waystone into the user's XDG data directory. The package does not auto-enable the service or modify game/provider credentials.

## Local checks

```bash
makepkg --printsrcinfo > .SRCINFO
python3 test-package.py
```

`test-package.py` exports the curated public source tree, builds the controller engine with the local .NET 10 SDK, sources and executes this recipe's actual `package()` function into a temporary package directory, checks the installed-file manifest, removes write bits from that staged application tree, and launches the app's offscreen smoke test with isolated HOME/XDG paths. It does not install system packages, create virtual input devices, or access a user library. This simulation is not a clean VM install and does not validate AUR hosting.
