import time
import unittest

from PySide6.QtCore import QCoreApplication

from steam_library_settings import SteamLibrarySettings

STEAM_ID = '76561198000000001'
KEY = '0123456789abcdef0123456789abcdef'


class Store:
    def __init__(self): self.value = None; self.saved = []; self.cleared = []
    def load(self, provider):
        assert provider == 'steam'
        return self.value
    def save(self, provider, record):
        assert provider == 'steam'
        self.value = dict(record); self.saved.append(dict(record))
    def clear(self, provider):
        self.cleared.append(provider); self.value = None


class Adapter:
    def __init__(self, account, key):
        assert account == STEAM_ID and key == KEY
    def fetch_owned_games(self):
        return {'complete': True, 'games': [{'product_id': '1'}]}


class BadAdapter(Adapter):
    def fetch_owned_games(self): raise RuntimeError(KEY)


class SteamLibrarySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QCoreApplication.instance() or QCoreApplication([])

    def wait(self, view):
        deadline = time.monotonic() + 3
        while (view.busy or view._worker is not None) and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.001)
        self.assertFalse(view.busy)
        self.app.processEvents()

    def test_vanity_is_resolved_verified_before_save_and_never_exposed(self):
        store = Store(); resolved = []
        def resolver(name, key):
            assert (name, key) == ('kian-name', KEY); resolved.append(name); return STEAM_ID
        view = SteamLibrarySettings(store, adapter_factory=Adapter, resolver=resolver)
        self.assertTrue(view.saveAccount('https://steamcommunity.com/id/kian-name', KEY))
        self.wait(view)
        self.assertEqual(resolved, ['kian-name'])
        self.assertEqual(store.saved, [{'kind': 'steam_web', 'account_id': STEAM_ID, 'api_key': KEY}])
        self.assertTrue(view.configured); self.assertEqual(view.steamId, STEAM_ID)
        self.assertIn('1 games visible', view.message)
        self.assertNotIn(KEY, view.message)
        self.assertTrue(view.forget()); self.assertEqual(store.cleared, ['steam'])
        self.assertFalse(view.configured)

    def test_failed_private_or_bad_key_request_does_not_store_secret(self):
        store = Store()
        view = SteamLibrarySettings(store, adapter_factory=BadAdapter, resolver=lambda *_: STEAM_ID)
        self.assertTrue(view.saveAccount(STEAM_ID, KEY))
        self.wait(view)
        self.assertEqual(store.saved, [])
        self.assertNotIn(KEY, view.message)
        self.assertFalse(view.saveAccount(STEAM_ID, 'bad-key'))
        self.assertEqual(store.saved, [])

    def test_keyring_status_does_not_display_secret(self):
        store = Store(); store.value = {'kind': 'steam_web', 'account_id': STEAM_ID, 'api_key': KEY}
        view = SteamLibrarySettings(store)
        self.assertTrue(view.checkSaved())
        self.assertEqual(view.steamId, STEAM_ID)
        self.assertNotIn(KEY, view.message)
