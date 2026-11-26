"""Offline behavior, safety and evidence regression tests for the desktop rebuild."""
import ast
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from library_model import LibraryModel, load_fixture, state_label
from snapshot_bridge import load_export, load_exports, snapshot_games, key, MAX_SNAPSHOT_BYTES
from organizer_view import organizer_preview
from modules.organizer_health import health_report

HERE = Path(__file__).parent


class DesktopModelTests(unittest.TestCase):
    def setUp(self):
        self.games = load_fixture(HERE / 'fixture-library.json')
        self.model = LibraryModel(self.games)

    def test_each_sort_key_both_directions(self):
        keys = {
            'title': lambda g: g['title'].casefold(),
            'sources': lambda g: ', '.join(sorted({v['source'] for v in g['variants']})).casefold(),
            'platforms': lambda g: ', '.join(sorted({v['platform'] for v in g['variants']})).casefold(),
            'installState': lambda g: ', '.join(sorted({v['status'] for v in g['variants']})),
            'variantCount': lambda g: len(g['variants']),
        }
        before = copy.deepcopy(self.games)
        for field, value in keys.items():
            for descending in (True, False):
                with self.subTest(field=field, descending=descending):
                    self.model.setSort(field, descending)
                    actual = [value(g) for g in self.model.visible]
                    self.assertEqual(actual, sorted(actual, reverse=descending))
                    self.assertEqual(len({g['id'] for g in self.model.visible}), 8)
        self.assertEqual(self.games, before)

    def test_sort_is_retained_after_filter_reset(self):
        self.model.setSort('title', True)
        self.model.setFilters('Adventure', 'All sources', 'All platforms', 'All states')
        actual = [g['title'].casefold() for g in self.model.visible]
        self.assertEqual(actual, sorted(actual, reverse=True))
        self.assertEqual(self.model.visibleCount, 3)

    def test_identity_tiebreaker_not_title_merge(self):
        self.model.setSort('title', False)
        same = [g['id'] for g in self.model.visible if g['title'] == 'Demo Adventure']
        self.assertEqual(same, sorted(same))
        self.assertEqual(len(same), 2)

    def test_sort_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            self.model.setSort('launch', False)

    def test_status_values_never_collapsed(self):
        declared = copy.deepcopy(self.games[1])
        declared['id'] = 'manifest-only'
        declared['variants'][0]['status'] = 'declared-installed'
        model = LibraryModel(self.games + [declared])
        model.setFilters('', 'All sources', 'All platforms', 'installed')
        self.assertEqual(model.rowCount(), 3)
        self.assertNotIn('manifest-only', [g['id'] for g in model.visible])
        model.setFilters('', 'All sources', 'All platforms', 'declared-installed')
        self.assertEqual(model.idAt(0), 'manifest-only')
        self.assertEqual('Needs local check', model.overview('manifest-only')['state'])
        self.assertNotEqual(state_label('registered'), state_label('not-installed'))

    def test_overview_honest_metadata_and_full_provenance(self):
        view = self.model.overview('demo-adventure')
        self.assertEqual(view['state'], 'Mixed observations')
        self.assertEqual(view['variantCount'], 2)
        self.assertIn('Heroic', view['sources'])
        self.assertIn('Synthetic fixture', view['provenance'])
        self.assertIn('Unknown', view['observed'])
        self.assertEqual(self.model.overview('absent'), {})

    def test_facets_explicit_full_catalog(self):
        self.model.setFilters('absent', 'All sources', 'All platforms', 'All states')
        self.assertEqual(self.model.facetCount('source', 'Steam'), 1)
        self.assertEqual(self.model.facetCount('status', 'installed'), 3)
        self.assertEqual(self.model.facetCount('bogus', 'Steam'), 0)

    def test_row_identity_mapping(self):
        self.model.setSort('title', True)
        for i in range(self.model.rowCount()):
            self.assertEqual(self.model.rowOf(self.model.idAt(i)), i)
        self.assertEqual(self.model.idAt(-1), '')
        self.assertEqual(self.model.idAt(100), '')
        self.assertEqual(self.model.rowOf('nope'), -1)

    def test_organizer_local_and_immutable(self):
        original = copy.deepcopy(self.games)
        report = health_report(self.games)
        self.assertEqual(report['entry_count'], 8)
        self.assertEqual(len(report['duplicate_candidates']), 1)
        text = organizer_preview(self.games)
        self.assertIn('HEALTH REPORT', text)
        self.assertIn('demo-adventure-remaster', text)
        self.assertEqual(self.games, original)

    def test_organizer_failure_remains_honest(self):
        def broken(games):
            raise ValueError('broken')
        self.assertIn('UNAVAILABLE', organizer_preview(self.games, broken))

    def test_all_frontend_text_is_plain_and_no_launch_surface(self):
        qml = '\n'.join(path.read_text() for path in HERE.glob('*.qml'))
        for token in ('onPlayClicked', 'onInstallClicked', 'Process {'):
            self.assertNotIn(token, qml)
        # Artwork is allowed only through the backend's validated opaque roles.
        # Exact binding admission prevents a future file/network/title-derived sink.
        approved_sources = {
            # The rail/top-bar logo is a fixed asset shipped with the app (assets/brand), never data-derived.
            # Ribbon logos (W18): the backend's validated logoUrl role via heroLogoUrl/detailLogoUrl.
            'ConsoleLibrary.qml': ['Qt.resolvedUrl("../../../assets/brand/waystone-mark.svg")',
                                   'consoleRoot.heroLogoUrl', 'consoleRoot.detailLogoUrl'],
            'Main.qml': ['Qt.resolvedUrl("../../../assets/brand/waystone-mark.svg")', 'compactList.visible && !root.organizerMode && detailsRow.y + detailsRow.height > compactList.contentY && detailsRow.y < compactList.contentY + compactList.height ? detailsRow.iconUrl : ""', '"VoiceMediaPane.qml"'],
            'DetailsPane.qml': ['pane.visible && !pane.compactMode ? (pane.game.backgroundUrl || "") : ""', 'pane.visible ? (pane.game.iconUrl || "") : ""'],
            # Assistant glide card: the passed game's cover from the backend's validated coverUrl role.
            'AssistantDirector.qml': ['passingCard.visible ? (director.passingGame.cover || "") : ""'],
            'MissingCover.qml': ['cover.loadEnabled ? cover.safeArtworkSource : ""'],
            'StoreAccountsPanel.qml': ['active ? "XodusSignInPane.qml" : ""'],
            # Mod thumbnails: native_services.mod_catalog._https_image only lets https URLs through.
            'ModManagementPage.qml': ['modelData.thumbnail_url || ""'],
            # Screenshot thumbnail: overlay tools bridge validated local file URL only.
            'OverlayToolScreenshot.qml': ['root.toolsBridge ? root.toolsBridge.lastScreenshotUrl : ""'],
            # Quick library cover artwork: overlay library bridge validated cover URLs only.
            'OverlayLibraryPanel.qml': [
                '(libraryBridge && libraryBridge.runningGame) ? (libraryBridge.runningGame.coverUrl || "") : ""',
                'modelData.coverUrl || ""',
                'modelData.coverUrl || ""',
                'modelData.coverUrl || ""',
                '(libraryBridge && libraryBridge.selectedGame) ? (libraryBridge.selectedGame.coverUrl || "") : ""',
            ],
        }
        approved_covers = {
            'ConsoleLibrary.qml': [
                'consoleRoot.heroCoverUrl',
                'modelData.coverUrl || ""',
                'coverUrl',
                'consoleRoot.selectedRomCoverUrl',
                'consoleRoot.detailCoverUrl',
            ],
            'Main.qml': ['coverDelegate.coverUrl'],
            'DetailsPane.qml': ['pane.game.coverUrl || ""'],
            # Store covers: price_comparison_view._https_image only lets https URLs through.
            'StorefrontDashboard.qml': ['modelData.coverUrl || ""'],
        }
        for path in HERE.glob('*.qml'):
            text = path.read_text()
            self.assertEqual(re.findall(r'^\s*source:\s*(.+)$', text, re.M), approved_sources.get(path.name, []), path.name)
            self.assertEqual(re.findall(r'^\s*artworkSource:\s*(.+)$', text, re.M), approved_covers.get(path.name, []), path.name)
        self.assertIn('TextEdit.PlainText', qml)
        self.assertIn('Text.PlainText', qml)
        for name in ('app.py', 'library_model.py', 'snapshot_bridge.py', 'organizer_view.py', 'modules/organizer_health.py'):
            tree = ast.parse((HERE / name).read_text())
            modules = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            for module in modules:
                self.assertFalse(any(t in module for t in ('subprocess', 'requests', 'socket', 'urllib', 'webbrowser', 'QtNetwork')))
        self.assertNotIn('sys.path', (HERE / 'organizer_view.py').read_text())


class SnapshotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((HERE / 'fixtures/demo-snapshot.json').read_text())

    def test_snapshot_declarations_are_never_validated_installations(self):
        games = snapshot_games(self.snapshot)
        model = LibraryModel(games)
        model.setFilters('', 'All sources', 'All platforms', 'installed')
        self.assertEqual(model.rowCount(), 0)
        model.setFilters('', 'All sources', 'All platforms', 'declared-installed')
        self.assertGreater(model.rowCount(), 0)

    def test_offline_incomplete_context_and_provenance_retained(self):
        self.snapshot.update(availability='offline', complete=False)
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            path = Path(directory) / 'export.json'
            path.write_text(json.dumps(self.snapshot))
            games, contexts = load_exports([path], with_context=True)
        self.assertFalse(contexts[0]['complete'])
        self.assertEqual(contexts[0]['availability'], 'offline')
        v = games[0]['variants'][0]
        self.assertEqual(v['observation']['availability'], 'offline')
        self.assertFalse(v['observation']['complete'])
        details = LibraryModel(games).details(games[0]['id'])
        for token in ('Provider installation ID:', 'External ID:', 'verification_scope', 'sha256', 'field_locators', 'offline'):
            self.assertIn(token, details)

    def test_invalid_stable_ids_and_provenance_are_rejected(self):
        for field, invalid in [('variant_key', 'fake'), ('record_key', 'fake'), ('installation_state', 'installed'), ('installation_id', '')]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.snapshot)
                data['records'][0][field] = invalid
                with self.assertRaises(ValueError):
                    snapshot_games(data)
        self.snapshot['records'][0]['evidence']['sha256'] = 'fake'
        with self.assertRaises(ValueError):
            snapshot_games(self.snapshot)

    def test_export_size_cap(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            path = Path(directory) / 'huge.json'
            with path.open('wb') as stream:
                stream.truncate(MAX_SNAPSHOT_BYTES + 1)
            with self.assertRaisesRegex(ValueError, 'size cap'):
                load_export(path)

    def test_zero_record_snapshot_is_valid_observation(self):
        self.snapshot['records'] = []
        self.assertEqual(snapshot_games(self.snapshot), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
