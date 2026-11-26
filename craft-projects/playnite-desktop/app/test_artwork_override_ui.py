"""Focused UI integration tests for manual artwork overrides.

Verifies offscreen QML preview, separate apply/undo, opaque image provider,
lazy store creation on Apply, exact-record targeting, and original fallback retention.
"""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from app import create_session
from library_model import load_fixture

HERE = Path(__file__).parent


class ArtworkOverrideUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QGuiApplication.instance() or QGuiApplication(['offline-artwork-override-tests'])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='test-art-ui-')
        self.root_path = Path(self.temp_dir.name)
        self.override_store_path = self.root_path / 'user_artwork_store'
        self.fixture_file = HERE / 'fixture-library.json'
        self.games = load_fixture(self.fixture_file)
        self.original_games = copy.deepcopy(self.games)

        # Create valid test images (cover, background, icon)
        self.sample_cover = self.root_path / 'sample_cover.png'
        cover_img = QImage(64, 96, QImage.Format_RGB32)
        cover_img.fill(QColor('purple'))
        self.assertTrue(cover_img.save(str(self.sample_cover), 'PNG'))

        self.sample_bg = self.root_path / 'sample_bg.jpg'
        bg_img = QImage(160, 90, QImage.Format_RGB32)
        bg_img.fill(QColor('darkblue'))
        self.assertTrue(bg_img.save(str(self.sample_bg), 'JPEG'))

        self.sample_icon = self.root_path / 'sample_icon.png'
        icon_img = QImage(32, 32, QImage.Format_RGB32)
        icon_img.fill(QColor('gold'))
        self.assertTrue(icon_img.save(str(self.sample_icon), 'PNG'))

    def tearDown(self):
        if hasattr(self, 'engine'):
            self.root.setVisible(False)
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.application.processEvents()
        self.temp_dir.cleanup()
        self.assertEqual(self.games, self.original_games, 'Model input games must remain immutable')

    def settle(self):
        self.application.processEvents()
        QTest.qWait(60)
        self.application.processEvents()

    def evaluate(self, item, expression):
        value = QQmlExpression(QQmlEngine.contextForObject(item), item, expression)
        result, _ = value.evaluate()
        self.assertFalse(value.hasError(), value.error().toString() if value.hasError() else '')
        return result

    def _find_item(self, name: str) -> QQuickItem | None:
        def visit(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                res = visit(child)
                if res is not None:
                    return res
            return None
        return visit(self.root.contentItem())

    def _find_items(self, name: str) -> list[QQuickItem]:
        found = []
        def visit(item):
            if item.objectName() == name:
                found.append(item)
            for child in item.childItems():
                visit(child)
        visit(self.root.contentItem())
        return found

    def click_item(self, item: QQuickItem):
        self.assertTrue(item.property('enabled'))
        if hasattr(item, 'clicked'):
            item.clicked.emit()
        else:
            p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, p)
        self.settle()

    def _create_test_session(self):
        self.engine, self.model, self.root, self.warnings = create_session(
            self.application,
            snapshots=None,
            catalog=None,
            artwork_override_root=self.override_store_path,
            headless=True,
        )
        self.root.resize(1280, 800)
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.root.setVisible(True)
        self.settle()
        return self.engine, self.model, self.root

    def wait_image_ready(self, item: QQuickItem, timeout_ticks=30) -> bool:
        for _ in range(timeout_ticks):
            if self.evaluate(item, 'status === Image.Ready'):
                return True
            self.settle()
        return False

    def test_startup_does_not_eagerly_create_store_on_disk(self):
        """CLI flag --artwork-override-root must not create any files or folders before Apply."""
        self.assertFalse(self.override_store_path.exists())
        self._create_test_session()
        # Verify store path is STILL not created
        self.assertFalse(self.override_store_path.exists(), 'Store path must not exist eagerly on startup')
        artwork_view = getattr(self.engine, 'artwork_view', None)
        self.assertIsNotNone(artwork_view)
        self.assertTrue(artwork_view.storeEnabled)
        self.assertEqual(artwork_view.revision, 0)
        self.assertEqual(artwork_view.undoableAuditId, 0)

    def test_preview_workflow_and_lazy_store_creation_on_apply(self):
        """Select local artwork -> Preview (no disk write) -> Apply (creates store on disk)."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        first_id = self.games[0]['id']

        # Select first game
        self.root.selectGame(first_id)
        self.settle()

        # Step 1: Preview without disk creation
        preview_res = artwork_view.preview(first_id, 'cover', str(self.sample_cover))
        self.assertTrue(preview_res['valid'])
        self.assertEqual(preview_res['format'], 'png')
        self.assertEqual(preview_res['width'], 64)
        self.assertEqual(preview_res['height'], 96)
        self.assertTrue(artwork_view.previewImageUrl.startswith('image://artwork-override/p_'))
        self.assertFalse(self.override_store_path.exists(), 'Preview must not create disk store')

        # Check QML preview box
        preview_box = self._find_item('artworkPreviewBox')
        self.assertIsNotNone(preview_box)
        self.assertTrue(preview_box.property('visible'))

        # Step 2: Discard preview
        artwork_view.discardPreview()
        self.settle()
        self.assertFalse(preview_box.property('visible'))
        self.assertEqual(artwork_view.previewImageUrl, '')
        self.assertFalse(self.override_store_path.exists())

        # Step 3: Preview again and Apply
        artwork_view.preview(first_id, 'cover', str(self.sample_cover))
        self.settle()
        self.assertTrue(preview_box.property('visible'))

        apply_res = artwork_view.apply()
        self.settle()
        self.assertTrue(apply_res['success'])
        self.assertEqual(apply_res['auditId'], 1)
        self.assertEqual(apply_res['revision'], 1)

        # Now the store MUST exist on disk
        self.assertTrue(self.override_store_path.is_dir(), 'Apply must create the store directory')
        self.assertTrue((self.override_store_path / 'state.json').is_file())
        self.assertFalse(preview_box.property('visible'), 'Preview box hides after apply')

        # Step 4: Verify opaque URL in model and overview
        override_url = artwork_view.artworkUrl(first_id, 'cover')
        self.assertTrue(override_url.startswith('image://artwork-override/c_'))
        overview = self.model.overview(first_id)
        self.assertEqual(overview['coverUrl'], override_url)

        row = self.model.rowOf(first_id)
        idx = self.model.index(row, 0)
        cover_role = [k for k, v in self.model.roleNames().items() if v == b'coverUrl'][0]
        self.assertEqual(self.model.data(idx, cover_role), override_url)

        # Step 5: Undo restores original fallback
        self.assertEqual(artwork_view.undoableAuditId, 1)
        undo_res = artwork_view.undo()
        self.settle()
        self.assertTrue(undo_res['success'])
        self.assertEqual(artwork_view.revision, 2)
        self.assertEqual(artwork_view.artworkUrl(first_id, 'cover'), '')

        # Model reverts to original fallback
        reverted_overview = self.model.overview(first_id)
        self.assertEqual(reverted_overview['coverUrl'], '')
        self.assertEqual(self.model.data(idx, cover_role), '')

    def test_qml_button_interactions_preview_apply_undo_flow(self):
        """End-to-end QML UI interaction: preview, QML Apply button click, grid/details raster render, QML Undo click."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        first_id = self.games[0]['id']

        self.root.selectGame(first_id)
        self.root.setView('details')
        self.settle()

        # Editor is visible in details pane
        editor = self._find_item('artworkOverrideEditor')
        self.assertIsNotNone(editor)
        self.assertTrue(editor.property('visible'))

        # Details cover starts as placeholder
        details_cover = self._find_item('detailsCover')
        self.assertIsNotNone(details_cover)
        self.assertTrue(details_cover.property('placeholderVisible'))

        # Preview via adapter
        artwork_view.preview(first_id, 'cover', str(self.sample_cover))
        self.settle()

        # Preview box is visible
        preview_box = self._find_item('artworkPreviewBox')
        self.assertTrue(preview_box.property('visible'))

        # Preview thumbnail loads
        preview_thumb = self._find_item('artworkPreviewThumbnail')
        self.assertIsNotNone(preview_thumb)
        self.assertTrue(self.wait_image_ready(preview_thumb))

        # Click Apply button via QML
        apply_btn = self._find_item('applyArtworkButton')
        self.assertIsNotNone(apply_btn)
        self.click_item(apply_btn)

        # Store created on disk
        self.assertTrue(self.override_store_path.is_dir())
        self.assertEqual(artwork_view.revision, 1)

        # Details cover image now loads opaque provider URL and reaches Image.Ready
        cover_image = details_cover.findChild(QQuickItem, 'coverImage')
        self.assertTrue(self.wait_image_ready(cover_image))
        self.assertTrue(cover_image.property('source').toString().startswith('image://artwork-override/c_'))
        self.assertFalse(details_cover.property('placeholderVisible'))

        # Switch to Grid view and verify grid delegate cover image
        self.root.setView('grid')
        self.settle()
        grid_covers = [c for c in self._find_items('gridArtwork') if c.parentItem().property('workId') == first_id]
        self.assertTrue(len(grid_covers) > 0)
        grid_cover_image = grid_covers[0].findChild(QQuickItem, 'coverImage')
        self.assertTrue(self.wait_image_ready(grid_cover_image))
        self.assertTrue(grid_cover_image.property('source').toString().startswith('image://artwork-override/c_'))

        # Switch back to details and click Undo button
        self.root.setView('details')
        self.settle()
        undo_btn = self._find_item('undoArtworkButton')
        self.assertIsNotNone(undo_btn)
        self.assertTrue(undo_btn.property('enabled'))
        self.click_item(undo_btn)

        # Revision bumped, override removed
        self.assertEqual(artwork_view.revision, 2)
        self.assertEqual(artwork_view.artworkUrl(first_id, 'cover'), '')
        self.assertTrue(details_cover.property('placeholderVisible'))

    def test_qml_button_interactions_reset_role_flow(self):
        """End-to-end QML UI interaction: Reset role button previews removal and Apply restores fallback."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        first_id = self.games[0]['id']

        self.root.selectGame(first_id)
        self.root.setView('details')
        self.settle()

        # Apply initial override
        artwork_view.preview(first_id, 'cover', str(self.sample_cover))
        artwork_view.apply()
        self.settle()
        self.assertEqual(artwork_view.revision, 1)

        # Clear button ("Reset role") should be enabled
        clear_btn = self._find_item('clearArtworkButton')
        self.assertIsNotNone(clear_btn)
        self.assertTrue(clear_btn.property('enabled'))

        # Click Reset role
        self.click_item(clear_btn)

        # Preview box shows removal preview
        preview_box = self._find_item('artworkPreviewBox')
        self.assertTrue(preview_box.property('visible'))
        self.assertTrue(artwork_view.activePreview['isClear'])

        # Click Apply to commit removal
        apply_btn = self._find_item('applyArtworkButton')
        self.click_item(apply_btn)

        self.assertEqual(artwork_view.revision, 2)
        self.assertEqual(artwork_view.artworkUrl(first_id, 'cover'), '')
        self.assertFalse(clear_btn.property('enabled'))

        # Details cover is back to fallback placeholder
        details_cover = self._find_item('detailsCover')
        self.assertTrue(details_cover.property('placeholderVisible'))

    def test_qml_cancel_preview_button(self):
        """Clicking Cancel in QML preview box dismisses preview without disk mutations."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        first_id = self.games[0]['id']

        self.root.selectGame(first_id)
        self.root.setView('details')
        self.settle()

        # Trigger preview
        artwork_view.preview(first_id, 'cover', str(self.sample_cover))
        self.settle()

        preview_box = self._find_item('artworkPreviewBox')
        self.assertTrue(preview_box.property('visible'))

        # Click Cancel
        cancel_btn = self._find_item('cancelArtworkButton')
        self.assertIsNotNone(cancel_btn)
        self.click_item(cancel_btn)

        self.assertFalse(preview_box.property('visible'))
        self.assertEqual(artwork_view.previewImageUrl, '')
        self.assertFalse(self.override_store_path.exists())

    def test_multi_role_overrides_cover_background_icon(self):
        """Overrides can be independently applied to cover, background, and icon."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        game_id = self.games[0]['id']

        # Apply cover
        artwork_view.preview(game_id, 'cover', str(self.sample_cover))
        artwork_view.apply()

        # Apply background
        artwork_view.preview(game_id, 'background', str(self.sample_bg))
        artwork_view.apply()

        # Apply icon
        artwork_view.preview(game_id, 'icon', str(self.sample_icon))
        artwork_view.apply()

        self.settle()
        self.assertEqual(artwork_view.revision, 3)

        overview = self.model.overview(game_id)
        self.assertTrue(overview['coverUrl'].startswith('image://artwork-override/c_'))
        self.assertTrue(overview['backgroundUrl'].startswith('image://artwork-override/c_'))
        self.assertTrue(overview['iconUrl'].startswith('image://artwork-override/c_'))

        # Reset cover only
        artwork_view.preview(game_id, 'cover', '')
        self.assertTrue(artwork_view.activePreview['isClear'])
        artwork_view.apply()
        self.settle()

        overview_after = self.model.overview(game_id)
        self.assertEqual(overview_after['coverUrl'], '')
        self.assertTrue(overview_after['backgroundUrl'].startswith('image://artwork-override/c_'))
        self.assertTrue(overview_after['iconUrl'].startswith('image://artwork-override/c_'))

    def test_security_rejection_of_remote_traversal_and_symlinks(self):
        """Security perimeter: symlinks, remote URLs, and traversal must be rejected with safe error messages."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        game_id = self.games[0]['id']

        # 1. Symlink
        symlink_path = self.root_path / 'symlink_art.png'
        try:
            os.symlink(str(self.sample_cover), str(symlink_path))
            res = artwork_view.preview(game_id, 'cover', str(symlink_path))
            self.assertFalse(res['valid'])
            self.assertIn('Symlinks are not permitted', artwork_view.error)
            self.assertNotIn(str(self.root_path), artwork_view.error)
        finally:
            if symlink_path.is_symlink():
                symlink_path.unlink()

        # 2. Remote URL
        res = artwork_view.preview(game_id, 'cover', 'https://example.com/malicious.png')
        self.assertFalse(res['valid'])
        self.assertIn('local file', artwork_view.error.lower())
        self.assertNotIn('example.com', artwork_view.error)

        # 3. Path traversal
        traversal = str(self.root_path) + '/../' + self.root_path.name + '/sample_cover.png'
        res = artwork_view.preview(game_id, 'cover', traversal)
        self.assertFalse(res['valid'])
        self.assertIn('prohibited', artwork_view.error.lower())

    def test_linked_group_exact_record_targeting(self):
        """In a linked group, overrides can target exact member records."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')

        rep_id = self.games[0]['id']
        member_id = self.games[1]['id']

        # Link member to representative
        self.model.setIdentityLinks({member_id: rep_id})
        self.settle()

        # Apply override specifically to the member record
        preview_res = artwork_view.preview(member_id, 'cover', str(self.sample_cover))
        self.assertTrue(preview_res['valid'])
        self.assertEqual(preview_res['canonicalId'], member_id)
        apply_res = artwork_view.apply()
        self.assertTrue(apply_res['success'])
        self.settle()

        # Member has direct override URL
        member_override_url = artwork_view.artworkUrl(member_id, 'cover')
        self.assertTrue(member_override_url.startswith('image://artwork-override/c_'))
        # Representative does NOT have direct override URL
        self.assertEqual(artwork_view.artworkUrl(rep_id, 'cover'), '')

        # Model overview for the group incorporates member override
        group_overview = self.model.overview(rep_id)
        self.assertEqual(group_overview['coverUrl'], member_override_url)

        # Now apply a distinct override directly to the representative
        artwork_view.preview(rep_id, 'cover', str(self.sample_bg))
        artwork_view.apply()
        self.settle()

        rep_override_url = artwork_view.artworkUrl(rep_id, 'cover')
        self.assertTrue(rep_override_url.startswith('image://artwork-override/c_'))
        self.assertNotEqual(rep_override_url, member_override_url)

        # Representative override takes precedence for the linked group
        updated_group = self.model.overview(rep_id)
        self.assertEqual(updated_group['coverUrl'], rep_override_url)

    def test_native_refresh_preserves_applied_overrides(self):
        """Native observation refresh must not wipe or discard applied artwork overrides."""
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        game_id = self.games[0]['id']

        # Apply override
        artwork_view.preview(game_id, 'cover', str(self.sample_cover))
        artwork_view.apply()
        self.settle()
        override_url = artwork_view.artworkUrl(game_id, 'cover')
        self.assertTrue(override_url.startswith('image://artwork-override/c_'))

        # Simulate native refresh
        self.model.setNativeFilter('Steam')
        self.settle()
        self.model.setNativeFilter('All native providers')
        self.settle()

        # Override remains active and intact
        overview = self.model.overview(game_id)
        self.assertEqual(overview['coverUrl'], override_url)
        self.assertEqual(artwork_view.artworkUrl(game_id, 'cover'), override_url)


    def test_ribbon_hero_and_logo_are_manual_override_roles(self):
        self._create_test_session()
        artwork_view = getattr(self.engine, 'artwork_view')
        game_id = self.games[0]['id']
        for role, path in (('hero', self.sample_bg), ('logo', self.sample_icon)):
            result = artwork_view.preview(game_id, role, str(path))
            self.assertTrue(result.get('valid'), result)
            self.assertTrue(artwork_view.apply().get('success'))
        overview = self.model.overview(game_id)
        self.assertTrue(overview['heroUrl'].startswith('image://artwork-override/c_'))
        self.assertTrue(overview['logoUrl'].startswith('image://artwork-override/c_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
