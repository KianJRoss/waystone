"""Bounded offline media/catalog tests. Only synthetic images in temporary roots."""
import copy
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
import media_catalog as media
from library_model import LibraryModel, load_fixture, validate_catalog
from snapshot_bridge import snapshot_games

HERE = Path(__file__).resolve().parent


def claim(reference, status='local_present'):
    return {'status': status, 'relative_reference': reference, 'role_source': 'selected-copy',
            'provenance': {'same_generation_verified': False, 'observed_at': 'fixture'}}


def variant(identity, reference):
    return {'source': 'Playnite', 'source_id': identity, 'installation_id': 'playnite-record:' + identity,
            'platform': 'unknown', 'status': 'unknown', 'recipe': [],
            'source_context': {'host': 'fixture'}, 'observation': {'complete': False},
            'evidence': {'kind': 'selected-copy'}, 'artwork': {'cover': claim(reference)}}


def game(identity='canonical:a', variants=None):
    return {'id': identity, 'title': 'Same Title', 'variants': variants or [variant('a', 'tiny.png')]}


class MediaCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='catalog-media-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = QImage(2, 3, QImage.Format_ARGB32)
        self.image.fill(0xff123456)
        self.assertTrue(self.image.save(str(self.root / 'tiny.png'), 'PNG'))
        self.provider = media.MediaCatalog(self.root)

    def request(self, url):
        return self.provider.requestImage(url.rsplit('/', 1)[-1], QSize(), QSize())

    def test_allowed_tiny_rasters_explicit_decoder(self):
        for fmt, suffix in [('PNG', 'png'), ('JPEG', 'jpg'), ('WEBP', 'webp'), ('BMP', 'bmp'), ('ICO', 'ico')]:
            with self.subTest(format=fmt):
                self.assertTrue(self.image.save(str(self.root / ('image.' + suffix)), fmt))
                url = self.provider.urls_for(game(variants=[variant('a', 'image.' + suffix)]))['coverUrl']
                self.assertTrue(url.startswith('image://catalog-media/'))
                self.assertEqual(self.request(url).size(), self.image.size())

    def test_roles_overview_and_immutable_original_provenance(self):
        raw = game()
        raw['variants'][0]['artwork'].update(background=claim('tiny.png'), icon=claim('tiny.png'))
        before = copy.deepcopy(raw)
        model = LibraryModel([raw], media=self.provider)
        roles = {name.decode(): role for role, name in model.roleNames().items()}
        overview = model.overview(raw['id'])
        for name in ('coverUrl', 'backgroundUrl', 'iconUrl'):
            self.assertTrue(overview[name].startswith('image://catalog-media/'))
            self.assertEqual(model.data(model.index(0), roles[name]), overview[name])
        details = model.details(raw['id'])
        self.assertIn('same_generation_verified', details)
        self.assertIn('local_present', details)
        self.assertEqual(raw, before)

    def test_catalog_display_uses_labeled_claims_and_never_fixture_fallback(self):
        raw = game()
        raw['variants'][0]['source_context'] = {}
        raw['variants'][0]['observation'] = {
            'cached_source_modified_at': '2026-10-16T04:37:52Z',
            'role_observed_at': '2026-11-15T04:10:25Z',
            'same_generation_verified': False,
        }
        before = copy.deepcopy(raw)
        view = LibraryModel([raw], catalog_mode=True).overview(raw['id'])
        self.assertEqual(view['hosts'], 'Not supplied (cached export)')
        self.assertEqual(view['observed'], 'Cached source modified: 2026-10-16T04:37:52Z; Selected-copy roles observed: 2026-11-15T04:10:25Z')
        self.assertEqual(raw, before)
        raw['variants'][0]['source_context']['host'] = 'Selected host claim'
        raw['variants'][0]['observation'] = {'at': '2026-11-14T00:00:00Z'}
        view = LibraryModel([raw], catalog_mode=True).overview(raw['id'])
        self.assertEqual(view['hosts'], 'Selected host claim')
        self.assertEqual(view['observed'], 'Record observed: 2026-11-14T00:00:00Z')
        for missing in ({}, {'cached_source_modified_at': None, 'role_observed_at': 123, 'at': ''}):
            raw['variants'][0]['observation'] = missing
            self.assertEqual(LibraryModel([raw], catalog_mode=True).overview(raw['id'])['observed'], 'Not supplied (cached export)')
        self.assertEqual(LibraryModel([raw]).overview(raw['id'])['observed'], 'Unknown / fixture')

    def test_missing_explicit_root_never_uses_json_roots(self):
        raw = game()
        raw['media_root'] = str(self.root)
        raw['variants'][0]['source_context']['root'] = str(self.root)
        for model in (LibraryModel([raw]), LibraryModel([raw], media=media.MediaCatalog())):
            overview = model.overview(raw['id'])
            self.assertEqual([overview[k] for k in ('coverUrl', 'backgroundUrl', 'iconUrl')], ['', '', ''])

    def test_stable_identity_order_title_collisions_rename_and_reorder(self):
        self.image.fill(0xffabcdef)
        self.assertTrue(self.image.save(str(self.root / 'second.png'), 'PNG'))
        first = game(variants=[variant('z', 'second.png'), variant('a', 'tiny.png')])
        second = game('canonical:b', [variant('b', 'second.png')])
        urls = self.provider.urls_for(first)
        reordered = copy.deepcopy(first)
        reordered['title'] = 'Renamed without identity changes'
        reordered['variants'].reverse()
        self.assertEqual(urls, self.provider.urls_for(reordered))
        self.assertNotEqual(urls['coverUrl'], self.provider.urls_for(second)['coverUrl'])
        self.assertEqual(self.request(urls['coverUrl']).pixel(0, 0), 0xff123456)
        model = LibraryModel([first, second], media=self.provider)
        self.assertIn('playnite-record:z', model.details(first['id']))
        self.assertIn('playnite-record:a', model.details(first['id']))
        self.assertEqual(model.overview(first['id'])['variantCount'], 2)

    def test_first_available_candidate_skips_bad_and_unknown_roles(self):
        raw = game(variants=[variant('a', 'absent.png'), variant('b', 'tiny.png')])
        url = self.provider.urls_for(raw)['coverUrl']
        self.assertFalse(self.request(url).isNull())
        raw['variants'][1]['artwork']['cover']['status'] = 'unknown'
        self.assertEqual(self.provider.urls_for(raw)['coverUrl'], '')

    def test_external_absolute_drive_traversal_and_controls_fail_closed(self):
        references = ['https://example.test/a.png', 'file:///etc/passwd', '//server/a.png',
                      str(self.root / 'tiny.png'), 'C:/tiny.png', 'C:\\tiny.png', '../tiny.png',
                      'nested/../../tiny.png', './tiny.png', 'nested//tiny.png', 'tiny.png\x00', 'tiny.png\n']
        for reference in references:
            with self.subTest(reference=reference):
                self.assertEqual(self.provider.urls_for(game(variants=[variant('a', reference)]))['coverUrl'], '')

    def test_symlink_file_directory_nonregular_and_no_root_escape(self):
        (self.root / 'link.png').symlink_to(self.root / 'tiny.png')
        (self.root / 'linkdir').symlink_to(self.root, target_is_directory=True)
        (self.root / 'directory.png').mkdir()
        os.mkfifo(self.root / 'fifo.png')
        for reference in ('link.png', 'linkdir/tiny.png', 'directory.png', 'fifo.png'):
            self.assertEqual(self.provider.urls_for(game(variants=[variant('a', reference)]))['coverUrl'], '')

    def test_provider_rechecks_after_resolution_and_never_interprets_ids_as_paths(self):
        url = self.provider.urls_for(game())['coverUrl']
        (self.root / 'tiny.png').unlink()
        (self.root / 'tiny.png').symlink_to('/etc/passwd')
        self.assertTrue(self.request(url).isNull())
        self.assertTrue(self.provider.requestImage('../tiny.png', QSize(), QSize()).isNull())

    def test_corrupt_svg_html_and_disguised_remote_content_rejected(self):
        for payload in (b'broken', b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'<html><img src="https://example.test"></html>'):
            (self.root / 'bad.png').write_bytes(payload)
            self.assertEqual(self.provider.urls_for(game(variants=[variant('a', 'bad.png')]))['coverUrl'], '')

    def test_oversized_file_and_pixel_bomb_rejected_before_decode(self):
        with (self.root / 'huge.png').open('wb') as stream:
            stream.truncate(media.MAX_MEDIA_BYTES + 1)
        self.assertEqual(self.provider.urls_for(game(variants=[variant('a', 'huge.png')]))['coverUrl'], '')
        header = struct.pack('>IIBBBBB', 5000, 5000, 8, 6, 0, 0, 0)
        chunk = b'IHDR' + header
        raw = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', len(header)) + chunk + struct.pack('>I', zlib.crc32(chunk)) + (self.root / 'tiny.png').read_bytes()[33:]
        (self.root / 'bomb.png').write_bytes(raw)
        with patch.object(media.QImageReader, 'read', side_effect=AssertionError('must not decode')):
            self.assertEqual(self.provider.urls_for(game(variants=[variant('a', 'bomb.png')]))['coverUrl'], '')
            self.assertTrue(media._image(raw, decode=True).isNull())

    def test_no_eager_decode_at_model_creation_and_no_image_cache(self):
        with patch.object(media.QImageReader, 'read', side_effect=AssertionError('eager decode')):
            model = LibraryModel([game()], media=self.provider)
            url = model.overview('canonical:a')['coverUrl']
        self.assertFalse(self.request(url).isNull())
        (self.root / 'tiny.png').write_bytes(b'corrupt after first request')
        self.assertTrue(self.request(url).isNull())

    def test_catalog_regular_bounded_shape_and_original_provenance(self):
        data = {'schema_version': 1, 'catalog_kind': media.CATALOG_KIND, 'games': [game()],
                'provenance': {'same_generation_verified': False, 'root': '/untrusted/json/root'}}
        path = self.root / 'catalog.json'
        path.write_text(json.dumps(data))
        self.assertEqual(media.load_catalog(path), data)
        (self.root / 'catalog-link').symlink_to(path)
        with self.assertRaises(OSError):
            media.load_catalog(self.root / 'catalog-link')
        with self.assertRaises(ValueError):
            media.load_catalog(self.root)
        os.mkfifo(self.root / 'catalog-fifo')
        with self.assertRaises(ValueError):
            media.load_catalog(self.root / 'catalog-fifo')

    def test_catalog_actual_byte_cap_not_only_declared_stat_size(self):
        path = self.root / 'catalog.json'
        path.write_bytes(b' ' * 65)
        fake = SimpleNamespace(st_mode=0o100600, st_size=1, st_mtime_ns=0, st_ctime_ns=0)
        with patch.object(media, 'MAX_CATALOG_BYTES', 64), patch.object(media.os, 'fstat', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'Actual file byte limit'):
                media.load_catalog(path)

    def test_malformed_json_shapes_nonfinite_and_invalid_variants(self):
        path = self.root / 'catalog.json'
        for raw in ('[]', '{}', '{', '{"catalog_kind":"playnite-display-selected-copy-v1","schema_version":1,"games":NaN}'):
            path.write_text(raw)
            with self.assertRaises(ValueError):
                media.load_catalog(path)
        for games in ([None], [{'id': 'a', 'title': 'A', 'variants': [None]}],
                      [{'id': 'a', 'title': 'A', 'variants': 'bad'}]):
            with self.assertRaises(ValueError):
                validate_catalog({'schema_version': 1, 'games': games})

    def test_fixtures_and_snapshot_projection_unchanged_with_empty_media_roles(self):
        fixture = load_fixture(HERE / 'fixture-library.json')
        snapshot = json.loads((HERE / 'fixtures/demo-snapshot.json').read_text())
        games = snapshot_games(snapshot)
        for records in (fixture, games):
            before = copy.deepcopy(records)
            model = LibraryModel(records)
            for record in records:
                self.assertEqual(model.overview(record['id'])['coverUrl'], '')
            self.assertEqual(records, before)

    def test_cli_catalog_snapshot_mutual_exclusion_before_qt_startup(self):
        result = subprocess.run([sys.executable, '-B', str(HERE / 'app.py'), '--catalog', 'a', '--snapshot', 'b'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not allowed with argument', result.stderr)

    def test_real_qml_provider_dispatch_decodes_without_file_url(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine, QQmlComponent
        application = QGuiApplication.instance() or QGuiApplication(['offline-provider-test'])
        engine = QQmlEngine()
        provider = media.MediaCatalog(self.root)
        url = provider.urls_for(game())['coverUrl']
        engine.addImageProvider('catalog-media', provider)
        component = QQmlComponent(engine)
        component.setData(('import QtQuick\nImage { source: "' + url + '"; cache: false; property bool ready: status === Image.Ready }').encode(), QUrl())
        image = component.create()
        self.assertIsNotNone(image, [error.toString() for error in component.errors()])
        self.assertTrue(image.property('ready'))
        self.assertEqual(image.property('implicitWidth'), 2)
        self.assertEqual(image.property('implicitHeight'), 3)
        image.deleteLater()
        engine.deleteLater()
        application.processEvents()

    def test_app_catalog_notice_and_provider_registration(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QT_QUICK_BACKEND', 'software')
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QGuiApplication
        from app import create_session
        application = QGuiApplication.instance() or QGuiApplication(['offline-catalog-test'])
        provenance = {'same_generation_verified': False, 'note': '<b>literal claim</b>'}
        data = {'schema_version': 1, 'catalog_kind': media.CATALOG_KIND, 'games': [game()], 'provenance': provenance}
        path = self.root / 'catalog.json'
        path.write_text(json.dumps(data))
        engine, model, root, warnings = create_session(application, headless=True, catalog=path, media_root=self.root)
        try:
            self.assertIsNotNone(engine.imageProvider('catalog-media'))
            self.assertEqual(model.overview('canonical:a')['observed'], 'Not supplied (cached export)')
            self.assertIn(json.dumps(provenance, ensure_ascii=False, sort_keys=True), engine.rootContext().contextProperty('catalogNotice'))
            self.assertTrue(model.overview('canonical:a')['coverUrl'].startswith('image://catalog-media/'))
            self.assertEqual(warnings, [])
        finally:
            root.setVisible(False)
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            application.processEvents()


if __name__ == '__main__':
    unittest.main()
