import importlib.util
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from native_services.install_receipts import InstallReceiptRegistry, project_canonical_installed_state
from native_services.provider_receipt_gog import verify_gog_install


class TestGogProviderReceiptAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.install_root = self.base / "games" / "Witcher3"
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.exe = self.install_root / "bin" / "witcher3"
        self.exe.parent.mkdir(parents=True, exist_ok=True)
        self.exe.write_text("#!/bin/sh\necho gog\n", encoding="utf-8")
        os.chmod(self.exe, 0o755)
        self.host = "test-host"
        self.registry = InstallReceiptRegistry(self.base / "receipts.json", host=self.host, clock=lambda: 1000)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _links(self, product="1424375862"):
        return [{"game_id": "canon-gog-1", "provider": "gog", "product_id": product}]

    def _assert_unverified(self, receipt, reason, detail_substr=None):
        self.assertIsNone(receipt[0], receipt)
        self.assertEqual(receipt[1], reason, receipt)
        if detail_substr:
            self.assertIn(detail_substr, receipt[2])

    def test_verified_direct_metadata_registers_and_projects_canonical_state(self):
        meta = {
            "appName": "1424375862",
            "title": "The Witcher 3",
            "install_path": str(self.install_root),
            "executable": "bin/witcher3",
            "version": "4.04",
            "platform": "linux",
        }
        receipt, reason, detail = verify_gog_install(
            meta,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
            account_id="acct1",
        )
        self.assertEqual(reason, "verified", (reason, detail))
        self.assertIsInstance(receipt, dict)
        self.assertEqual(receipt["provider"], "gog")
        self.assertEqual(receipt["product_id"], "1424375862")
        self.assertEqual(receipt["state"], "files_verified")
        self.assertEqual(receipt["playability"], "unknown")
        self.assertEqual(receipt["verified_by"], "gog-installed-metadata")
        self.assertEqual(receipt["selected_product_id"], "1424375862")
        self.assertEqual(receipt["install_path"], str(self.install_root.resolve()))
        self.assertEqual(receipt["executable"], str(self.exe.resolve()))

        stored = self.registry.register(receipt)
        self.assertEqual(stored["provider"], "gog")
        rows = self.registry.query("canon-gog-1", self._links())
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_present"])
        self.assertTrue(rows[0]["files_verified"])
        projection = project_canonical_installed_state(self.registry, "canon-gog-1", self._links())
        self.assertEqual(len(projection["install_receipts"]), 1)
        self.assertTrue(projection["install_receipts"][0]["is_present"])

    def test_verified_from_installed_container_selects_exact_product(self):
        meta = {
            "installed": [
                {"appName": "999", "install_path": str(self.base / "other"), "executable": "x"},
                {
                    "appName": "1424375862",
                    "install_path": str(self.install_root),
                    "playTasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/witcher3"}],
                    "buildId": "build123",
                },
            ]
        }
        receipt, reason, detail = verify_gog_install(
            meta,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified", (reason, detail))
        self.assertEqual(receipt["executable"], str(self.exe.resolve()))
        self.assertEqual(receipt["buildId"], "build123")

    def test_rejects_selected_product_mismatch_and_not_found(self):
        meta = {"appName": "999", "install_path": str(self.install_root), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "selected_product_mismatch",
        )
        container = {"installed": [{"appName": "999", "install_path": str(self.install_root), "executable": "bin/witcher3"}]}
        self._assert_unverified(
            verify_gog_install(container, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "selected_product_not_found",
        )

    def test_rejects_destination_mismatch_and_missing_install(self):
        other = self.base / "other-root"
        other.mkdir()
        meta = {"appName": "1424375862", "install_path": str(other), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "destination_mismatch",
        )
        missing = self.base / "missing"
        meta2 = {"appName": "1424375862", "install_path": str(missing), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta2, selected_product_id="1424375862", selected_destination=str(missing)),
            "install_path_missing",
        )

    def test_rejects_missing_executable_and_incomplete_marker(self):
        meta = {"appName": "1424375862", "install_path": str(self.install_root), "executable": "bin/nope"}
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "executable_missing",
        )
        marker = self.install_root / ".nexus-update-incomplete-v1"
        marker.write_text("x", encoding="utf-8")
        meta_ok = {"appName": "1424375862", "install_path": str(self.install_root), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta_ok, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "incomplete_install",
            "incomplete marker",
        )

    def test_rejects_gogdl_resume_incomplete_marker(self):
        """Heroic library.ts checkForOfflineInstallerChanges returns early when
        <install_path>/.gogdl-resume exists (main@2f5a44f4, ~line 387), so a
        partial gogdl install must never be verified."""
        marker = self.install_root / ".gogdl-resume"
        marker.write_text("{}", encoding="utf-8")
        meta_ok = {"appName": "1424375862", "install_path": str(self.install_root), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta_ok, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "incomplete_install",
            ".gogdl-resume",
        )

    def test_rejects_executable_escape_and_symlink_escape(self):
        outside = self.base / "outside.exe"
        outside.write_text("x", encoding="utf-8")
        meta = {"appName": "1424375862", "install_path": str(self.install_root), "executable": str(outside)}
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "executable_escape",
        )
        link = self.install_root / "bin" / "linkout"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        meta_link = {"appName": "1424375862", "install_path": str(self.install_root), "executable": "bin/linkout"}
        self._assert_unverified(
            verify_gog_install(meta_link, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "executable_escape",
        )

    def test_rejects_ambiguous_or_malformed_primary_tasks(self):
        meta = {
            "appName": "1424375862",
            "install_path": str(self.install_root),
            "playTasks": [
                {"isPrimary": False, "type": "FileTask", "path": "bin/witcher3"},
                {"isPrimary": False, "type": "FileTask", "path": "bin/other"},
            ],
        }
        (self.install_root / "bin" / "other").write_text("x", encoding="utf-8")
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "ambiguous_primary_tasks",
        )
        malformed = {
            "appName": "1424375862",
            "install_path": str(self.install_root),
            "playTasks": "not-a-list",
        }
        self._assert_unverified(
            verify_gog_install(malformed, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "malformed_primary_tasks",
        )

    def test_rejects_dlc_without_executable_proof_and_invalid_ids(self):
        meta = {"appName": "1424375862", "is_dlc": True, "install_path": str(self.install_root)}
        self._assert_unverified(
            verify_gog_install(meta, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "dlc_without_executable_proof",
        )
        meta_ok = {"appName": "not-numeric", "install_path": str(self.install_root), "executable": "bin/witcher3"}
        self._assert_unverified(
            verify_gog_install(meta_ok, selected_product_id="1424375862", selected_destination=str(self.install_root)),
            "metadata_missing_product_id",
        )
        self._assert_unverified(
            verify_gog_install({"appName": "1424375862"}, selected_product_id="abc", selected_destination=str(self.install_root)),
            "invalid_selected_product",
        )
        self._assert_unverified(
            verify_gog_install({"appName": "1424375862"}, selected_product_id="1424375862", selected_destination="relative/path"),
            "unsafe_selected_destination",
        )

    def test_metadata_keyed_by_product_id_container(self):
        meta = {
            "1424375862": {
                "title": "The Witcher 3",
                "install_path": str(self.install_root),
                "executable": "bin/witcher3",
            }
        }
        receipt, reason, _ = verify_gog_install(
            meta,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified")
        self.assertEqual(receipt["product_id"], "1424375862")

    def test_actual_producer_shaped_heroic_installed_info_fixture(self):
        """Producer-shaped: Heroic gog_store/installed InstalledInfo entry (primary source schema)."""
        heroic_installed_entry = {
            "platform": "linux",
            "executable": "bin/witcher3",
            "install_path": str(self.install_root),
            "install_size": "1.2 GB",
            "is_dlc": False,
            "version": "4.04",
            "appName": "1424375862",
            "installedDLCs": [],
            "language": "en-US",
            "buildId": "build-heroic-1",
            "branch": None,
            "pinnedVersion": False,
        }
        # As persisted: array under "installed" (provider_entitlements/gog_store/installed.json shape)
        store_payload = {"installed": [heroic_installed_entry]}
        receipt, reason, detail = verify_gog_install(
            store_payload,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
            account_id="gog-acct",
        )
        self.assertEqual(reason, "verified", (reason, detail, receipt))
        self.assertEqual(receipt["provider"], "gog")
        self.assertEqual(receipt["product_id"], "1424375862")
        self.assertEqual(receipt["buildId"], "build-heroic-1")
        self.assertEqual(receipt["platform"], "linux")
        # Must plug into existing InstallReceiptRegistry (real baseline API)
        recorded = self.registry.register(receipt)
        self.assertEqual(recorded["state"], "files_verified")
        self.assertEqual(recorded["provider"], "gog")
        self.assertEqual(recorded["product_id"], "1424375862")
        self.assertEqual(recorded["verified_by"], "gog-installed-metadata")

    def test_gogdl_import_style_tasks_record_with_explicit_install_path(self):
        """Producer-shaped (gogdl import/get_info style): appName+tasks; requires explicit install_path/exe proof."""
        gogdl_style = {
            "appName": "1424375862",
            "buildId": "build-gogdl-1",
            "title": "The Witcher 3",
            "tasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/witcher3"}],
            "installedLanguage": "en-US",
            "platform": "linux",
            "install_path": str(self.install_root),
            "versionName": "4.04",
        }
        receipt, reason, detail = verify_gog_install(
            gogdl_style,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified", (reason, detail))
        self.assertEqual(receipt["buildId"], "build-gogdl-1")

    def test_goggame_info_no_install_path_with_selected_destination(self):
        """Producer-shaped goggame-<selectedProduct>.info: has gameId/rootGameId/playTasks but no install_path."""
        goggame_info = {
            "gameId": "1424375862",
            "rootGameId": "1424375862",
            "buildId": "55236634408701651",
            "name": "The Witcher 3",
            "playTasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/witcher3", "workingDir": "bin"}],
            "language": "en-US",
        }
        receipt, reason, detail = verify_gog_install(
            goggame_info,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified", (reason, detail, receipt))
        self.assertEqual(receipt["product_id"], "1424375862")
        self.assertEqual(receipt["install_path"], str(self.install_root.resolve()))
        # Direct gogdl semantics: executable = install root + task path; workingDir is separate.
        self.assertEqual(receipt["executable"], str(self.exe.resolve()))
        self.assertEqual(receipt.get("working_dir"), str((self.install_root / "bin").resolve()))
        self.assertEqual(receipt["buildId"], "55236634408701651")

    def test_direct_gogdl_workingdir_not_joined_into_executable_and_traversal_refused(self):
        """Pinned direct gogdl semantics: bin/game.exe with workingDir bin resolves to root/bin/game.exe."""
        game = self.install_root / "bin" / "game.exe"
        game.write_text("MZ", encoding="utf-8")
        # Correct path is root/bin/game.exe, not root/bin/bin/game.exe.
        wrong_join = self.install_root / "bin" / "bin" / "game.exe"
        wrong_join.parent.mkdir(parents=True, exist_ok=True)
        wrong_join.write_text("WRONG", encoding="utf-8")
        meta = {
            "gameId": "1424375862",
            "rootGameId": "1424375862",
            "playTasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/game.exe", "workingDir": "bin"}],
        }
        receipt, reason, detail = verify_gog_install(
            meta,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified", (reason, detail, receipt))
        self.assertEqual(receipt["executable"], str(game.resolve()))
        self.assertEqual(receipt.get("working_dir"), str((self.install_root / "bin").resolve()))

        self._assert_unverified(
            verify_gog_install(
                {**meta, "playTasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/game.exe", "workingDir": "../outside"}]},
                selected_product_id="1424375862",
                selected_destination=str(self.install_root),
            ),
            "working_dir_escape",
        )

    def test_gogdl_import_output_tasks_schema_no_install_path(self):
        """Actual gogdl import/get_info output (tasks schema), no install_path; bounded selected destination supplied separately."""
        gogdl_import_output = {
            "appName": "1424375862",
            "buildId": "build-gogdl-2",
            "title": "The Witcher 3",
            "tasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/witcher3"}],
            "installedLanguage": "en-US",
            "dlcs": [],
            "platform": "linux",
            "versionName": "4.04",
        }
        receipt, reason, detail = verify_gog_install(
            gogdl_import_output,
            selected_product_id="1424375862",
            selected_destination=str(self.install_root),
            host=self.host,
        )
        self.assertEqual(reason, "verified", (reason, detail))
        self.assertEqual(receipt["product_id"], "1424375862")
        self.assertEqual(receipt["buildId"], "build-gogdl-2")

    def test_no_install_path_goggame_info_rejects_unverified_task_proof_and_resume(self):
        base_meta = {"gameId": "1424375862", "rootGameId": "1424375862"}
        self._assert_unverified(
            verify_gog_install(
                {**base_meta, "playTasks": [{"type": "URLTask", "link": "https://example.test"}]},
                selected_product_id="1424375862",
                selected_destination=str(self.install_root),
            ),
            "missing_executable",
        )
        self._assert_unverified(
            verify_gog_install(
                {**base_meta, "playTasks": [{"type": "FileTask", "path": "bin/witcher3"}, {"type": "FileTask", "path": "bin/other"}]},
                selected_product_id="1424375862",
                selected_destination=str(self.install_root),
            ),
            "ambiguous_primary_tasks",
        )
        # no-install_path requires corroborated game-info/tasks schema, not arbitrary records
        self._assert_unverified(
            verify_gog_install(
                {"appName": "1424375862", "title": "The Witcher 3"},
                selected_product_id="1424375862",
                selected_destination=str(self.install_root),
            ),
            "missing_install_path",
        )
        marker = self.install_root / ".gogdl-resume"
        marker.write_text("partial", encoding="utf-8")
        self._assert_unverified(
            verify_gog_install(
                {**base_meta, "playTasks": [{"isPrimary": True, "type": "FileTask", "path": "bin/witcher3"}]},
                selected_product_id="1424375862",
                selected_destination=str(self.install_root),
            ),
            "incomplete_install",
            ".gogdl-resume",
        )


if __name__ == "__main__":
    unittest.main()
