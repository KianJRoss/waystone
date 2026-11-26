"""UI-only raster contract tests; synthetic files, offscreen/software, no backend writes."""
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
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from library_model import LibraryModel, load_fixture

HERE = Path(__file__).parent
MEDIA_ROLES = ('coverUrl', 'backgroundUrl', 'iconUrl')


class SyntheticMedia:
    """Inject local test URLs through the real model's media-provider boundary.

    This is deliberately not a backend resolution/validation test. Every test URL
    is an explicit tiny synthetic local raster (or a removed/missing test file).
    """
    def __init__(self, media):
        self.media = media

    def urls_for(self, game):
        return {name: self.media.get(game['id'], {}).get(name, '') for name in MEDIA_ROLES}


class ArtworkUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QGuiApplication.instance() or QGuiApplication(['offline-artwork-ui-tests'])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='craft-artwork-ui-')
        self.directory = Path(self.temporary.name)
        self.games = load_fixture(HERE / 'fixture-library.json')
        self.original_games = copy.deepcopy(self.games)
        self.media = {}
        for index, game in enumerate(self.games):
            roles = {}
            for role, color in zip(MEDIA_ROLES, ['red', 'green', 'blue']):
                path = self.directory / f'{index}-{role}.png'
                image = QImage(4, 4, QImage.Format_RGB32)
                image.fill(QColor(color))
                self.assertTrue(image.save(str(path)))
                roles[role] = QUrl.fromLocalFile(str(path)).toString()
            self.media[game['id']] = roles
        self.warnings = []

    def create(self):
        self.model = LibraryModel(self.games, media=SyntheticMedia(self.media))
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda warnings: self.warnings.extend(w.toString() for w in warnings))
        for name, value in {
            'libraryModel': self.model, 'catalogNotice': 'Synthetic artwork contract test only',
            'catalogLabel': 'Synthetic artwork fixture', 'organizerNotice': 'Read-only test',
            'initialOrganizerView': False, 'initialView': 'grid', 'headlessCheck': True,
            'sourceOptions': ['All sources'] + sorted({v['source'] for g in self.games for v in g['variants']}),
            'platformOptions': ['All platforms'] + sorted({v['platform'] for g in self.games for v in g['variants']}),
        }.items():
            self.engine.rootContext().setContextProperty(name, value)
        self.engine.load(QUrl.fromLocalFile(str(HERE / 'Main.qml')))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.root = self.engine.rootObjects()[0]
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.root.setVisible(True)
        self.settle()

    def tearDown(self):
        if hasattr(self, 'engine'):
            self.root.setVisible(False)
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.application.processEvents()
        self.temporary.cleanup()
        self.assertEqual(self.games, self.original_games, 'UI must not mutate model game dictionaries')

    def settle(self):
        self.application.processEvents()
        QTest.qWait(80)
        self.application.processEvents()

    def items(self, name):
        found = []

        def visit(item):
            if item.objectName() == name:
                found.append(item)
            for child in item.childItems():
                visit(child)

        visit(self.root.contentItem())
        return found

    def item(self, name):
        matches = self.items(name)
        self.assertTrue(matches, name)
        return matches[0]

    def evaluate(self, item, expression):
        value = QQmlExpression(QQmlEngine.contextForObject(item), item, expression)
        result, undefined = value.evaluate()
        self.assertFalse(value.hasError(), value.error().toString())
        return result

    def wait_ready(self, item):
        for _ in range(30):
            if self.evaluate(item, 'status === Image.Ready'):
                return
            self.settle()
        self.fail('Raster did not reach Ready: ' + str(item.property('source')) + '\n' + '\n'.join(self.warnings))

    def assert_source(self, item, expected):
        self.assertEqual(item.property('source').toString(), expected)

    def test_portrait_covers_load_by_role_with_bounded_async_decode(self):
        self.create()
        for cover in self.items('gridArtwork'):
            work_id = cover.parentItem().property('workId')
            image = cover.findChild(QQuickItem, 'coverImage')
            if cover.property('loadEnabled'):
                self.wait_ready(image)
                self.assert_source(image, self.media[work_id]['coverUrl'])
                self.assertFalse(cover.property('placeholderVisible'))
                self.assertTrue(image.property('asynchronous'))
                self.assertTrue(image.property('cache'))
                self.assertLessEqual(image.property('sourceSize').width(), 512)
                self.assertLessEqual(image.property('sourceSize').height(), 768)
        self.assertEqual(self.warnings, [])

    def test_empty_and_error_roles_show_quiet_placeholder_without_stale_art(self):
        first, second = self.games[0]['id'], self.games[1]['id']
        self.media[first] = {name: '' for name in MEDIA_ROLES}
        self.media[second] = {name: '' for name in MEDIA_ROLES}
        self.media[second]['coverUrl'] = QUrl.fromLocalFile(str(self.directory / 'missing-cover.png')).toString()
        self.create()
        for work_id, expected_status in [(first, 0), (second, 3)]:
            cover = next(c for c in self.items('gridArtwork') if c.parentItem().property('workId') == work_id)
            for _ in range(20):
                if cover.property('artworkStatus') == expected_status:
                    break
                self.settle()
            self.assertEqual(cover.property('artworkStatus'), expected_status)
            self.assertTrue(cover.property('placeholderVisible'))
        self.root.selectGame(self.games[2]['id'])
        self.root.setView('details')
        self.settle()
        self.wait_ready(self.item('detailsBackground'))
        self.wait_ready(self.item('detailsHeaderIcon'))
        self.root.selectGame(first)
        self.settle()
        self.assert_source(self.item('detailsBackground'), '')
        self.assert_source(self.item('detailsHeaderIcon'), '')
        self.assertTrue(self.item('detailsCover').property('placeholderVisible'))
        self.root.selectGame(second)
        self.settle()
        self.assertEqual(self.item('detailsCover').property('artworkStatus'), 3)
        self.assertTrue(self.item('detailsCover').property('placeholderVisible'))
        self.assertTrue(all('missing-cover.png' in warning for warning in self.warnings), self.warnings)

    def test_same_title_records_keep_distinct_art_through_sort_filter_and_views(self):
        first, second = self.games[0]['id'], self.games[1]['id']
        self.assertEqual(self.games[0]['title'], self.games[1]['title'])
        self.create()

        def selected_media(work_id):
            self.assertEqual(self.root.property('selectedId'), work_id)
            self.root.setView('details')
            self.settle()
            self.wait_ready(self.item('detailsBackground'))
            self.wait_ready(self.item('detailsHeaderIcon'))
            self.wait_ready(self.item('detailsCover').findChild(QQuickItem, 'coverImage'))
            self.assert_source(self.item('detailsBackground'), self.media[work_id]['backgroundUrl'])
            self.assert_source(self.item('detailsHeaderIcon'), self.media[work_id]['iconUrl'])
            self.assertEqual(self.item('detailsCover').property('artworkSource').toString(), self.media[work_id]['coverUrl'])
            for name, bound in [('detailsBackground', (1024, 576)), ('detailsHeaderIcon', (48, 48))]:
                image = self.item(name)
                self.assertTrue(image.property('asynchronous'))
                self.assertTrue(image.property('cache'))
                self.assertLessEqual(image.property('sourceSize').width(), bound[0])
                self.assertLessEqual(image.property('sourceSize').height(), bound[1])

        self.root.selectGame(first)
        selected_media(first)
        old_row = self.model.rowOf(first)
        self.root.setProperty('descending', True)
        self.settle()
        self.assertNotEqual(self.model.rowOf(first), old_row)
        self.root.setProperty('query', 'Adventure')
        self.root.setView('list')
        self.settle()
        selected_media(first)
        for icon in self.items('compactGameIcon'):
            if icon.property('source').toString():
                self.wait_ready(icon)
                work_id = icon.parentItem().parentItem().property('workId')
                self.assert_source(icon, self.media[work_id]['iconUrl'])
        self.root.selectGame(second)
        selected_media(second)
        self.root.setView('grid')
        self.settle()
        self.assert_source(self.item('detailsBackground'), '')  # Compact overview must not load backdrop.
        self.assertEqual(self.warnings, [])

    def test_grid_caption_bounds_and_lazy_offscreen_sources(self):
        self.create()
        grid = self.item('coverGrid')
        grid_bottom = grid.mapToScene(QPointF(0, grid.height())).y()
        # At the supplied 1280x800 default, all eight fixture captions fit above Zoom.
        for caption in self.items('gridStatusCaption'):
            self.assertLessEqual(caption.y() + caption.height(), caption.parentItem().height())
            self.assertLessEqual(caption.mapToScene(QPointF(0, caption.height())).y(), grid_bottom + 1)
        self.root.resize(900, 560)
        self.settle()
        offscreen = [cover for cover in self.items('gridArtwork') if not cover.property('loadEnabled')]
        self.assertTrue(offscreen, 'Small viewport should have offscreen delegates')
        for cover in offscreen:
            self.assert_source(cover.findChild(QQuickItem, 'coverImage'), '')
        self.evaluate(grid, 'positionViewAtEnd()')
        self.settle()
        last_id = self.model.idAt(self.model.rowCount() - 1)
        last = next(c for c in self.items('gridStatusCaption') if c.parentItem().property('workId') == last_id)
        self.assertLessEqual(last.mapToScene(QPointF(0, last.height())).y(), grid.mapToScene(QPointF(0, grid.height())).y() + 1)
        self.assertEqual(self.warnings, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
