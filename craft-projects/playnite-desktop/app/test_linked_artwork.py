"""Offline linked-artwork regressions; only synthetic images and catalogs."""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from library_model import LibraryModel
from media_catalog import MediaCatalog


def claim(reference):
    return {'status': 'local_present', 'relative_reference': reference}


def game(ident, source):
    return {'id': ident, 'title': 'Same Title', 'variants': [
        {'source': source, 'source_id': ident, 'platform': 'PC', 'status': 'unknown'}]}


class LinkedArtworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='linked-artwork-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.colors = {'fallback.png': 0xff123456, 'explicit.png': 0xffabcdef,
                       'icon.png': 0xff112233, 'other.png': 0xff445566}
        for name, color in self.colors.items():
            image = QImage(2, 3, QImage.Format_ARGB32)
            image.fill(color)
            self.assertTrue(image.save(str(self.root / name), 'PNG'))
        self.provider = MediaCatalog(self.root)

    def references(self, url):
        return self.provider._handles[url.rsplit('/', 1)[-1]]

    def pixel(self, url):
        self.assertTrue(url)
        image = self.provider.requestImage(url.rsplit('/', 1)[-1], QSize(), QSize())
        self.assertFalse(image.isNull())
        return image.pixel(0, 0)

    def test_member_game_cover_survives_variant_with_only_icon(self):
        games = [game('a', 'Unassigned'), game('b', 'Steam')]
        games[1]['artwork'] = {'cover': claim('fallback.png')}
        games[1]['variants'][0]['artwork'] = {'icon': claim('icon.png')}
        original = copy.deepcopy(games)
        model = LibraryModel(games, media=self.provider)
        self.assertEqual(self.pixel(model.overview('b')['coverUrl']), self.colors['fallback.png'])
        model.setIdentityLinks({'b': 'a'})
        overview = model.overview('a')
        self.assertEqual(self.pixel(overview['coverUrl']), self.colors['fallback.png'])
        self.assertEqual(self.pixel(overview['iconUrl']), self.colors['icon.png'])
        self.assertEqual(overview['variantCount'], 2)
        self.assertEqual(games, original)
        reordered = LibraryModel(list(reversed(games)), media=self.provider)
        reordered.setIdentityLinks({'b': 'a'})
        self.assertEqual(reordered.overview('a')['coverUrl'], overview['coverUrl'])

    def test_empty_links_keep_explicit_variant_ahead_of_game_fallback(self):
        raw = game('a', 'a')
        raw['artwork'] = {'cover': claim('fallback.png')}
        later = game('z', 'z')['variants'][0]
        later['artwork'] = {'cover': claim('explicit.png')}
        raw['variants'].append(later)
        original = copy.deepcopy(raw)
        model = LibraryModel([raw], media=self.provider)
        before = model.overview('a')['coverUrl']
        self.assertEqual(self.references(before), ('explicit.png', 'fallback.png'))
        model.setIdentityLinks({})
        after = model.overview('a')['coverUrl']
        self.assertEqual(after, before)
        self.assertEqual(self.pixel(after), self.colors['explicit.png'])
        self.assertNotIn('artwork', model.games[0]['variants'][0])
        self.assertEqual(raw, original)

    def test_variant_priority_then_member_id_order_for_every_role(self):
        raw = game('representative', 'Steam')
        raw['variants'][0]['artwork'] = {'cover': claim('explicit.png')}
        raw['artwork'] = {'cover': claim('other.png')}
        raw['linked_member_artwork'] = [
            {'cover': claim('fallback.png'), 'background': claim('fallback.png')},
            {role: claim('other.png') for role in ('cover', 'background', 'icon')},
        ]
        original = copy.deepcopy(raw)
        urls = self.provider.urls_for(raw)
        self.assertEqual(self.references(urls['coverUrl']),
                         ('explicit.png', 'fallback.png', 'other.png'))
        self.assertEqual(self.pixel(urls['coverUrl']), self.colors['explicit.png'])
        self.assertEqual(self.pixel(urls['backgroundUrl']), self.colors['fallback.png'])
        self.assertEqual(self.pixel(urls['iconUrl']), self.colors['other.png'])
        self.assertEqual(raw, original)

    def test_unusable_member_role_falls_through_to_next_member(self):
        raw = game('a', 'Steam')
        raw['linked_member_artwork'] = [
            {'cover': claim('missing.png'),
             'icon': {'status': 'unknown', 'relative_reference': 'other.png'}},
            {'cover': claim('fallback.png'), 'icon': claim('icon.png')},
        ]
        urls = self.provider.urls_for(raw)
        self.assertEqual(self.pixel(urls['coverUrl']), self.colors['fallback.png'])
        self.assertEqual(self.pixel(urls['iconUrl']), self.colors['icon.png'])


if __name__ == '__main__':
    unittest.main()
