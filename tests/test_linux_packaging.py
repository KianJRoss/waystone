"""Unit tests for W24 Linux packaging (AppImage, .deb/.rpm via nFPM, and cleanroom scripts)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class LinuxPackagingTests(unittest.TestCase):
    def test_appimage_scripts_exist_and_pass_syntax_check(self):
        apprun = REPO_ROOT / "packaging" / "appimage" / "AppRun"
        builder = REPO_ROOT / "packaging" / "appimage" / "build_appimage.sh"
        cleanroom = REPO_ROOT / "tools" / "appimage_cleanroom_test.sh"

        for script in (apprun, builder, cleanroom):
            self.assertTrue(script.is_file(), f"Missing {script}")
            self.assertTrue(os.access(script, os.X_OK), f"Not executable: {script}")
            res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(res.returncode, 0, f"{script} syntax error: {res.stderr}")

        apprun_text = apprun.read_text(encoding="utf-8")
        self.assertIn("WAYSTONE_PYTHON", apprun_text)
        self.assertIn("DOTNET_ROOT", apprun_text)
        self.assertIn("open-waystone.sh", apprun_text)

        builder_text = builder.read_text(encoding="utf-8")
        for needle in ("PySide6", "httpx", "PyYAML", "libevdev.so.2", "UnifiedController.Headless", "mksquashfs"):
            self.assertIn(needle, builder_text)

    def test_nfpm_deb_and_rpm_config_matches_arch_layout(self):
        nfpm_yaml = REPO_ROOT / "packaging" / "nfpm" / "nfpm.yaml"
        nfpm_script = REPO_ROOT / "packaging" / "nfpm" / "build_deb_rpm.sh"

        self.assertTrue(nfpm_yaml.is_file())
        self.assertTrue(nfpm_script.is_file())
        self.assertTrue(os.access(nfpm_script, os.X_OK))
        res = subprocess.run(["bash", "-n", str(nfpm_script)], capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)

        cfg = yaml.safe_load(nfpm_yaml.read_text(encoding="utf-8"))
        self.assertEqual(cfg["name"], "waystone")
        self.assertEqual(cfg["license"], "AGPL-3.0-only")
        destinations = {item["dst"] for item in cfg.get("contents", [])}
        self.assertEqual(
            destinations,
            {
                "/usr/bin/waystone",
                "/usr/share/applications/waystone.desktop",
                "/usr/lib/udev/rules.d/70-waystone-input.rules",
                "/usr/lib/systemd/user/waystone.service",
                "/usr/share/icons/hicolor",
                "/usr/share/licenses/waystone-launcher",
                "/usr/lib/waystone",
            },
        )

    def test_github_workflow_includes_ubuntu_packages_job(self):
        workflow_path = REPO_ROOT / ".github" / "workflows" / "tests.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        jobs = workflow.get("jobs", {})
        self.assertIn("packages", jobs)
        pkg_job = jobs["packages"]
        self.assertEqual(pkg_job.get("runs-on"), "ubuntu-24.04")
        steps_text = "\n".join(str(s.get("run", "")) for s in pkg_job.get("steps", []))
        self.assertIn("packaging/appimage/build_appimage.sh", steps_text)
        self.assertIn("packaging/nfpm/build_deb_rpm.sh", steps_text)
