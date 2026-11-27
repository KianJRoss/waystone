"""Fake/fixture-only backend tests for W9a: Mod debugger crash-log readers (Crash Logger/Trainwreck/.NET Script Framework), culprit ranking, and pure load-order checks."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from native_services.mod_log_analyzer import ModLogAnalyzer
from native_services.skyrim_load_order import (
    FULL_PLUGIN_SLOT_LIMIT,
    LIGHT_PLUGIN_SLOT_LIMIT,
    SkyrimLoadOrderSorter,
    SkyrimPlugin,
    read_plugin_header_masters,
)

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "crash_logs"


def _staged_mods_fixture() -> list[dict]:
    return [
        {
            "mod_id": "mod-precision",
            "name": "Precision - Accurate Melee Collisions",
            "version": "2.0.4",
            "enabled": True,
            "installed_files": ["SKSE/Plugins/Precision.dll", "Precision.esp"],
        },
        {
            "mod_id": "mod-tdm",
            "name": "True Directional Movement",
            "version": "2.2.5",
            "enabled": True,
            "installed_files": ["SKSE/Plugins/TrueDirectionalMovement.dll", "TrueDirectionalMovement.esp"],
        },
        {
            "mod_id": "mod-lux",
            "name": "Lux Lighting Overhaul",
            "version": "6.5",
            "enabled": True,
            "installed_files": ["Lux.esp", "Lux - Resources.esp"],
        },
        {
            "mod_id": "mod-combat-music",
            "name": "Combat Music Fix NG",
            "version": "1.1.0",
            "enabled": True,
            "installed_files": ["SKSE/Plugins/CombatMusicFix.dll"],
        },
        {
            "mod_id": "mod-po3-tweaks",
            "name": "powerofthree's Tweaks",
            "version": "1.11.0",
            "enabled": True,
            "installed_files": ["SKSE/Plugins/po3_Tweaks.dll"],
        },
        {
            "mod_id": "mod-northern-roads",
            "name": "Northern Roads",
            "version": "1.3.0",
            "enabled": True,
            "installed_files": ["Northern Roads.esp"],
        },
        {
            "mod_id": "mod-hdtsmp",
            "name": "Faster HDT-SMP",
            "version": "1.50.9",
            "enabled": True,
            "installed_files": ["Data/SKSE/Plugins/hdtSMP64.dll"],
        },
        {
            "mod_id": "mod-obis",
            "name": "OBIS SE - Organized Bandits",
            "version": "2.6",
            "enabled": True,
            "installed_files": ["OBIS SE.esp"],
        },
    ]


def _build_fake_tes4_plugin_bytes(*, is_master: bool = False, is_light: bool = False, masters: list[str] | None = None) -> bytes:
    subrecords = bytearray()
    for m in (masters or []):
        m_bytes = m.encode("utf-8") + b"\x00"
        subrecords.extend(b"MAST" + struct.pack("<H", len(m_bytes)) + m_bytes)
        subrecords.extend(b"DATA" + struct.pack("<HQ", 8, 0))
    flags = (0x0001 if is_master else 0) | (0x0200 if is_light else 0)
    header = b"TES4" + struct.pack("<IIIII", len(subrecords), flags, 0, 0, 0)
    return header + bytes(subrecords)


class TestModDebuggerW9aBackend(unittest.TestCase):
    def test_crash_logger_trainwreck_and_net_script_framework_fixtures(self):
        analyzer = ModLogAnalyzer()
        known_mods = _staged_mods_fixture()

        # 1. Crash Logger SSE/AE/VR fixture
        cl_path = FIXTURES_DIR / "crash-2026-11-26-19-42-10.log"
        cl_report = analyzer.analyze_file(cl_path, known_mods=known_mods)
        self.assertFalse(cl_report.healthy)
        self.assertEqual(cl_report.crash_format, "crash_logger_sse")
        self.assertEqual(cl_report.when, "2026-11-26 19:42:10")
        self.assertEqual(cl_report.exception, "EXCEPTION_ACCESS_VIOLATION")
        self.assertIn("0x7FF6A1234560", cl_report.address)
        self.assertIn("Precision.dll", cl_report.address)
        self.assertGreaterEqual(len(cl_report.culprits), 3)
        # Top ranked culprit is Precision.dll (exception site + top call stack frames)
        top_cl = cl_report.culprits[0]
        self.assertEqual(top_cl["artifact"], "Precision.dll")
        self.assertEqual(top_cl["mod_id"], "mod-precision")
        self.assertEqual(top_cl["mod_name"], "Precision - Accurate Melee Collisions")
        self.assertEqual(top_cl["recommended_fix"]["action"], "disable_mod")
        # TDM and Lux.esp are also identified and ranked
        culprit_mod_ids = [c["mod_id"] for c in cl_report.culprits if c.get("mod_id")]
        self.assertIn("mod-tdm", culprit_mod_ids)
        self.assertIn("mod-lux", culprit_mod_ids)
        # Base binaries/official masters are excluded
        culprit_artifacts = {c["artifact"].lower() for c in cl_report.culprits}
        self.assertNotIn("skyrimse.exe", culprit_artifacts)
        self.assertNotIn("ntdll.dll", culprit_artifacts)
        self.assertNotIn("skyrim.esm", culprit_artifacts)
        self.assertIn("Precision - Accurate Melee Collisions", cl_report.summary)

        # 2. Trainwreck fixture
        tw_path = FIXTURES_DIR / "crash-2026-11-26-20-15-05-trainwreck.log"
        tw_report = analyzer.analyze_file(tw_path, known_mods=known_mods)
        self.assertFalse(tw_report.healthy)
        self.assertEqual(tw_report.crash_format, "trainwreck")
        self.assertEqual(tw_report.when, "2026-11-26 20:15:05")
        self.assertEqual(tw_report.exception, "EXCEPTION_INT_DIVIDE_BY_ZERO")
        self.assertEqual(tw_report.culprits[0]["mod_id"], "mod-combat-music")
        self.assertEqual(tw_report.culprits[1]["mod_id"], "mod-po3-tweaks")
        self.assertIn("mod-northern-roads", [c["mod_id"] for c in tw_report.culprits])

        # 3. .NET Script Framework fixture (exception inside SkyrimSE.exe, culprits in callstack + Possible relevant objects)
        nsf_path = FIXTURES_DIR / "Crash_2026_11_26_18-30-15.txt"
        nsf_report = analyzer.analyze_file(nsf_path, known_mods=known_mods)
        self.assertFalse(nsf_report.healthy)
        self.assertEqual(nsf_report.crash_format, "net_script_framework")
        self.assertEqual(nsf_report.when, "26 Nov 2026 18:30:15.412")
        nsf_mod_ids = [c["mod_id"] for c in nsf_report.culprits]
        self.assertEqual(nsf_mod_ids[0], "mod-hdtsmp")
        self.assertIn("mod-obis", nsf_mod_ids)

    def test_discover_logs_in_proton_prefix_and_overwrite_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compat_data = root / "compatdata" / "489830"
            skse_docs = compat_data / "pfx/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE"
            skse_docs.mkdir(parents=True)
            cl_file = skse_docs / "crash-2026-11-26-19-42-10.log"
            cl_file.write_text((FIXTURES_DIR / "crash-2026-11-26-19-42-10.log").read_text(encoding="utf-8"), encoding="utf-8")

            profile_dir = root / "mo2_profile"
            nsf_dir = profile_dir / "overwrite/NetScriptFramework/Crash"
            nsf_dir.mkdir(parents=True)
            nsf_file = nsf_dir / "Crash_2026_11_26_18-30-15.txt"
            nsf_file.write_text((FIXTURES_DIR / "Crash_2026_11_26_18-30-15.txt").read_text(encoding="utf-8"), encoding="utf-8")

            now = time.time()
            os.utime(nsf_file, (now - 120, now - 120))
            os.utime(cl_file, (now, now))

            analyzer = ModLogAnalyzer()
            found = analyzer.discover_logs(compat_data_path=compat_data, profile_directory=profile_dir)
            self.assertEqual([p.name for p in found[:2]], [cl_file.name, nsf_file.name])

    def test_pure_load_order_checks_and_tes4_header_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "Data"
            data_dir.mkdir(parents=True)
            # Write a fake binary TES4 plugin header for Lux.esp requiring Skyrim.esm, MissingMaster.esm, and DisabledMaster.esm
            (data_dir / "Lux.esp").write_bytes(
                _build_fake_tes4_plugin_bytes(
                    is_master=False,
                    is_light=False,
                    masters=["Skyrim.esm", "MissingMaster.esm", "DisabledMaster.esm"],
                )
            )
            is_m, is_l, hdr_masters = read_plugin_header_masters(data_dir / "Lux.esp")
            self.assertFalse(is_m)
            self.assertFalse(is_l)
            self.assertEqual(hdr_masters, ["Skyrim.esm", "MissingMaster.esm", "DisabledMaster.esm"])

            plugins = [
                SkyrimPlugin(name="EarlyPatch.esp", enabled=True, masters=["Skyrim.esm", "LateMaster.esm"]),
                SkyrimPlugin(name="Skyrim.esm", enabled=True, is_master=True),
                SkyrimPlugin(name="DisabledMaster.esm", enabled=False, is_master=True),
                SkyrimPlugin(name="LateMaster.esm", enabled=True, is_master=True),
                SkyrimPlugin(name="Lux.esp", enabled=True),  # masters read from TES4 header in data_dir
                SkyrimPlugin(name="ExtraLight1.esl", enabled=True, is_light=True),
                SkyrimPlugin(name="ExtraLight2.esl", enabled=True, is_light=True),
            ]
            sorter = SkyrimLoadOrderSorter(data_dir=data_dir)
            report = sorter.check_load_order(
                plugins,
                known_mods=_staged_mods_fixture(),
                full_slot_limit=3,
                light_slot_limit=1,
            )
            self.assertFalse(report.healthy)
            kinds = {issue.kind for issue in report.issues}
            self.assertIn("missing_master", kinds)
            self.assertIn("disabled_master", kinds)
            self.assertIn("order_violation", kinds)
            self.assertIn("slot_limit_exceeded", kinds)

            missing_issue = next(i for i in report.issues if i.kind == "missing_master")
            self.assertEqual(missing_issue.culprit_mod_id, "mod-lux")
            self.assertIsNotNone(missing_issue.recommended_fix)
            self.assertEqual(missing_issue.recommended_fix["action"], "disable_mod")

            # Verify healthy load order case
            healthy_plugins = [
                SkyrimPlugin(name="Skyrim.esm", enabled=True, is_master=True),
                SkyrimPlugin(name="Update.esm", enabled=True, is_master=True, masters=["Skyrim.esm"]),
                SkyrimPlugin(name="Precision.esp", enabled=True, is_light=True, masters=["Skyrim.esm"]),
            ]
            healthy_report = SkyrimLoadOrderSorter().check_load_order(healthy_plugins, known_mods=_staged_mods_fixture())
            self.assertTrue(healthy_report.healthy)
            self.assertEqual(healthy_report.active_full_count, 2)
            self.assertEqual(healthy_report.full_slot_limit, FULL_PLUGIN_SLOT_LIMIT)
            self.assertEqual(healthy_report.active_light_count, 1)
            self.assertEqual(healthy_report.light_slot_limit, LIGHT_PLUGIN_SLOT_LIMIT)
            self.assertEqual(len(healthy_report.issues), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
