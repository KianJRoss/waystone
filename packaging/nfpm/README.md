# Waystone `.deb` and `.rpm` Packaging (nFPM)

Builds Debian/Ubuntu (`.deb`) and Fedora/RHEL (`.rpm`) packages with the exact same read-only `/usr/lib/waystone` layout as `packaging/arch/PKGBUILD`.

## Build
```bash
bash packaging/nfpm/build_deb_rpm.sh --output-dir dist --version 0.1.0
```

Produces:
- `dist/waystone_0.1.0-1_amd64.deb`
- `dist/waystone-0.1.0-1.x86_64.rpm`

## Installed Layout
- `/usr/bin/waystone`
- `/usr/lib/waystone/` (read-only application tree + compiled `UnifiedController.Headless`)
- `/usr/share/applications/waystone.desktop`
- `/usr/share/icons/hicolor/{16x16..512x512,scalable}/apps/waystone.{png,svg}`
- `/usr/lib/udev/rules.d/70-waystone-input.rules`
- `/usr/lib/systemd/user/waystone.service`
- `/usr/share/licenses/waystone-launcher/LICENSE`
