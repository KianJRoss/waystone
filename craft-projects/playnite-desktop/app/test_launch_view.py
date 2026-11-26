"""Offline Qt tests for LaunchView adapter wired to concrete native_services.

Strict safety constraints:
- Injected mock/fake spawn callable; NEVER spawns actual games or processes.
- Tests default state (idle, no automatic launch on init or selection).
- Tests explicit Play per selected installation / target.
- Tests editable profiles (executable, args array, cwd, env).
- Tests ROM / platform emulator associations and ROM scanning from explicit roots.
- Tests choosing between alternatives and setting game overrides.
- Tests missing-profile fix flow, diagnostics, and client handoff error states.
"""
from __future__ import annotations

import os
import json
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication

from launch_view import LaunchView
from native_services.launch_profiles import LaunchProfileStore, LaunchProfile
from native_services.rom_inventory import RomInventoryStore, EmulatorProfile


class FakeProcess:
    def __init__(self, pid: int = 12345):
        self.pid = pid


class FakeSpawn:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.invocations: list[dict[str, Any]] = []

    def __call__(self, argv, **kwargs):
        self.invocations.append({
            'argv': list(argv),
            'kwargs': kwargs,
        })
        return FakeProcess(self.pid)


class TestLaunchViewIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_path = Path(self.temp_dir.name)

        self.profile_root = self.work_path / "profiles"
        self.profile_root.mkdir()
        self.rom_config_dir = self.work_path / "rom_config"
        self.rom_config_dir.mkdir()

        # Create temporary fake executable binaries
        self.fake_steam_bin = self.work_path / "bin" / "steam"
        self.fake_steam_bin.parent.mkdir(parents=True, exist_ok=True)
        self.fake_steam_bin.write_text("#!/bin/sh\nexit 0\n")
        self.fake_steam_bin.chmod(self.fake_steam_bin.stat().st_mode | stat.S_IEXEC)

        self.fake_lichii_bin = self.work_path / "bin" / "lichii"
        self.fake_lichii_bin.write_text("#!/bin/sh\nexit 0\n")
        self.fake_lichii_bin.chmod(self.fake_lichii_bin.stat().st_mode | stat.S_IEXEC)

        # Create a fake ROM directory with a ROM file
        self.fake_rom_root = self.work_path / "roms"
        self.fake_rom_dir = self.fake_rom_root / "switch2"
        self.fake_rom_dir.mkdir(parents=True, exist_ok=True)
        self.fake_rom = self.fake_rom_dir / "Mario Kart World.nup"
        self.fake_rom.write_bytes(b"MOCK_ROM_PAYLOAD_BYTES")

        self.fake_spawn = FakeSpawn()
        self.view = LaunchView(
            launch_profile_root=self.profile_root,
            rom_config_dir=self.rom_config_dir,
            spawn_fn=self.fake_spawn,
        )

        # Update default profiles in view's profile store to point to fake executables
        self.view._profile_store.create_profile(LaunchProfile(
            profile_id='test-steam',
            display_name='Test Steam Client',
            target_type='native_game',
            platform_or_provider='steam',
            executable=str(self.fake_steam_bin),
            argv_template=('steam://rungameid/{game_id}',),
            is_default=True,
            supported_platforms=('steam',),
        ))

        self.view._profile_store.create_profile(LaunchProfile(
            profile_id='test-lichii',
            display_name='Test Lichii',
            target_type='rom',
            platform_or_provider='switch2',
            executable=str(self.fake_lichii_bin),
            argv_template=('{rom}',),
            is_default=True,
            supported_platforms=('switch2', 'switch'),
            supported_extensions=('.nup',),
        ))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_initial_state_no_auto_launch(self):
        """Verify initial view state is completely idle with 0 PID and no spawn calls."""
        self.assertEqual(self.view.selectedTargetId, "")
        self.assertEqual(self.view.launchStatus, "idle")
        self.assertEqual(self.view.lastPid, 0)
        self.assertEqual(self.view.lastError, "")
        self.assertFalse(self.view.canFix)
        self.assertEqual(len(self.fake_spawn.invocations), 0)

    def test_set_target_resolves_profile_without_launching(self):
        """Selecting a target must load profiles and update diagnostics without launching."""
        self.view.setTarget("1086940", {
            'provider': 'steam',
            'title': "Baldur's Gate 3",
        })

        self.assertEqual(self.view.selectedTargetId, "1086940")
        self.assertEqual(self.view.launchStatus, "idle")
        self.assertEqual(self.view.lastPid, 0)
        self.assertEqual(len(self.fake_spawn.invocations), 0)
        self.assertGreaterEqual(self.view.profileCount, 1)

        active = self.view.activeProfile
        self.assertEqual(active['profile_id'], 'test-steam')

    def test_explicit_play_handoff_with_injected_spawn(self):
        """Play explicitly invokes injected spawn, updates status to handed_off, records PID."""
        self.view.setTarget("1086940", {
            'provider': 'steam',
            'title': "Baldur's Gate 3",
        })

        signals_received = []
        self.view.launched.connect(lambda t_id, pid: signals_received.append((t_id, pid)))

        result = self.view.play()

        self.assertEqual(result['status'], 'handed_off')
        self.assertEqual(result['pid'], 4242)
        self.assertEqual(self.view.launchStatus, 'handed_off')
        self.assertEqual(self.view.lastPid, 4242)
        self.assertEqual(len(self.fake_spawn.invocations), 1)

        inv = self.fake_spawn.invocations[0]
        self.assertEqual(inv['argv'], [str(self.fake_steam_bin), 'steam://rungameid/1086940'])
        self.assertFalse(inv['kwargs']['shell'])
        self.assertEqual(signals_received, [("1086940", 4242)])

    def test_production_owner_wiring_wraps_steam_uri_without_running_wrapper_in_test(self):
        self.view._launcher_owner_enabled = True
        self.view.setTarget("1086940", {
            'provider': 'steam', 'title': 'Baldur\'s Gate 3', 'install_path': '/games/steam/1086940',
        })
        result = self.view.play()
        argv = self.fake_spawn.invocations[-1]['argv']
        self.assertEqual(result['status'], 'handed_off')
        self.assertTrue(any(str(a).endswith('launcher_owner_runtime.py') for a in argv))
        self.assertEqual(argv[argv.index('--provider') + 1], 'steam')
        launch_args = json.loads(argv[argv.index('--launch-args-json') + 1])
        self.assertEqual(launch_args, ['steam://rungameid/1086940'])
        self.assertEqual(argv[argv.index('--game-folder') + 1], '/games/steam/1086940')
        self.assertTrue(self.view._active_status_file.endswith('.json'))
        self.assertFalse(Path(self.view._active_status_file).exists())  # fake spawn did not execute it

    def test_missing_executable_error_and_can_fix_flow(self):
        """If executable does not exist, play fails safely and canFix suggests correction."""
        non_existent_exe = str(self.work_path / "does_not_exist.bin")
        self.view._profile_store.create_profile(LaunchProfile(
            profile_id='broken-prof',
            display_name='Broken Profile',
            target_type='native_game',
            platform_or_provider='broken',
            executable=non_existent_exe,
            argv_template=(),
            is_default=True,
        ))

        self.view.setTarget("game_broken", {
            'provider': 'broken',
            'title': 'Broken Game',
        })

        fail_signals = []
        self.view.launchFailed.connect(lambda t_id, err: fail_signals.append((t_id, err)))

        res = self.view.play()

        self.assertEqual(res['status'], 'failed')
        self.assertEqual(self.view.launchStatus, 'failed')
        self.assertTrue(self.view.canFix)
        self.assertEqual(len(self.fake_spawn.invocations), 0)
        self.assertEqual(len(fail_signals), 1)

    def test_editable_profiles_and_alternative_selection(self):
        """User can add, edit, and switch between alternative profiles for a target."""
        self.view.setTarget("1086940", {
            'provider': 'steam',
            'title': 'Steam Game',
        })

        # Add an alternative profile with custom flags
        added = self.view.updateProfile({
            'profile_id': 'profile-custom-steam',
            'display_name': 'Custom Flags Steam',
            'platform_or_provider': 'steam',
            'target_type': 'native_game',
            'executable': str(self.fake_steam_bin),
            'argv_template': ('steam://rungameid/{game_id}', '--vulkan'),
            'cwd': str(self.work_path),
            'env': {'MANGOHUD': '1'},
        })
        self.assertTrue(added)
        self.assertEqual(self.view.activeProfile['profile_id'], 'profile-custom-steam')

        # Switch back to default profile
        switched = self.view.selectProfile('test-steam')
        self.assertTrue(switched)
        self.assertEqual(self.view.activeProfile['profile_id'], 'test-steam')

        # Play with test-steam
        self.view.play()
        self.assertEqual(self.fake_spawn.invocations[-1]['argv'], [str(self.fake_steam_bin), 'steam://rungameid/1086940'])

        # Switch to custom profile and play
        self.view.selectProfile('profile-custom-steam')
        self.view.play()
        last_inv = self.fake_spawn.invocations[-1]
        self.assertEqual(last_inv['argv'], [str(self.fake_steam_bin), 'steam://rungameid/1086940', '--vulkan'])
        self.assertEqual(last_inv['kwargs']['cwd'], str(self.work_path))
        self.assertEqual(last_inv['kwargs']['env']['MANGOHUD'], '1')

    def test_rom_inventory_scan_and_play(self):
        """Scan explicit ROM root and play a discovered ROM using matching platform profile."""
        # Add explicit ROM root and scan
        added = self.view.addRomRoot(str(self.fake_rom_root))
        self.assertTrue(added)
        self.assertEqual(self.view.romRoots, [str(self.fake_rom_root.resolve())])

        count = self.view.scanRoms()
        self.assertGreaterEqual(count, 1)
        self.assertEqual(self.view.romCount, count)

        # Select discovered ROM
        rom_id = self.view.romRecords[0]['rom_id']
        self.view.selectRom(rom_id)

        self.assertEqual(self.view.selectedTargetId, rom_id)
        self.assertEqual(self.view.activeProfile['profile_id'], 'test-lichii')

        # Play the selected ROM
        res = self.view.play()
        self.assertEqual(res['status'], 'handed_off')
        self.assertEqual(self.fake_spawn.invocations[-1]['argv'], [str(self.fake_lichii_bin), str(self.fake_rom)])


if __name__ == '__main__':
    unittest.main()
