"""Offline UI contract for browsing imported genre/category/tag collections."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlExpression
from app import create_session

HERE = Path(__file__).parent


class MetadataFacetUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QGuiApplication.instance() or QGuiApplication(['facet-ui-offline-test'])

    def test_searchable_collection_facets_are_live_and_exact(self):
        data = json.loads((HERE / 'fixture-library.json').read_text())
        data['catalog_kind'] = 'playnite-display-selected-copy-v1'
        data['games'][0]['variants'][0]['metadata'] = {
            'genres': ['Adventure', 'Co-op'], 'categories': ['Backlog'], 'tags': ['Controller Friendly'], 'favorite': True}
        data['games'][1]['variants'][0]['metadata'] = {
            'genres': ['Adventure'], 'categories': ['Completed'], 'tags': ['Solo'], 'favorite': False}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(data))
            engine, model, root, warnings = create_session(self.application, headless=True, catalog=path)
            try:
                explorer = root.findChild(QObject, 'explorerField')
                search = root.findChild(QObject, 'facetSearch')
                items = root.findChild(QObject, 'explorerItems')
                self.assertIsNotNone(explorer)
                self.assertIsNotNone(search)
                self.assertIsNotNone(items)
                explorer.setProperty('currentIndex', 3)  # Genres
                self.application.processEvents()
                self.assertTrue(search.property('visible'))
                root.setProperty('facetQuery', 'co')
                self.application.processEvents()
                expression = QQmlExpression(engine.rootContext(), root, 'filteredExplorerValues()')
                values = expression.evaluate()
                self.assertFalse(expression.hasError(), expression.error().toString())
                values = values[0].toVariant()
                self.assertEqual(values[0], 'All genres')
                self.assertEqual(values[1:], ['Co-op'])
                root.setProperty('facetQuery', '')
                QQmlExpression(engine.rootContext(), root, "toggleCollection('genres', 'Adventure')").evaluate()
                self.assertEqual({g['id'] for g in model.visible}, {'demo-adventure', 'demo-adventure-remaster'})
                self.assertFalse(warnings, warnings)
            finally:
                root.setVisible(False)
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()

    def test_multiple_metadata_facets_combine_and_can_be_removed(self):
        data = json.loads((HERE / 'fixture-library.json').read_text())
        data['catalog_kind'] = 'playnite-display-selected-copy-v1'
        data['games'][0]['variants'][0]['metadata'] = {
            'genres': ['Adventure'], 'tags': ['Controller Friendly'], 'categories': ['Backlog']}
        data['games'][0]['variants'][1]['metadata'] = {
            'genres': ['Adventure'], 'tags': ['Solo'], 'categories': ['Completed']}
        data['games'][1]['variants'][0]['metadata'] = {
            'genres': ['Adventure'], 'tags': ['Controller Friendly'], 'categories': ['Completed']}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(data))
            engine, model, root, warnings = create_session(self.application, headless=True, catalog=path)
            try:
                def toggle(field, value):
                    expression = QQmlExpression(engine.rootContext(), root, f"toggleCollection('{field}', '{value}')")
                    expression.evaluate()
                    self.assertFalse(expression.hasError(), expression.error().toString())
                toggle('genres', 'Adventure')
                toggle('tags', 'Controller Friendly')
                toggle('categories', 'Backlog')
                self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
                toggle('tags', 'Solo')  # OR within Tags
                self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
                toggle('categories', 'Backlog')  # remove category constraint
                self.assertEqual({game['id'] for game in model.visible}, {'demo-adventure', 'demo-adventure-remaster'})
                QQmlExpression(engine.rootContext(), root, "clearFilters()").evaluate()
                self.assertEqual(model.rowCount(), 8)
                self.assertFalse(warnings, warnings)
            finally:
                root.setVisible(False)
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()

    def test_reviewed_link_overlay_groups_grid_and_keeps_source_records(self):
        data = {
            'schema_version': 1, 'catalog_kind': 'playnite-display-selected-copy-v1',
            'games': [
                {'id': 'playnite:unassigned-1', 'title': 'Linked Demo', 'variants': [
                    {'source': 'Unassigned', 'source_id': 'playnite-record-1', 'installation_id': 'install-1',
                     'platform': 'PC', 'status': 'unknown', 'edition': 'Standard'}]},
                {'id': 'steam:123', 'title': 'Linked Demo', 'variants': [
                    {'source': 'Steam', 'source_id': '123', 'installation_id': 'install-2',
                     'platform': 'PC', 'status': 'unknown', 'edition': 'Standard'}]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'catalog.json'
            state = Path(directory) / 'links.sqlite3'
            catalog.write_text(json.dumps(data))
            engine, model, root, warnings = create_session(self.application, headless=True,
                                                           catalog=catalog, link_state=state)
            try:
                manager = engine.catalog_link_view
                self.assertEqual(model.totalCount, 2)
                self.assertTrue(manager.createLinkState(str(state), True), manager.error)
                candidate = manager.rows[0]['candidateId']
                self.assertIn('"preview": true', manager.preview([candidate]))
                applied = json.loads(manager.apply('test-ui-apply'))
                self.assertTrue(applied['applied'])
                self.assertEqual(model.totalCount, 1)
                self.application.processEvents()
                self.assertEqual(root.property('selectedGame')['variantCount'], 2)
                self.assertEqual(model.sourceRecordCount, 2)
                self.assertEqual(len(model.games[0]['variants']), 2)
                self.assertIn('steam:123', model.details('playnite:unassigned-1'))
                self.assertEqual(model.rowOf('steam:123'), model.rowOf('playnite:unassigned-1'))
                self.assertFalse(warnings, warnings)
                manager.undo(applied['audit_id'], 'test-ui-undo')
                self.application.processEvents()
                self.assertEqual(root.property('selectedGame')['variantCount'], 1)
            finally:
                root.setVisible(False)
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()

    def test_top_level_duplicate_action_is_explicit_and_opens_review(self):
        data = {
            'schema_version': 1, 'catalog_kind': 'playnite-display-selected-copy-v1',
            'games': [
                {'id': 'playnite:unassigned-1', 'title': 'Link Action Demo', 'variants': [
                    {'source': 'Unassigned', 'source_id': 'playnite-record-1', 'installation_id': 'install-1',
                     'platform': 'PC', 'status': 'unknown'}]},
                {'id': 'steam:123', 'title': 'Link Action Demo', 'variants': [
                    {'source': 'Steam', 'source_id': '123', 'installation_id': 'install-2',
                     'platform': 'PC', 'status': 'unknown'}]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'catalog.json'
            state = Path(directory) / 'links.sqlite3'
            catalog.write_text(json.dumps(data))
            engine, model, root, warnings = create_session(self.application, headless=True,
                                                           catalog=catalog, link_state=state)
            try:
                button = root.findChild(QObject, 'linkAccountsButton')
                dialog = root.findChild(QObject, 'accountLinkReviewDialog')
                self.assertIsNotNone(button)
                self.assertIsNotNone(dialog)
                self.assertFalse(state.exists())  # opening a session is read-only
                expression = QQmlExpression(engine.rootContext(), root, 'openAccountLinkReview()')
                expression.evaluate()
                self.assertFalse(expression.hasError(), expression.error().toString())
                self.assertTrue(state.exists())  # only the explicit user action creates overlay
                self.assertTrue(engine.catalog_link_view.storeEnabled)
                self.assertTrue(dialog.property('visible'))
                self.assertFalse(warnings, warnings)
            finally:
                root.setVisible(False)
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()


if __name__ == '__main__':
    unittest.main()
