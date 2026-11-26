"""Mods > Browse bridge: platform choice per game, remembered game matches, search/paging, errors."""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

from mod_browser_view import ModBrowserView
from native_services import mod_catalog


def item(platform, n):
    return {"platform": platform, "mod_id": str(n), "title": f"Mod {n}", "summary": "", "author": "a",
            "updated_at": "", "thumbnail_url": "", "downloads": n, "likes": 0, "url": f"https://x/{n}", "game": "G"}


class FakeCatalog(types.SimpleNamespace):
    def __init__(self):
        super().__init__(calls=[], nexus_games=[], gb_games=[], fail=None)

    def _log(self, *a):
        self.calls.append(a)
        if self.fail:
            raise mod_catalog.CatalogError(self.fail)

    def search_modrinth(self, query, limit=20, offset=0):
        self._log("modrinth", query, offset)
        return {"total": 45, "items": [item("modrinth", offset + i) for i in range(20 if offset < 40 else 5)]}

    def search_nexus(self, query, game_domain=None, count=20, offset=0):
        self._log("nexus", query, game_domain, offset)
        return {"total": 2, "items": [item("nexus", 1), item("nexus", 2)]}

    def search_gamebanana(self, query="", game_id=None, page=1):
        self._log("gamebanana", query, game_id, page)
        return {"total": 1, "items": [item("gamebanana", 7)]}

    def find_nexus_games(self, name):
        self._log("find_nexus", name)
        return self.nexus_games

    def find_gamebanana_games(self, name):
        self._log("find_gb", name)
        return self.gb_games


GAMES = [
    {"game_id": "pc-1", "title": "Skyrim Special Edition", "platforms": ["PC (Windows)"], "source": "Steam"},
    {"game_id": "emu-1", "title": "The Legend of Zelda: Tears of the Kingdom", "platforms": ["Nintendo Switch"], "source": "Emulation"},
    {"game_id": "mc-1", "title": "Minecraft for Windows", "platforms": ["PC (Windows)"], "source": "Xbox"},
]


class ModBrowserViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["mod-browser-tests"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "catalog-games.json"
        self.cat = FakeCatalog()
        self.view = ModBrowserView(GAMES, store_path=self.store, catalog=self.cat, synchronous=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pc_game_defaults_to_nexus_and_exact_match_is_remembered(self):
        self.cat.nexus_games = [{"id": 1704, "domain": "skyrimspecialedition", "name": "Skyrim Special Edition"},
                                {"id": 110, "domain": "skyrim", "name": "Skyrim"}]
        self.view.selectGame("pc-1")
        self.assertEqual([p["id"] for p in self.view.platforms], ["nexus", "gamebanana"])
        self.assertEqual(self.view.platform, "nexus")
        # Skyrim SE has a shipped recipe: its Nexus site is known, so no lookup is needed.
        self.assertNotIn("find_nexus", [c[0] for c in self.cat.calls])
        self.assertEqual(self.view.recipe["manager"], "mo2")
        self.assertEqual(self.cat.calls[-1], ("nexus", "", "skyrimspecialedition", 0))
        saved = json.loads(self.store.read_text())
        self.assertEqual(saved["pc-1"]["nexus"]["domain"], "skyrimspecialedition")
        # second visit: no lookup again
        self.cat.calls.clear()
        self.view.selectGame("pc-1")
        self.assertNotIn("find_nexus", [c[0] for c in self.cat.calls])

    def test_ambiguous_match_asks_the_user_instead_of_guessing(self):
        self.cat.gb_games = [{"id": 7617, "name": "The Legend of Zelda: Tears of the Kingdom (Switch)"},
                             {"id": 1, "name": "Kingdom Hearts"}]
        self.view.selectGame("emu-1")
        self.assertEqual(self.view.platform, "gamebanana")
        self.assertEqual(len(self.view.candidates), 2)
        self.assertNotIn("gamebanana", [c[0] for c in self.cat.calls])  # no search until the user picks
        self.view.chooseCandidate(0)
        self.assertEqual(self.view.candidates, [])
        self.assertEqual(self.cat.calls[-1], ("gamebanana", "", 7617, 1))
        self.assertEqual(self.view.total, 1)

    def test_minecraft_uses_modrinth_and_load_more_pages(self):
        self.view.selectGame("mc-1")
        self.assertEqual(self.view.platform, "modrinth")
        self.assertEqual(self.view.matchedGame, "Minecraft")
        self.assertEqual(len(self.view.items), 20)
        self.assertTrue(self.view.canLoadMore)
        self.view.loadMore()
        self.view.loadMore()
        self.assertEqual(len(self.view.items), 45)
        self.assertFalse(self.view.canLoadMore)
        self.assertEqual([c[2] for c in self.cat.calls if c[0] == "modrinth"], [0, 20, 40])

    def test_change_match_forgets_and_looks_up_again(self):
        self.cat.nexus_games = [{"id": 1704, "domain": "skyrimspecialedition", "name": "Skyrim Special Edition"}]
        self.view.selectGame("pc-1")
        self.cat.nexus_games = [{"id": 110, "domain": "skyrim", "name": "Skyrim"}, {"id": 2, "domain": "x", "name": "X"}]
        self.view.changeGameMatch()
        self.assertEqual(len(self.view.candidates), 2)
        self.assertNotIn("nexus", json.loads(self.store.read_text()).get("pc-1", {}))

    def test_network_failure_is_a_status_not_a_crash(self):
        self.cat.fail = "timed out"
        self.view.selectGame("mc-1")
        self.assertFalse(self.view.busy)
        self.assertIn("timed out", self.view.status)
        self.assertEqual(self.view.items, [])

    def test_gamebanana_without_game_or_query_does_not_call_the_api(self):
        self.view.selectGame("emu-1")  # no candidates found -> falls back to an unscoped search
        self.assertIn("Type a search", self.view.status)
        self.view.search("60fps")
        self.assertEqual(self.cat.calls[-1], ("gamebanana", "60fps", None, 1))


if __name__ == "__main__":
    unittest.main()


class FakeDownloads:
    def __init__(self):
        self.calls = []

    def gamebanana_files(self, mod_id):
        self.calls.append(("gb_files", mod_id))
        return [{"file_id": "1", "name": "mod.zip", "size": 10, "url": "https://x/1", "md5": "m", "sha1": "", "date": ""}]

    def modrinth_files(self, project_id):
        return []

    def download(self, url, dest, *, filename, md5="", sha1="", progress=None):
        self.calls.append(("download", url, str(dest), filename, md5))
        if progress:
            progress(5, 10)
        return Path(dest) / filename


class FakeInstaller:
    def __init__(self, conflicts=()):
        self.conflicts = list(conflicts)
        self.calls = []

    def plan_install(self, archive, emulator, *, title_id, mod_name, home):
        self.calls.append(("plan", emulator, title_id, mod_name))
        return {"files": [{"member": "romfs/a"}], "conflicts": self.conflicts, "target_root": f"/{emulator}/x"}

    def apply_install(self, plan, *, journal_dir):
        self.calls.append(("apply",))
        return {"journal": str(Path(journal_dir) / "j.json"), "written": 1}

    def undo_install(self, journal):
        self.calls.append(("undo", journal))
        return {"removed": 1, "kept_changed": []}


class InstallFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["mod-browser-tests"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        (home / ".local/share/eden").mkdir(parents=True)
        self.cat, self.dl, self.inst = FakeCatalog(), FakeDownloads(), FakeInstaller()
        self.cat.gb_games = [{"id": 7617, "name": "The Legend of Zelda: Tears of the Kingdom"}]
        self.view = ModBrowserView(GAMES, store_path=home / "data/catalog-games.json", catalog=self.cat,
                                   synchronous=True, home=home, downloads=self.dl, installer=self.inst)
        self.view.selectGame("emu-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_installed_matching_emulators_are_offered(self):
        self.assertEqual([e["id"] for e in self.view.emulators], ["eden"])

    def test_files_download_preview_install_undo(self):
        self.view.showFiles(0)
        self.assertEqual(self.view.files[0]["name"], "mod.zip")
        self.view.download(0)
        self.assertTrue(self.view.downloadedPath.endswith("gamebanana/7/mod.zip"))
        self.assertEqual(self.dl.calls[-1][4], "m")  # checksum passed through
        self.view.previewInstall("eden", " 0100F2C0115B6000 ")
        self.assertEqual(self.inst.calls[-1], ("plan", "eden", "0100F2C0115B6000", "Mod 7"))
        self.view.applyInstall()
        self.assertTrue(self.view.canUndo)
        self.view.undoInstall()
        self.assertFalse(self.view.canUndo)
        self.assertIn("Removed 1", self.view.status)

    def test_conflicts_block_install(self):
        self.inst.conflicts = ["/eden/x/romfs/a"]
        self.view.showFiles(0)
        self.view.download(0)
        self.view.previewInstall("eden", "")
        self.assertIn("would be replaced", self.view.status)
        self.view.applyInstall()
        self.assertNotIn(("apply",), self.inst.calls)



class FakeNexus:
    def __init__(self, key="K"):
        self.key, self.calls = key, []

    def api_key(self):
        return self.key

    def parse_nxm(self, url):
        from native_services.nexus_client import parse_nxm
        return parse_nxm(url)

    def mod_files(self, domain, mod_id, key):
        self.calls.append(("files", domain, mod_id, key))
        return [{"file_id": "749043", "name": "SkyUI.zip", "description": "SkyUI 6.11 · Main", "size": 1}]

    def download_url(self, domain, mod_id, file_id, key, nxm_key="", expires=""):
        self.calls.append(("link", domain, mod_id, file_id, nxm_key, expires))
        return "https://cdn.example/SkyUI.zip"


class NexusFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["mod-browser-tests"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.inbox = self.home / "inbox"; self.inbox.mkdir()
        self.nx, self.dl = FakeNexus(), FakeDownloads()
        self.view = ModBrowserView(GAMES, store_path=self.home / "d/m.json", catalog=FakeCatalog(), synchronous=True,
                                   home=self.home, downloads=self.dl, nexus=self.nx, inbox=self.inbox)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nxm_link_in_inbox_downloads_with_the_link_key(self):
        seen = []
        self.view.nexusLinkReceived.connect(lambda: seen.append(1))
        (self.inbox / "1.nxm").write_text("nxm://skyrimspecialedition/mods/12604/files/749043?key=abc&expires=99&user_id=1")
        self.view.processInbox()
        self.assertEqual(seen, [1])
        self.assertEqual(self.nx.calls[-1], ("link", "skyrimspecialedition", 12604, 749043, "abc", "99"))
        self.assertTrue(self.view.downloadedPath.endswith("SkyUI.zip"))
        self.assertEqual(self.view.filesFor["platform"], "nexus")
        self.assertFalse(list(self.inbox.iterdir()))  # consumed

    def test_no_key_explains_what_to_do(self):
        self.nx.key = None
        self.view.handleNxm("nxm://skyrimspecialedition/mods/1/files/2?key=a&expires=1")
        self.assertIn("API key", self.view.status)

    def test_nexus_files_listed_with_key_and_game_folder_remembered(self):
        self.view._items = [{**item("nexus", 12604), "url": "https://www.nexusmods.com/skyrimspecialedition/mods/12604"}]
        self.view._game = {"game_id": "pc-1", "title": "Skyrim Special Edition"}
        self.view.showFiles(0)
        self.assertEqual(self.nx.calls[-1], ("files", "skyrimspecialedition", "12604", "K"))
        self.view.setGameFolder(str(self.home / "Skyrim"))
        self.assertEqual(self.view.gameFolder, str(self.home / "Skyrim"))


    def _show_skyui(self):
        self.view._items = [{**item("nexus", 12604), "url": "https://www.nexusmods.com/skyrimspecialedition/mods/12604"}]
        self.view._game = {"game_id": "pc-1", "title": "Skyrim Special Edition"}
        self.view._files = [{"file_id": "749043", "name": "SkyUI.zip"}]
        self.view._files_for = {"platform": "nexus", "domain": "skyrimspecialedition", "mod_id": "12604", "title": "SkyUI"}

    def test_premium_downloads_directly_without_the_browser(self):
        self._show_skyui()
        opened = []
        self.view._open_nexus_page = lambda *a: opened.append(a)
        self.view.openNexusDownload(0)
        self.assertEqual(opened, [])
        self.assertEqual(self.nx.calls[-1], ("link", "skyrimspecialedition", "12604", "749043", "", ""))
        self.assertTrue(self.view.downloadedPath.endswith("SkyUI.zip"))
        self.assertEqual(self.view.filesFor["title"], "SkyUI")

    def test_free_account_falls_back_to_the_nexus_page(self):
        from native_services.nexus_client import NexusError
        def refuse(*a, **k):
            raise NexusError("You don't have permission to get download links from the API without visting nexusmods.com")
        self.nx.download_url = refuse
        self._show_skyui()
        opened = []
        self.view._open_nexus_page = lambda *a: opened.append(a)
        self.view.openNexusDownload(0)
        self.assertEqual(opened, [("skyrimspecialedition", "12604", "749043")])
        self.assertEqual(self.view.downloadedPath, "")
        self.assertFalse(self.view.busy)

class RankingTests(unittest.TestCase):
    def test_pc_first_then_installed_favourite_playtime_recent_and_hidden_dropped(self):
        from mod_browser_view import rank_mod_games
        def g(i, platform, source, status="unknown", **meta):
            return {"id": i, "title": i, "variants": [{"platform": platform, "source": source, "status": status, "metadata": meta}]}
        games = [
            g("emu-big", "Nintendo Switch", "Emulation", "installed", playtime_seconds=999999),
            g("pc-old", "PC (Windows)", "Steam", "installed", playtime_seconds=10, last_activity="2020-01-01T00:00:00Z"),
            g("pc-new", "PC (Windows)", "Steam", "installed", playtime_seconds=10, last_activity="2026-01-01T00:00:00Z"),
            g("pc-most", "unknown", "Epic", "installed", playtime_seconds=5000),
            g("pc-fav", "PC (Windows)", "GOG", "declared-installed", favorite=True),
            g("pc-not-installed", "PC (Windows)", "Steam", playtime_seconds=99999),
            g("pc-hidden", "PC (Windows)", "Steam", "installed", hidden=True, playtime_seconds=10**7),
        ]
        order = [e["game_id"] for e in rank_mod_games(games)]
        self.assertEqual(order, ["pc-fav", "pc-most", "pc-new", "pc-old", "pc-not-installed", "emu-big"])


class NexusAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["mod-browser-tests"])

    def test_key_is_validated_before_it_is_stored(self):
        stored = []
        class NX(FakeNexus):
            def validate(self, key):
                if key != "good-key":
                    from native_services.nexus_client import NexusError
                    raise NexusError("Nexus did not accept the API key")
                return {"name": "someone", "premium": False}
            def store_api_key(self, key):
                stored.append(key)
        with tempfile.TemporaryDirectory() as tmp:
            view = ModBrowserView([], store_path=Path(tmp) / "m.json", synchronous=True, nexus=NX())
            view.setNexusKey("bad")
            self.assertEqual(stored, [])
            self.assertIn("Not saved", view.nexusAccount)
            view.setNexusKey("  good-key ")
            self.assertEqual(stored, ["good-key"])
            self.assertEqual(view.nexusAccount, "Connected as someone")


class UnknownGameLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["mod-browser-tests"])

    def test_game_without_a_recipe_is_looked_up_on_nexus(self):
        cat = FakeCatalog()
        cat.nexus_games = [{"id": 9, "domain": "someindiegame", "name": "Some Indie Game"}]
        with tempfile.TemporaryDirectory() as tmp:
            view = ModBrowserView([{"game_id": "x", "title": "Some Indie Game", "platforms": ["PC (Windows)"], "source": "Steam"}],
                                  store_path=Path(tmp) / "m.json", catalog=cat, synchronous=True)
            view.selectGame("x")
            self.assertIn(("find_nexus", "Some Indie Game"), cat.calls)
            self.assertEqual(view.matchedGame, "Some Indie Game")
            self.assertEqual(view.recipe, {})
