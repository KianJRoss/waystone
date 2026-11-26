#!/usr/bin/env python3
"""Non-root PKGBUILD package() simulation and read-only install smoke test.

This exercises the real package() function against a public export in a temporary
directory. It does not contact GitHub/AUR or create system devices/services.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "packaging/arch"


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="waystone-pkgtest-") as tmp:
        tmpdir = Path(tmp)
        public = tmpdir / "waystone-0.1.0"
        pkgdir = tmpdir / "pkg"
        srcdir = tmpdir / "src"
        home = tmpdir / "home"
        srcdir.mkdir(); home.mkdir()
        run(str(ROOT / "tools/export_public.sh"), str(public), cwd=ROOT)

        # Source the release tree, then run the recipe's actual build() and package() functions.
        staging = srcdir / "waystone-0.1.0"
        shutil.copytree(public, staging)
        script = f'''set -euo pipefail
srcdir={str(srcdir)!r}
pkgdir={str(pkgdir)!r}
source {str(ARCH / "PKGBUILD")!r}
build
package
'''
        run("bash", "-c", script, cwd=ARCH,
            env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1"})

        expected = [
            "usr/bin/waystone",
            "usr/share/applications/waystone.desktop",
            "usr/lib/udev/rules.d/70-waystone-input.rules",
            "usr/lib/systemd/user/waystone.service",
            "usr/lib/waystone/open-waystone.sh",
            "usr/lib/waystone/craft-projects/playnite-desktop/app/app.py",
            "usr/lib/waystone/components/controller_studio/headless/publish/UnifiedController.Headless",
            "usr/share/icons/hicolor/512x512/apps/waystone.png",
            "usr/share/licenses/waystone-launcher/LICENSE",
        ]
        missing = [rel for rel in expected if not (pkgdir / rel).exists()]
        if missing:
            raise SystemExit("missing package files: " + ", ".join(missing))

        # Ensure all installed files are read-only and launch a synthetic smoke test
        # with isolated XDG/HOME. The process may write user data, never /usr/share.
        for path in pkgdir.rglob("*"):
            path.chmod((path.stat().st_mode & ~0o222) if path.is_file() else (path.stat().st_mode & ~0o222))
        for directory in sorted((p for p in pkgdir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            directory.chmod(0o555)
        pkgdir.chmod(0o555)
        xdg_data = tmpdir / "xdg-data"; xdg_config = tmpdir / "xdg-config"; xdg_state = tmpdir / "xdg-state"
        for path in (xdg_data, xdg_config, xdg_state): path.mkdir()
        env = {
            **os.environ,
            "HOME": str(home), "XDG_DATA_HOME": str(xdg_data), "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_STATE_HOME": str(xdg_state), "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(tmpdir / "pycache"),
        }
        app = pkgdir / "usr/lib/waystone/craft-projects/playnite-desktop/app/app.py"
        run("python3", "-B", str(app), "--smoke-test", cwd=tmpdir, env=env)
        if any(p.suffix in {".pyc", ".pyo"} for p in (pkgdir / "usr/lib/waystone").rglob("*")):
            raise SystemExit("Python bytecode appeared in the installed application tree")
        print("PASS package() file manifest, controller build, and read-only smoke test")
        print("PASS isolated user data roots:", xdg_data, xdg_config, xdg_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
