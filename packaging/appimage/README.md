# Waystone AppImage Packaging

Builds a self-contained `Waystone-<version>-x86_64.AppImage` from the curated public export tree (`tools/export_public.sh`).

## What is Bundled
- **Python 3.13** standalone relocatable runtime (`uv python`)
- **PySide6** (`QtQuick`, `QtMultimedia`, `QtSvg`, `QtWebEngineQuick`), `httpx`, `PyYAML`, `websockets`, `mcp`
- **UnifiedController.Headless** (`.NET 10` published self-contained for `linux-x64` with invariant globalization)
- **`libevdev.so.2`** and XCB cursor/util helper libraries
- **Waystone application tree** under `/usr/share/waystone` inside the read-only SquashFS image, started via `open-waystone.sh`

All user data, library database, accounts, and settings stay in your XDG directories (`~/.local/share/waystone`, `~/.config/waystone`, `~/.local/state/waystone`). The AppImage never writes into its own directory or next to the `.AppImage` file.

## Build
```bash
bash packaging/appimage/build_appimage.sh --output-dir dist --version 0.1.0
```

## Run
```bash
chmod +x dist/Waystone-0.1.0-x86_64.AppImage
./dist/Waystone-0.1.0-x86_64.AppImage
```
In containers or environments without FUSE (`/dev/fuse`), pass `--appimage-extract-and-run` or set `APPIMAGE_EXTRACT_AND_RUN=1`.

## Virtual Controller Access (`udev` rule)
Virtual controller pads require read/write access to `/dev/uinput` and `/dev/uhid`. On first start, Waystone's setup screen offers a one-click **`Install udev rule (pkexec)`** button that installs `packaging/arch/70-waystone-input.rules` (`TAG+="uaccess"`) to `/etc/udev/rules.d/70-waystone-input.rules` and reloads `udev`.
