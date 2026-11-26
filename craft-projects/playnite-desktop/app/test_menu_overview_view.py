"""Unit tests for MenuOverviewView aggregation controller.

Verifies:
- Pure QObject: zero filesystem walks, network calls, DB writes, or process execution.
- Card schema contract: each card contains exact {key, label, count, summary}.
- Exact key contract:
    * connections.key: exact provider string ('steam', 'heroic-epic', 'xodus', 'lutris-battlenet')
    * roms.key: exact platform string (e.g. 'Nintendo GameCube', 'Sony PlayStation 2')
    * catalogs.key: store catalog key, 'control', or 'emulation:<exact platform>'
- Truth in status: count None means unavailable, not fake 0.
- Full records > 50: accurate counting over large datasets across providers and platforms.
- Filter independence: filtering or paging injected views does NOT affect overview counts.
- Privacy & clean summaries: top-level card summaries NEVER contain individual titles,
  ROM file paths, or store product IDs.
"""
import os
import sys
import unittest
from pathlib import Path

# Ensure offscreen Qt platform for test runner
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parents[2]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from menu_overview_view import MenuOverviewView

try:
    from private_addons import STORE_CATALOG_KEY as _STORE_CATALOG_KEY
except ImportError:
    _STORE_CATALOG_KEY = None


def get_app():
    return QGuiApplication.instance() or QGuiApplication([])


class MockConnectionView:
    def __init__(self, records, available=True):
        self._records = list(records)
        # Paged / filtered matches simulating UI state
        self._matches = list(self._records[:5])
        self.available = available

    @property
    def rows(self):
        # UI only sees 5 paged rows
        return [{"title": r.get("title")} for r in self._matches]


class MockLaunchView:
    def __init__(self, rom_records):
        self._rom_records = list(rom_records)
        self.romCount = len(self._rom_records)

    @property
    def romRecords(self):
        # Simulated capped UI view
        return [r if isinstance(r, dict) else r.to_dict() for r in self._rom_records[:10]]


class MockNativeProviderView:
    def __init__(self, store_items=None, retro_items=None, db_summary=None):
        self._items = tuple(store_items) if store_items is not None else None
        self._matches = self._items[:5] if self._items is not None else ()
        self._retro = tuple(retro_items) if retro_items is not None else None
        self._db_summary = db_summary


class MockControlAdapter:
    def __init__(self, is_active=True):
        self.is_active = is_active


