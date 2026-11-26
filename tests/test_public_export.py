"""Tests for public export boundary and optional private_addons isolation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = REPO_ROOT / "tools" / "export_public.sh"


class PublicExportTests(unittest.TestCase):
    def test_core_modules_import_and_operate_without_private_addons(self):
        from native_services import (
            catalog_providers,
            catalog_reconciliation,
            install_receipts,
            installation_library,
            mod_catalog,
            organizer_control,
            provider_jobs,
            waystone_paths,
        )

        self.assertIn("epic", provider_jobs.SUPPORTED_PROVIDERS)
        self.assertIn("gog", provider_jobs.SUPPORTED_PROVIDERS)
        self.assertIn("amazon", provider_jobs.SUPPORTED_PROVIDERS)
        self.assertIn("steam", installation_library.PROVIDERS)
        self.assertIn("xbox", install_receipts.PROVIDERS)
        self.assertEqual(mod_catalog.platforms_for({"title": "ROM", "source": "Emulation"}), ["gamebanana"])
        self.assertTrue(callable(waystone_paths.data_dir))
        self.assertTrue(hasattr(catalog_providers, "CatalogSnapshot"))
        self.assertTrue(callable(catalog_reconciliation.load_assignments))
        self.assertTrue(hasattr(organizer_control, "OrganizerControl"))

    def test_export_script_behavior_matches_tree_mode(self):
        has_addons = importlib.util.find_spec("private_addons") is not None
        if not EXPORT_SCRIPT.exists():
            # Running inside the exported public tree: private_addons and export_public.sh must be absent
            self.assertFalse(has_addons)
            self.assertFalse((REPO_ROOT / "private_addons").exists())
            self.assertFalse((REPO_ROOT / "tools" / "public_export.txt").exists())
            return

        import tempfile
        with tempfile.TemporaryDirectory(prefix="waystone-export-test-") as tmp:
            dest = Path(tmp) / "public-copy"
            res = subprocess.run(
                ["bash", str(EXPORT_SCRIPT), str(dest)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse((dest / "private_addons").exists())
            self.assertFalse((dest / "research").exists())
            self.assertFalse((dest / "docs").exists())
            self.assertFalse((dest / "tools" / "export_public.sh").exists())
            self.assertTrue((dest / "craft-projects" / "playnite-desktop" / "app" / "app.py").is_file())
