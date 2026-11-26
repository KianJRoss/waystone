"""Catalog-link presentation boundary tests; all state is temporary."""
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QGuiApplication
from catalog_link_view import CatalogLinkView


def game(ident, title, source, platform='PC', edition='Standard', **extra):
    return {'id': ident, 'title': title, 'variants': [
        {'source': source, 'platform': platform, 'edition': edition, **extra}]}


class CatalogLinkViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QGuiApplication.instance() or QGuiApplication(['catalog-link-tests'])

    def test_default_never_creates_state_or_mutates_catalog(self):
        data = {'games': [game('id-a', 'Same', 'Unassigned'), game('id-b', 'Same', 'Steam')]}
        before = json.dumps(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'not-created.sqlite'
            view = CatalogLinkView(data, link_state=path, enable_link_state=False)
            self.assertFalse(path.exists())
            self.assertFalse(view.storeEnabled)
            self.assertEqual(view.preview([view.rows[0]['candidateId']]), '')
            self.assertIn('Preview unavailable', view.error)
            self.assertEqual(json.dumps(data), before)

    def test_explicit_temp_store_preview_apply_and_undo_are_separate(self):
        data = {'games': [game('canonical-a', 'Same', 'Unassigned'),
                          game('canonical-b', 'Same', 'Steam')]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'overlay.sqlite'
            view = CatalogLinkView(data)
            self.assertTrue(view.createLinkState(str(path), True), view.error)
            candidate_id = view.rows[0]['candidateId']
            preview = view.preview([candidate_id])
            self.assertIn('"preview": true', preview)
            self.assertEqual(json.loads(preview)['links_to_add'], 1)
            self.assertEqual(view._store.snapshot()['resolution'], {})
            self.assertEqual(view.resolution, {})
            applied = view.apply('apply-001')
            result = json.loads(applied)
            self.assertTrue(result['applied'])
            self.assertEqual(view._store.resolution(), {'canonical-b': 'canonical-a'})
            self.assertEqual(view.resolution, {'canonical-b': 'canonical-a'})
            self.assertTrue(view.rows[0]['linked'])
            self.assertTrue(view.rows[0]['blocked'])
            self.assertEqual(view.preview([candidate_id]), '')
            self.assertEqual(view._store.resolution(), {'canonical-b': 'canonical-a'})
            undo = view.undo(result['audit_id'], 'undo-001')
            self.assertTrue(json.loads(undo)['undone'])
            self.assertEqual(view._store.resolution(), {})
            self.assertEqual(view.resolution, {})
            self.assertFalse(view.rows[0]['linked'])

    def test_no_apply_without_preview_and_explicit_creation_gate(self):
        data = {'games': [game('a', 'Same', 'Unassigned'), game('b', 'Same', 'Steam')]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'overlay.sqlite'
            view = CatalogLinkView(data)
            self.assertFalse(view.createLinkState(str(path), False))
            self.assertFalse(path.exists())
            self.assertEqual(view.apply('no-preview'), '')
            self.assertIn('preview again', view.error)

    def test_create_requires_existing_parent_and_stale_catalog_state_fails_closed(self):
        old_catalog = {'games': [game('old-a', 'Same', 'Unassigned'),
                                 game('old-b', 'Same', 'Steam')]}
        new_catalog = {'games': [game('new-a', 'Same', 'Unassigned'),
                                 game('new-b', 'Same', 'Steam')]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / 'missing' / 'links.sqlite'
            creator = CatalogLinkView(old_catalog)
            self.assertFalse(creator.createLinkState(str(missing), True))
            self.assertFalse(missing.parent.exists())
            state = root / 'links.sqlite'
            self.assertTrue(creator.createLinkState(str(state), True), creator.error)
            stale = CatalogLinkView(new_catalog, link_state=state, enable_link_state=True)
            self.assertFalse(stale.storeEnabled)
            self.assertEqual(stale.resolution, {})
            self.assertIn('does not match', stale.status)
            self.assertIn('does not match', stale.error)
            self.assertEqual(stale.preview([stale.rows[0]['candidateId']]), '')
            self.assertEqual(stale.resolution, {})

    def test_links_survive_a_new_game_joining_the_library(self):
        catalog = {'games': [game('a', 'Same', 'Unassigned'), game('b', 'Same', 'Steam')]}
        grown = {'games': catalog['games'] + [game('c', 'Other', 'Steam')]}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'links.sqlite'
            creator = CatalogLinkView(catalog)
            self.assertTrue(creator.createLinkState(str(state), True), creator.error)
            reopened = CatalogLinkView(grown, link_state=state, enable_link_state=True)
            self.assertTrue(reopened.storeEnabled, reopened.error)
            self.assertNotIn('does not match', reopened.status)
            from native_services.catalog_link_store import CatalogLinkStore
            self.assertIn('c', CatalogLinkStore(state).canonical_ids())

    def test_conflicting_exact_identity_is_blocked_and_text_is_redacted(self):
        data = {'games': [game('a', '/private/import/file.sh', 'Unassigned', 'PC', 'Standard', external_id='x'),
                          game('b', '/private/import/file.sh', 'Steam', 'Nintendo Wii', 'Deluxe', external_id='x')]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'overlay.sqlite'
            view = CatalogLinkView(data)
            self.assertTrue(view.createLinkState(str(path), True), view.error)
            self.assertTrue(view.rows[0]['blocked'])
            self.assertEqual(view.rows[0]['records'][0]['title'], 'Imported text withheld')
            rendered = json.dumps(view.rows)
            self.assertNotIn('/private/import', rendered)
            self.assertNotIn("'x'", rendered)
            self.assertEqual(view.preview([view.rows[0]['candidateId']]), '')
            self.assertIn('Preview unavailable', view.error)
            self.assertEqual(view._store.snapshot()['resolution'], {})

    def test_more_than_500_rows_are_available_with_identifiable_sources(self):
        games = []
        for n in range(510):
            games.append(game(f'unassigned-{n}', f'Unique review game {n}', 'Unassigned'))
            games.append(game(f'steam-{n}', f'Unique review game {n}', 'Steam'))
        view = CatalogLinkView({'games': games})
        self.assertEqual(len(view.rows), 510)
        self.assertIn('Unassigned', view.rows[0]['records'][0]['sources'])
        self.assertIn('Steam', view.rows[0]['records'][1]['sources'])
        self.assertTrue(all(row['records'][0]['sourceCount'] == 1 for row in view.rows))

    def test_review_rows_expose_platform_and_edition_uncertainty(self):
        data = {'games': [game('a', 'Same', 'Unassigned', 'PC', ''),
                          game('b', 'Same', 'Steam', 'PC', '')]}
        row = CatalogLinkView(data).rows[0]
        self.assertEqual([record['platforms'] for record in row['records']], [['Pc'], ['Pc']])
        self.assertEqual([record['editions'] for record in row['records']], [[], []])
        self.assertIn('Edition: " + (modelData.editions.length ? modelData.editions.join(" / ") : "Unknown (not supplied)")',
                      Path(__file__).with_name('Main.qml').read_text())

    def test_latest_apply_remains_undoable_after_view_recreation(self):
        data = {'games': [game('a', 'Same', 'Unassigned'), game('b', 'Same', 'Steam')]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'overlay.sqlite'
            first = CatalogLinkView(data)
            self.assertTrue(first.createLinkState(str(path), True), first.error)
            candidate_id = first.rows[0]['candidateId']
            first.preview([candidate_id])
            result = json.loads(first.apply('persistent-link'))
            second = CatalogLinkView(data, link_state=path, enable_link_state=True)
            self.assertEqual(second.undoableAuditId, result['audit_id'])
            self.assertIn('can undo', second.latestAction)
            undo = json.loads(second.undo(second.undoableAuditId, 'persistent-undo'))
            self.assertTrue(undo['undone'])
            self.assertEqual(second.undoableAuditId, 0)
            self.assertIn('undo', second.latestAction)


if __name__ == '__main__':
    unittest.main()