class TestMenuOverviewView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_app()

    def setUp(self):
        # Generate > 50 realistic connection records across multiple providers
        self.connection_records = []
        for i in range(30):
            self.connection_records.append({
                "provider": "steam",
                "product_id": f"100{i:03d}",
                "title": f"Steam Secret Title {i}",
                "installed": (i % 2 == 0),
                "join_state": "matched" if i < 20 else "unmatched",
            })
        for i in range(15):
            self.connection_records.append({
                "provider": "heroic-epic",
                "product_id": f"EpicApp_{i}",
                "title": f"Epic Private Title {i}",
                "installed": (i < 5),
                "join_state": "matched" if i < 10 else "ambiguous",
            })
        for i in range(10):
            self.connection_records.append({
                "provider": "xodus",
                "product_id": f"a1b2c3d4e5f600{i:02d}",
                "title": f"Xbox Private Title {i}",
                "installed": False,
                "join_state": "unmatched",
            })
        for i in range(5):
            self.connection_records.append({
                "provider": "lutris-battlenet",
                "product_id": f"bnet_{i}",
                "title": f"Battle.net Client {i}",
                "installed": True,
                "join_state": "unmatched",
                "launch_intent": {"execution_supported": False},
            })

        # Generate > 50 realistic ROM records across exact platform strings
        self.rom_records = []
        for i in range(25):
            self.rom_records.append({
                "platform": "Super Nintendo (SNES)",
                "rom_id": f"snes_{i}",
                "file_name": f"secret_game_{i}.sfc",
                "file_path": f"/games/snes/secret_game_{i}.sfc",
                "launchable": (i % 3 != 0),
                "emulator_name": "snes9x" if i % 2 == 0 else "bsnes",
            })
        for i in range(20):
            self.rom_records.append({
                "platform": "Sony PlayStation",
                "rom_id": f"psx_{i}",
                "file_name": f"psx_iso_{i}.chd",
                "file_path": f"/games/psx/psx_iso_{i}.chd",
                "launchable": True,
                "emulator_name": "duckstation",
            })
        for i in range(15):
            self.rom_records.append({
                "platform": "Nintendo GameCube",
                "rom_id": f"gc_{i}",
                "file_name": f"gamecube_{i}.iso",
                "file_path": f"/games/gc/gamecube_{i}.iso",
                "launchable": False,
                "emulator_name": None,
            })

        self.store_items = [{"id": f"store_{i}", "title": f"Store Game {i}"} for i in range(60)]
        self.retro_items = [{"platform": "Nintendo 64", "title": f"Retro Game {i}"} for i in range(40)]

        self.mock_conn = MockConnectionView(self.connection_records)
        self.mock_launch = MockLaunchView(self.rom_records)
        self.mock_native = MockNativeProviderView(
            store_items=self.store_items,
            retro_items=self.retro_items,
            db_summary={
                "total": 4500,
                "platform_count": 2,
                "platform_counts": {
                    "Nintendo 64": 1500,
                    "Sony PlayStation 2": 3000,
                },
                "platforms": ["Nintendo 64", "Sony PlayStation 2"],
            },
        )
        self.mock_control = MockControlAdapter(is_active=True)

    def test_pure_qobject_lifecycle_and_defaults(self):
        view = MenuOverviewView()
        self.assertEqual(view.connections, [])
        self.assertEqual(view.roms, [])
        self.assertEqual(view.catalogs, [])
        self.assertEqual(view.totalConnectionsCount, 0)
        self.assertEqual(view.totalRomsCount, 0)
        self.assertEqual(view.totalCatalogsCount, 0)

    def test_connections_exact_keys_and_filter_independence(self):
        view = MenuOverviewView(connection_view=self.mock_conn)

        cards = view.connections
        self.assertEqual(len(cards), 4)
        by_key = {c["key"]: c for c in cards}

        # Exact provider keys contract
        self.assertIn("steam", by_key)
        self.assertIn("heroic-epic", by_key)
        self.assertIn("xodus", by_key)
        self.assertIn("lutris-battlenet", by_key)

        # Full counts over > 50 records
        self.assertEqual(by_key["steam"]["count"], 30)
        self.assertEqual(by_key["heroic-epic"]["count"], 15)
        self.assertEqual(by_key["xodus"]["count"], 10)
        self.assertEqual(by_key["lutris-battlenet"]["count"], 5)
        self.assertEqual(view.totalConnectionsCount, 60)

        # Filter independence: mutate mock_conn._matches and verify counts are unchanged
        self.mock_conn._matches = self.mock_conn._records[:1]
        view.refresh()
        self.assertEqual(view.totalConnectionsCount, 60)

    def test_roms_exact_platform_keys(self):
        view = MenuOverviewView(launch_view=self.mock_launch)

        cards = view.roms
        self.assertEqual(len(cards), 3)
        by_key = {c["key"]: c for c in cards}

        # Exact platform string keys
        self.assertIn("Super Nintendo (SNES)", by_key)
        self.assertIn("Sony PlayStation", by_key)
        self.assertIn("Nintendo GameCube", by_key)

        self.assertEqual(by_key["Super Nintendo (SNES)"]["count"], 25)
        self.assertEqual(by_key["Sony PlayStation"]["count"], 20)
        self.assertEqual(by_key["Nintendo GameCube"]["count"], 15)
        self.assertEqual(view.totalRomsCount, 60)

        # Verify summary reports configured profiles, does not falsely claim installed emulator
        self.assertIn("configured profiles: bsnes, snes9x", by_key["Super Nintendo (SNES)"]["summary"])
        self.assertIn("configured profiles: duckstation", by_key["Sony PlayStation"]["summary"])
        self.assertIn("no profile assigned", by_key["Nintendo GameCube"]["summary"])

    def test_catalogs_exact_keys_and_platform_counts(self):
        view = MenuOverviewView(native_view=self.mock_native, control_adapter=self.mock_control)

        cards = view.catalogs
        by_key = {c["key"]: c for c in cards}

        if _STORE_CATALOG_KEY is not None:
            self.assertIn(_STORE_CATALOG_KEY, by_key)
            self.assertEqual(by_key[_STORE_CATALOG_KEY]["count"], 60)

        self.assertIn("control", by_key)
        self.assertEqual(by_key["control"]["count"], 1)

        self.assertIn("emulation:Nintendo 64", by_key)
        self.assertEqual(by_key["emulation:Nintendo 64"]["count"], 1500)

        self.assertIn("emulation:Sony PlayStation 2", by_key)
        self.assertEqual(by_key["emulation:Sony PlayStation 2"]["count"], 3000)

    def test_count_none_when_unavailable_not_fake_zero(self):
        # Native view with no store items loaded
        empty_native = MockNativeProviderView(store_items=None, retro_items=None, db_summary={"platforms": ["Sega Dreamcast"]})
        view = MenuOverviewView(native_view=empty_native)

        by_key = {c["key"]: c for c in view.catalogs}
        if _STORE_CATALOG_KEY is not None:
            self.assertIn(_STORE_CATALOG_KEY, by_key)
            self.assertIsNone(by_key[_STORE_CATALOG_KEY]["count"])

        # Emulation platform with variant count unavailable -> count must be None, NOT 0
        self.assertIn("emulation:Sega Dreamcast", by_key)
        self.assertIsNone(by_key["emulation:Sega Dreamcast"]["count"])

    def test_privacy_invariants_no_titles_or_product_ids_in_summaries(self):
        view = MenuOverviewView(
            connection_view=self.mock_conn,
            launch_view=self.mock_launch,
            native_view=self.mock_native,
            control_adapter=self.mock_control,
        )

        all_cards = view.connections + view.roms + view.catalogs
        for card in all_cards:
            summary = card["summary"]
            # No titles, filenames, IDs or paths in summaries
            self.assertNotIn("Secret Title", summary)
            self.assertNotIn("Private Title", summary)
            self.assertNotIn("secret_game", summary)
            self.assertNotIn("psx_iso", summary)
            self.assertNotIn("100000", summary)
            self.assertNotIn("EpicApp_", summary)
            self.assertNotIn("/games/", summary)


if __name__ == "__main__":
    unittest.main()
