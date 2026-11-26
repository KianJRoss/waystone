import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from native_services.gog_local_receipt import verify_selected_gog_directory


PRODUCT = "1424375862"
OTHER_PRODUCT = "9999999999"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)


def make_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    os.chmod(path, 0o755)


class TestGogLocalReaderReview(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "install"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.td.cleanup()

    def assert_unverified(self, result, reason):
        receipt, got = result
        self.assertIsNone(receipt, result)
        self.assertEqual(got, reason, result)
        return result

    def test_windows_root_info_backslash_filetask_and_exe(self):
        exe = self.root / "bin" / "game.exe"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "name": "Example Game",
            "playTasks": [
                {
                    "isPrimary": True,
                    "type": "FileTask",
                    "category": "game",
                    "path": "bin\\game.exe",
                    "workingDir": "bin",
                }
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)

        receipt, reason = verify_selected_gog_directory(
            PRODUCT, self.root, platform="windows", host="test-host"
        )
        self.assertEqual(reason, "verified", (reason, receipt))
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.get("provider"), "gog")
        self.assertEqual(receipt.get("product_id"), PRODUCT)
        self.assertEqual(receipt.get("install_path"), str(self.root.resolve()))
        self.assertEqual(receipt.get("executable"), str(exe.resolve()))
        self.assertEqual(receipt.get("working_dir"), str((self.root / "bin").resolve()))
        self.assertEqual(receipt.get("verified_by"), "gog-game-local-info")

    def test_linux_game_layout_info(self):
        game_root = self.root / "game"
        exe = game_root / "start.sh"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "name": "Linux Game",
            "playTasks": [
                {
                    "isPrimary": True,
                    "type": "FileTask",
                    "category": "game",
                    "path": "start.sh",
                    "workingDir": ".",
                }
            ],
        }
        write_json(game_root / f"goggame-{PRODUCT}.info", info)

        receipt, reason = verify_selected_gog_directory(
            PRODUCT, self.root, platform="linux", host="test-host"
        )
        self.assertEqual(reason, "verified", (reason, receipt))
        self.assertEqual(receipt.get("executable"), str(exe.resolve()))
        self.assertEqual(receipt.get("install_path"), str(self.root.resolve()))
        self.assertEqual(
            receipt.get("metadata_locator"),
            str((game_root / f"goggame-{PRODUCT}.info").resolve()),
        )

    def test_primary_urltask_with_secondary_filetask_rejected(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {
                    "isPrimary": True,
                    "type": "URLTask",
                    "path": "bin/game",
                },
                {
                    "isPrimary": False,
                    "type": "FileTask",
                    "category": "game",
                    "path": "bin/game",
                    "workingDir": "bin",
                },
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        self.assert_unverified(
            verify_selected_gog_directory(PRODUCT, self.root, platform="windows"),
            "non_executable_task",
        )

    def test_different_gameid_same_root_rejected(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": OTHER_PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/game", "workingDir": "."}
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        self.assert_unverified(
            verify_selected_gog_directory(PRODUCT, self.root, platform="windows"),
            "local_product_mismatch",
        )

    def test_duplicate_json_keys_rejected(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        # duplicate gameId key; reader should reject unsafe metadata
        raw = (
            "{"
            f'"gameId":"{PRODUCT}",'
            f'"gameId":"{PRODUCT}",'
            f'"rootGameId":"{PRODUCT}",'
            '"playTasks":[{"isPrimary":true,"type":"FileTask","path":"bin/game","workingDir":"."}]'
            "}"
        )
        write_bytes(self.root / f"goggame-{PRODUCT}.info", raw)
        result = verify_selected_gog_directory(PRODUCT, self.root, platform="windows")
        # duplicate key is treated as unusable/unsafe metadata
        self.assertIsNone(result[0], result)
        self.assertIn(result[1], ("local_metadata_unusable", "local_metadata_unusable"), result)

    def test_gogdl_resume_marker_means_incomplete(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/game", "workingDir": "."}
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        (self.root / ".gogdl-resume").write_text("inprogress", encoding="utf-8")
        self.assert_unverified(
            verify_selected_gog_directory(PRODUCT, self.root, platform="windows"),
            "incomplete_install",
        )

    def test_escaped_symlink_metadata_rejected(self):
        outside = Path(self.td.name) / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        outside_info = outside / f"goggame-{PRODUCT}.info"
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/game", "workingDir": "."}
            ],
        }
        write_json(outside_info, info)
        link = self.root / f"goggame-{PRODUCT}.info"
        try:
            os.symlink(outside_info, link)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"symlink not supported: {e}")
        result = verify_selected_gog_directory(PRODUCT, self.root, platform="windows")
        self.assertIsNone(result[0], result)
        self.assertIn(
            result[1],
            ("local_metadata_unusable", "metadata_escape", "local_metadata_missing"),
            result,
        )

    def test_ambiguous_tasks_rejected(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": False, "type": "FileTask", "path": "bin/game", "workingDir": "."},
                {"isPrimary": False, "type": "FileTask", "path": "bin/game2", "workingDir": "."},
            ],
        }
        make_exe(self.root / "bin" / "game2")
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        self.assert_unverified(
            verify_selected_gog_directory(PRODUCT, self.root, platform="windows"),
            "ambiguous_local_tasks",
        )

    def test_ambiguous_executable_casefold_rejected(self):
        # create two entries differing only by case to trigger ambiguity in walk
        lower = self.root / "bin" / "game.exe"
        upper = self.root / "bin" / "Game.exe"
        make_exe(lower)
        try:
            if upper.resolve() == lower.resolve() and upper.name == lower.name:
                self.skipTest("case-insensitive filesystem cannot represent casefold ambiguity")
            make_exe(upper)
        except Exception:
            self.skipTest("could not create case-variant executable names")
        names = [p.name for p in (self.root / "bin").iterdir()]
        if "game.exe" not in names or "Game.exe" not in names:
            self.skipTest("filesystem does not preserve distinct case names")
        # Use a declared path whose exact case does not exist, so the casefold
        # walk sees both variants and must reject as ambiguous.
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/GAME.exe", "workingDir": "bin"}
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        result = verify_selected_gog_directory(PRODUCT, self.root, platform="windows")
        self.assert_unverified(result, "ambiguous_executable_case")

    def test_missing_exe_rejected(self):
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/missing.exe", "workingDir": "bin"}
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        result = verify_selected_gog_directory(PRODUCT, self.root, platform="windows")
        self.assertIsNone(result[0], result)
        # Safe rejection required; concrete reason comes from verifier/reader path.
        self.assertIn(
            result[1],
            (
                "executable_missing",
                "ambiguous_executable_case",
                "invalid_local_executable",
                "destination_unavailable",
            ),
            result,
        )

    def test_title_rename_irrelevant(self):
        exe = self.root / "bin" / "game"
        make_exe(exe)
        info = {
            "gameId": PRODUCT,
            "rootGameId": PRODUCT,
            "name": "Completely Different Title After Rename",
            "playTasks": [
                {"isPrimary": True, "type": "FileTask", "path": "bin/game", "workingDir": "."}
            ],
        }
        write_json(self.root / f"goggame-{PRODUCT}.info", info)
        receipt, reason = verify_selected_gog_directory(
            PRODUCT, self.root, platform="windows", host="test-host"
        )
        self.assertEqual(reason, "verified", (reason, receipt))
        self.assertEqual(receipt.get("product_id"), PRODUCT)


if __name__ == "__main__":
    unittest.main()
