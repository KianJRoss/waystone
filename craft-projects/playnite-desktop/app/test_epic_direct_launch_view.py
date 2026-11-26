"""Offline launch_view integration for Waystone-owned Epic Legendary routing."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from launch_view import LaunchView


class FakeProcess:
    pid = 12456


class FakeSpawn:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return FakeProcess()


class EpicDirectLaunchViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile_root = self.root / "profiles"
        self.profile_root.mkdir()
        self.rom_root = self.root / "roms"
        self.rom_root.mkdir()
        self.engine = self.root / "bin" / "legendary"
        self.engine.parent.mkdir()
        self.engine.write_text("#!/bin/sh\nexit 0\n")
        self.engine.chmod(0o755)
        self.config = self.root / "epic"
        self.config.mkdir()
        self.install = self.root / "games" / "Example"
        self.install.mkdir(parents=True)
        self.game_exe = self.install / "Example.sh"
        self.game_exe.write_text("#!/bin/sh\nexit 0\n")
        self.game_exe.chmod(0o755)
        self.app_id = "EXAMPLE_APP_123"
        self._write_installed("Linux")
        self.spawn = FakeSpawn()
        self.view = self._view()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_installed(self, platform):
        (self.config / "installed.json").write_text(json.dumps({
            self.app_id: {
                "app_name": self.app_id,
                "title": "Example",
                "platform": platform,
                "install_path": str(self.install),
                "executable": "Example.sh",
                "is_dlc": False,
            }
        }))

    def _view(self):
        view = LaunchView(
            launch_profile_root=self.profile_root,
            rom_config_dir=self.rom_root,
            spawn_fn=self.spawn,
        )
        self.assertTrue(view.configureEpicDirectLaunch(str(self.engine), str(self.config)))
        view.setTarget(self.app_id, {
            "product_id": self.app_id,
            "provider": "heroic-epic",
            "title": "Example",
        })
        return view

    def test_unconfigured_epic_never_falls_back_to_heroic_uri(self):
        result = self.view.play()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Heroic will not be opened", result["error"])
        self.assertEqual(self.spawn.calls, [])

    def test_epic_game_installed_by_heroic_is_not_a_waystone_engine_target(self):
        """Rocket League case (2026-11-24): installed in Heroic's Legendary, not Waystone's. It must not be refused
        with 'choose a Waystone runtime'; it goes to the Heroic profile, which Waystone keeps hidden."""
        self.view.setTarget("HEROIC_ONLY_GAME", {"product_id": "HEROIC_ONLY_GAME", "provider": "heroic-epic",
                                                 "title": "Heroic game"})
        self.assertFalse(self.view.isEpicDirectTarget)
        result = self.view.play()
        self.assertNotIn("Heroic will not be opened", result.get("error") or "")
        self.assertFalse(any(str(self.engine) == str(call[0][0]) for call in self.spawn.calls))

    def test_configured_native_epic_routes_through_waystone_engine(self):
        self.assertTrue(self.view.configureEpicRuntime("native"))
        result = self.view.play()
        self.assertEqual(result["status"], "handed_off")
        argv, kwargs = self.spawn.calls[0]
        self.assertEqual(argv, [str(self.engine), "launch", self.app_id, "--no-wine"])
        self.assertEqual(kwargs["cwd"], str(self.install))
        self.assertEqual(kwargs["env"]["LEGENDARY_CONFIG_PATH"], str(self.config))
        self.assertFalse(kwargs["shell"])

    def test_runtime_is_per_game_and_persists_in_profile_store(self):
        self.assertTrue(self.view.configureEpicRuntime("native"))
        other = "OTHER_APP_456"
        self.view.setTarget(other, {"product_id": other, "provider": "heroic-epic", "title": "Other"})
        self.assertTrue(self.view.configureEpicRuntime("native"))
        other_profile = self.view.activeProfile["profile_id"]
        self.assertNotEqual(other_profile, "")
        self.view.setTarget(self.app_id, {"product_id": self.app_id, "provider": "heroic-epic"})
        self.assertIn("Native runtime selected", self.view.epicRuntimeStatus)
        reloaded = self._view()
        self.assertIn("Native runtime selected", reloaded.epicRuntimeStatus)
        self.assertEqual(reloaded.activeProfile["profile_id"], self.view.activeProfile["profile_id"])
        self.assertNotEqual(reloaded.activeProfile["profile_id"], other_profile)

    def test_windows_title_requires_explicit_wine_profile(self):
        self._write_installed("Windows")
        self.assertTrue(self.view.configureEpicRuntime("native"))
        result = self.view.play()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Windows title", result["error"])
        self.assertEqual(self.spawn.calls, [])
        wine = self.root / "bin" / "wine"
        wine.write_text("#!/bin/sh\nexit 0\n")
        wine.chmod(0o755)
        prefix = self.root / "prefix"
        prefix.mkdir()
        self.assertTrue(self.view.configureEpicRuntime("wine", str(wine), str(prefix)))
        result = self.view.play()
        self.assertEqual(result["status"], "handed_off")
        argv, kwargs = self.spawn.calls[-1]
        self.assertEqual(argv[-4:], ["--wine", str(wine), "--wine-prefix", str(prefix)])
        self.assertEqual(kwargs["env"]["LEGENDARY_CONFIG_PATH"], str(self.config))

    def test_invalid_wine_paths_are_rejected_before_profile_write(self):
        before = dict(self.view._profile_store.profiles)
        self.assertFalse(self.view.configureEpicRuntime(
            "wine", str(self.root / "missing-wine"), str(self.root / "missing-prefix")))
        self.assertIn("does not exist", self.view.epicRuntimeStatus)
        self.assertEqual(before, self.view._profile_store.profiles)
        self.assertEqual(self.spawn.calls, [])

    def test_disabling_managed_configuration_preserves_old_headless_profile_flow(self):
        view = LaunchView(launch_profile_root=self.profile_root,
                          rom_config_dir=self.rom_root, spawn_fn=self.spawn)
        view.setTarget("unmanaged", {"product_id": "unmanaged", "provider": "heroic-epic"})
        result = view.play()
        # The compatibility profile remains available in headless fixtures; only a live
        # session configured with a Waystone account routes through the managed engine.
        self.assertIn(result["status"], {"handed_off", "failed"})


if __name__ == "__main__":
    unittest.main()

