"""AI-assistant mod tools: install preview/apply/undo into a game folder and an emulator, offline."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_control import OrganizerControl
from native_services.organizer_store import OrganizerStore
from tools import waystone_mcp


class ModToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"; self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / ".local/share"),
                                                "XDG_CACHE_HOME": str(self.home / ".cache")})
        self.env.start()
        control = OrganizerControl(organizer_store=OrganizerStore.create(self.tmp / "o.sqlite", ["g1"]),
                                   catalog_link_store=CatalogLinkStore.create(self.tmp / "l.sqlite", ["g1"]))
        self.app = waystone_mcp.create_mcp_app(control)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    async def call(self, name, args):
        res = await self.app.call_tool(name, args)
        return res, (json.loads(res.content[0].text) if not res.is_error else res.content[0].text)

    async def test_game_install_preview_then_apply_then_undo(self):
        game = self.tmp / "Skyrim"; (game / "Data").mkdir(parents=True); (game / "Data/Skyrim.esm").write_text("v")
        z = self.tmp / "mod.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Cool/Cool.esp", "p")
        res, data = await self.call("waystone_install_mod", {"archive_path": str(z), "target": "game", "game_dir": str(game)})
        self.assertFalse(res.is_error, data)
        self.assertEqual(data["preview"]["target_root"], str(game / "Data"))
        self.assertFalse((game / "Data/Cool.esp").exists())            # preview wrote nothing
        res, data = await self.call("waystone_install_mod", {"archive_path": str(z), "target": "game", "game_dir": str(game), "apply": True})
        self.assertTrue((game / "Data/Cool.esp").exists())
        res, undone = await self.call("waystone_undo_mod_install", {"journal_path": data["journal"]})
        self.assertEqual(undone["removed"], 1)
        self.assertFalse((game / "Data/Cool.esp").exists())

    async def test_emulator_install_preview(self):
        z = self.tmp / "totk.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("0100F2C0115B6000/Fix/exefs/a.pchtxt", "x")
        res, data = await self.call("waystone_install_mod", {"archive_path": str(z), "target": "eden", "mod_name": "Fix"})
        self.assertFalse(res.is_error, data)
        self.assertTrue(data["preview"]["target_root"].endswith("eden/load/0100F2C0115B6000/Fix"))

    async def test_nexus_download_points_to_the_site(self):
        res, data = await self.call("waystone_download_mod", {"site": "nexus", "mod_id": "12604", "game_domain": "skyrimspecialedition", "file_id": "749043"})
        self.assertEqual(data["action"], "open_in_browser")
        self.assertIn("nmm=1", data["url"])


if __name__ == "__main__":
    unittest.main()
