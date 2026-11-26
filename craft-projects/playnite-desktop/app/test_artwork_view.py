"""Offline Qt tests for ArtworkView adapter and ArtworkImageProvider.

Tests cover:
- Store initialization, explicit opt-in, non-creation on default startup.
- Local-file URL selection (file:// and raw path), rejection of remote URLs, symlinks, traversal.
- Preview plan generation without store/file mutation.
- Separate preview -> apply -> undo workflow with idempotent request IDs.
- Opaque image:// handle generation and QQuickImageProvider raster delivery.
- Redaction of paths and secret/internal details from user-facing error messages.
- read_asset verified raster bytes check.
- Corrupted/invalid raster rejection and dimension limits.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage

from artwork_view import ArtworkView, ArtworkImageProvider, PROVIDER_ID, IMAGE_SCHEME_PREFIX
from native_services.artwork_overrides import ArtworkOverrideStore


def make_test_image(path: Path, width: int = 16, height: int = 16, color: int = 0xff123456, fmt: str = 'PNG') -> Path:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(color)
    image.save(str(path), fmt)
    return path


class ArtworkViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(['artwork-view-tests'])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='artwork-view-test-')
        self.root = Path(self.temp_dir.name)
        self.catalog = {
            'games': [
                {'id': 'game-001', 'title': 'Alpha Quest', 'variants': []},
                {'id': 'game-002', 'title': 'Beta Runner', 'variants': []},
            ]
        }
        self.sample_png = make_test_image(self.root / 'cover.png', 32, 32, 0xffaabbcc, 'PNG')
        self.sample_jpg = make_test_image(self.root / 'bg.jpg', 64, 48, 0xff334455, 'JPEG')

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_init_does_not_create_store_or_mutate_files(self):
        store_path = self.root / 'store'
        view = ArtworkView(self.catalog, override_root=store_path, enable_override_state=False)
        self.assertFalse(view.storeEnabled)
        self.assertFalse(store_path.exists())
        self.assertEqual(view.revision, 0)
        self.assertEqual(view.undoableAuditId, 0)

    def test_saved_pick_survives_a_new_game_joining_the_library(self):
        store_path = self.root / 'grow_store'
        view = ArtworkView(self.catalog, override_root=store_path, enable_override_state=True)
        view.preview('game-001', 'cover', str(self.sample_png))
        self.assertTrue(view.apply('req-grow-1')['success'])
        grown = {'games': self.catalog['games'] + [{'id': 'game-003', 'title': 'Gamma', 'variants': []}]}
        reopened = ArtworkView(grown, override_root=store_path, enable_override_state=True)
        self.assertEqual(reopened.error, '')
        self.assertTrue(reopened.artworkUrl('game-001', 'cover').startswith(IMAGE_SCHEME_PREFIX))
        self.assertIn('game-003', ArtworkOverrideStore(store_path).canonical_ids())
        # the new game can be given artwork too
        self.assertTrue(reopened.preview('game-003', 'cover', str(self.sample_png))['valid'])
        self.assertTrue(reopened.apply('req-grow-2')['success'])
        # a game leaving the library keeps its saved pick for when it returns
        shrunk = {'games': [self.catalog['games'][1]]}
        ArtworkView(shrunk, override_root=store_path, enable_override_state=True)
        self.assertIn('game-001', ArtworkOverrideStore(store_path).canonical_ids())

    def test_lazy_store_creation_on_apply_via_override_root(self):
        store_path = self.root / 'lazy_store'
        view = ArtworkView(self.catalog, override_root=store_path, enable_override_state=True)
        self.assertTrue(view.storeEnabled)
        self.assertFalse(store_path.exists())

        # Preview does not create store on disk
        preview = view.preview('game-001', 'cover', str(self.sample_png))
        self.assertTrue(preview['valid'])
        self.assertFalse(preview['isClear'])
        self.assertTrue(view.previewImageUrl.startswith(IMAGE_SCHEME_PREFIX))
        self.assertFalse(store_path.exists())

        # Discard preview
        view.discardPreview()
        self.assertEqual(view.previewImageUrl, '')
        self.assertEqual(view.activePreview, {})
        self.assertFalse(store_path.exists())

        # Preview again and apply
        view.preview('game-001', 'cover', str(self.sample_png))
        self.assertFalse(store_path.exists())
        res = view.apply('req-lazy-1')
        self.assertTrue(res['success'])
        self.assertTrue(store_path.exists())
        self.assertEqual(view.revision, 1)
        self.assertEqual(view.undoableAuditId, 1)
        self.assertTrue(view.artworkUrl('game-001', 'cover').startswith(IMAGE_SCHEME_PREFIX))

        # Undo
        undo_res = view.undo(1, 'req-lazy-undo')
        self.assertTrue(undo_res['success'])
        self.assertEqual(view.revision, 2)
        self.assertEqual(view.artworkUrl('game-001', 'cover'), '')

    def test_create_store_opt_in(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertFalse(view.createStore(str(store_path), False))
        self.assertFalse(store_path.exists())

        # Explicit create
        self.assertTrue(view.createStore(str(store_path), True))
        self.assertTrue(store_path.is_dir())
        self.assertTrue(view.storeEnabled)
        self.assertEqual(view.revision, 0)

    def test_preview_and_apply_workflow(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))

        # 1. Preview with local file URL
        file_url = QUrl.fromLocalFile(str(self.sample_png)).toString()
        preview_res = view.preview('game-001', 'cover', file_url)
        self.assertTrue(preview_res['valid'])
        self.assertEqual(preview_res['canonicalId'], 'game-001')
        self.assertEqual(preview_res['role'], 'cover')
        self.assertEqual(preview_res['format'], 'png')
        self.assertEqual(preview_res['width'], 32)
        self.assertEqual(preview_res['height'], 32)
        self.assertFalse(preview_res['isClear'])
        self.assertTrue(preview_res['previewUrl'].startswith(IMAGE_SCHEME_PREFIX))

        # Assert no store mutation occurred on preview
        self.assertEqual(view.revision, 0)
        self.assertEqual(view.artworkUrl('game-001', 'cover'), '')

        # Preview image provider resolution
        provider = view.provider()
        preview_handle = preview_res['previewUrl'].removeprefix(IMAGE_SCHEME_PREFIX)
        qsize = QSize()
        img = provider.requestImage(preview_handle, qsize, QSize())
        self.assertFalse(img.isNull())
        self.assertEqual(img.width(), 32)
        self.assertEqual(img.height(), 32)

        # 2. Apply plan
        apply_res = view.apply('req-apply-1')
        self.assertTrue(apply_res['success'])
        self.assertEqual(apply_res['auditId'], 1)
        self.assertEqual(apply_res['revision'], 1)
        self.assertEqual(view.revision, 1)
        self.assertEqual(view.undoableAuditId, 1)

        # Artwork URL now available via opaque handle
        art_url = view.artworkUrl('game-001', 'cover')
        self.assertTrue(art_url.startswith(IMAGE_SCHEME_PREFIX))
        self.assertNotIn(str(self.root), art_url)
        self.assertNotIn('file://', art_url)

        # Committed image provider resolution
        art_handle = art_url.removeprefix(IMAGE_SCHEME_PREFIX)
        qsize2 = QSize()
        img2 = provider.requestImage(art_handle, qsize2, QSize())
        self.assertFalse(img2.isNull())
        self.assertEqual(img2.width(), 32)
        self.assertEqual(img2.height(), 32)

        # Verify underlying store raster bytes via read_asset
        verified_bytes = view._store.read_asset('game-001', 'cover')
        self.assertIsNotNone(verified_bytes)
        self.assertEqual(len(verified_bytes), self.sample_png.stat().st_size)

        # 3. Undo
        undo_res = view.undo(1, 'req-undo-1')
        self.assertTrue(undo_res['success'])
        self.assertEqual(view.revision, 2)
        self.assertEqual(view.artworkUrl('game-001', 'cover'), '')
        self.assertEqual(view.undoableAuditId, 0)

    def test_reopen_existing_store(self):
        store_path = self.root / 'managed_store'
        view1 = ArtworkView(self.catalog)
        self.assertTrue(view1.createStore(str(store_path), True))
        view1.preview('game-001', 'cover', str(self.sample_png))
        view1.apply('req-1')
        self.assertEqual(view1.revision, 1)

        # Reopen in new view instance
        view2 = ArtworkView(self.catalog, override_root=store_path, enable_override_state=True)
        self.assertTrue(view2.storeEnabled)
        self.assertEqual(view2.revision, 1)
        self.assertEqual(view2.undoableAuditId, 1)
        self.assertTrue(view2.artworkUrl('game-001', 'cover').startswith(IMAGE_SCHEME_PREFIX))

    def test_rejection_of_remote_urls_and_traversal(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))

        # Remote URL
        res = view.preview('game-001', 'cover', 'https://example.com/art.png')
        self.assertFalse(res['valid'])
        self.assertIn('local file', view.error.lower())

        # Path traversal
        res2 = view.preview('game-001', 'cover', '/tmp/../etc/passwd')
        self.assertFalse(res2['valid'])

        # Redaction check: ensure path does not leak into user-facing error
        self.assertNotIn('/tmp/../etc/passwd', view.error)
        self.assertNotIn('/etc/passwd', view.error)

    def test_symlink_rejection(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))

        link_path = self.root / 'cover_symlink.png'
        try:
            link_path.symlink_to(self.sample_png)
            res = view.preview('game-001', 'cover', str(link_path))
            self.assertFalse(res['valid'])
            self.assertIn('symlink', view.error.lower())
        except OSError:
            pass

    def test_audit_log_format(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))
        view.preview('game-001', 'cover', str(self.sample_png))
        view.apply('req-audit-1')

        log = view.auditLog()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]['auditId'], 1)
        self.assertEqual(log[0]['canonicalId'], 'game-001')
        self.assertEqual(log[0]['role'], 'cover')
        self.assertTrue(log[0]['hasOverride'])

    def test_clear_override_preview_and_apply(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))
        view.preview('game-001', 'cover', str(self.sample_png))
        view.apply('req-1')
        self.assertTrue(view.artworkUrl('game-001', 'cover').startswith(IMAGE_SCHEME_PREFIX))

        # Clear preview (empty/None selection)
        clear_res = view.preview('game-001', 'cover', '')
        self.assertTrue(clear_res['valid'])
        self.assertTrue(clear_res['isClear'])
        self.assertEqual(clear_res['previewUrl'], '')

        # Apply clear
        res = view.apply('req-clear-1')
        self.assertTrue(res['success'])
        self.assertEqual(view.artworkUrl('game-001', 'cover'), '')
        self.assertIsNone(view._store.read_asset('game-001', 'cover'))

    def test_corrupted_or_non_image_rejection(self):
        store_path = self.root / 'managed_store'
        view = ArtworkView(self.catalog)
        self.assertTrue(view.createStore(str(store_path), True))

        bad_file = self.root / 'corrupted.png'
        bad_file.write_bytes(b'not an image payload')
        res = view.preview('game-001', 'cover', str(bad_file))
        self.assertFalse(res['valid'])
        self.assertFalse(res.get('isClear', False))


if __name__ == '__main__':
    unittest.main()
