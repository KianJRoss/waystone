"""Real Qt Quick controls, exercised only with the offscreen software backend."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'
# Offscreen platform otherwise defaults ItemDelegate hover effects off.
os.environ['QT_QUICK_CONTROLS_HOVER_ENABLED'] = '1'
import unittest
from PySide6.QtCore import QCoreApplication, QEvent, QIODevice, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply
from PySide6.QtQml import QQmlEngine, QQmlExpression, QQmlNetworkAccessManagerFactory
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from app import create_session
from ui_checks import exercise_ui
from pathlib import Path

HERE = Path(__file__).parent


class DeniedReply(QNetworkReply):
    """Never open a socket, even if a tooltip regression requests a URL."""
    def __init__(self, request, parent):
        super().__init__(parent)
        self.setUrl(request.url())
        self.setError(QNetworkReply.ContentAccessDenied, 'Network disabled by offline UI test')
        self.open(QIODevice.ReadOnly)
        QTimer.singleShot(0, self.finish)

    def finish(self):
        self.setFinished(True)
        self.finished.emit()

    def abort(self):
        pass

    def readData(self, maximum):
        return b''


class DenyNetworkFactory(QQmlNetworkAccessManagerFactory):
    def __init__(self):
        super().__init__()
        self.attempts = []
        self.managers = []

    def create(self, parent):
        attempts = self.attempts

        class Manager(QNetworkAccessManager):
            def createRequest(self, operation, request, outgoing=None):
                attempts.append(request.url().toString())
                return DeniedReply(request, self)

        manager = Manager(parent)
        self.managers.append(manager)
        return manager


class QtDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QGuiApplication.instance() or QGuiApplication(['offline-ui-tests'])

    def create(self, snapshots=None):
        self.engine, self.model, self.root, self.warnings = create_session(self.application, snapshots, headless=True)
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.root.setVisible(True)
        QTest.qWait(100)
        self.application.processEvents()

    def tearDown(self):
        if hasattr(self, 'engine'):
            self.root.setVisible(False)
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.application.processEvents()

    def item(self, name):
        result = self.root.findChild(QQuickItem, name)
        self.assertIsNotNone(result, name)
        return result

    def click(self, name):
        obj = self.item(name)
        p = obj.mapToScene(QPointF(obj.width()/2, obj.height()/2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, p)
        self.settle()

    def settle(self):
        self.application.processEvents()
        QTest.qWait(50)

    def test_fixture_interactions(self):
        self.create()
        report = exercise_ui(self.application, self.root, self.model, self.warnings)
        self.assertTrue(all(s['passed'] for s in report['steps']), report)

    def test_explicit_snapshot_interactions(self):
        self.create([HERE / 'fixtures/demo-snapshot.json'])
        report = exercise_ui(self.application, self.root, self.model, self.warnings)
        self.assertTrue(all(s['passed'] for s in report['steps']), report)

    def test_store_search_replaces_library_in_main_workspace(self):
        self.create()
        self.click('railStore')
        self.assertEqual(self.root.property('activePage'),'store')
        self.assertFalse(self.item('libraryWorkspace').isVisible())
        self.assertTrue(self.item('storefrontWorkspaceLoader').isVisible())
        self.assertTrue(self.item('storefrontWorkspacePage').isVisible())
        self.assertIsNotNone(self.item('storeGameSearch'))
        self.assertIsNotNone(self.item('storeMarketSelector'))
        self.assertIsNotNone(self.item('storeVoucherToggle'))
        public_url=self.item('storePublicOfferUrl')
        public_check=self.item('storePublicPageCheck')
        self.assertIsNotNone(self.item('storeScraperSettings'))
        price_view=self.engine.rootContext().contextProperty('priceComparison')
        price_view._hosts=[]
        price_view.changed.emit()
        self.settle()
        self.assertFalse(public_check.isEnabled())
        price_view._hosts=['retailer.example']
        price_view.changed.emit()
        public_url.setProperty('text','https://retailer.example/game')
        self.settle()
        self.assertTrue(public_check.isEnabled())
        scroll=self.item('storeResultsScrollView')
        price_view._candidates=[{'id':f'candidate-{i}','title':f'Long store search candidate {i}'} for i in range(40)]
        price_view.changed.emit()
        self.settle()
        self.assertGreater(scroll.property('contentHeight'),scroll.height())
        self.item('storeGameSearch').setProperty('text','Requested game')
        price_view._scraped_title='Different game edition'
        price_view.changed.emit()
        self.settle()
        self.assertTrue(self.item('storeScrapedTitleMismatch').isVisible())
        self.assertEqual(self.warnings,[])


    def test_store_accounts_link_opens_platform_connections_in_same_workspace(self):
        self.create()
        self.click('railStore')
        self.click('storeAccountsLink')
        # Sign-ins have one home: Settings > Accounts (Social no longer has a platforms tab).
        self.assertEqual(self.root.property('activePage'),'settings')
        self.assertTrue(self.item('settingsDashboard').isVisible())
        self.assertEqual(self.item('settingsDashboard').property('selectedCategory'),0)
        self.assertTrue(self.item('steamQrSignIn').isVisible())
        self.assertFalse(self.item('libraryWorkspace').isVisible())
        self.assertEqual(self.warnings,[])


    def test_grid_selection_zoom_and_details_resize(self):
        self.create()
        grid = self.item('coverGrid')
        point = grid.mapToScene(QPointF(grid.property('cellWidth') + 30, 30)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.settle()
        self.assertEqual(self.root.property('selectedId'), self.model.idAt(1))
        QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.settle()
        self.assertEqual(self.root.property('viewMode'), 'details')
        compact = self.item('compactGameList')
        old_width = compact.width()
        edge = compact.mapToScene(QPointF(old_width + 2, compact.height()/2)).toPoint()
        QTest.mousePress(self.root, Qt.LeftButton, Qt.NoModifier, edge)
        QTest.mouseMove(self.root, edge + QPointF(42, 0).toPoint(), 60)
        QTest.mouseRelease(self.root, Qt.LeftButton, Qt.NoModifier, edge + QPointF(42, 0).toPoint())
        self.settle()
        self.assertGreater(compact.width(), old_width + 20)
        # The provenance control is below the fold; scroll to it before simulating a real click.
        bar = self.item('detailsDazeScrollBar')
        bar.setProperty('position', 1.0 - bar.property('size'))  # the last position that still shows the end
        self.settle()
        self.click('evidenceToggle')
        self.assertTrue(self.item('provenanceText').isVisible())
        self.assertIn(self.root.property('selectedId'), self.item('provenanceText').property('text'))
        self.root.setView('grid')
        self.settle()
        old_cell = grid.property('cellWidth')
        slider = self.item('coverZoom')
        p = slider.mapToScene(QPointF(slider.width() - 10, slider.height()/2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, p)
        self.settle()
        self.assertGreater(grid.property('cellWidth'), old_cell)
        self.assertEqual(self.warnings, [])

    def test_minimum_width_panels_and_column_resize(self):
        self.create()
        self.root.resize(900, 560)
        self.root.setProperty('filterVisible', True)
        self.root.setView('details')
        self.settle()
        for name in ['searchField', 'explorerPanel', 'gameDetails']:
            obj = self.item(name)
            p = obj.mapToScene(QPointF(obj.width(), 0))
            self.assertLessEqual(p.x(), self.root.width() + 1, name)
        self.root.setView('list')
        self.root.setProperty('inspectorVisible', False)
        self.settle()
        table = self.item('columnView')
        old_width = self.root.property('columnWidths').toVariant()[0]
        edge = table.mapToScene(QPointF(old_width - 2, 16)).toPoint()
        QTest.mousePress(self.root, Qt.LeftButton, Qt.NoModifier, edge)
        QTest.mouseMove(self.root, edge + QPointF(40, 0).toPoint(), 60)
        QTest.mouseRelease(self.root, Qt.LeftButton, Qt.NoModifier, edge + QPointF(40, 0).toPoint())
        self.settle()
        self.assertGreater(self.root.property('columnWidths').toVariant()[0], old_width + 20)
        self.assertEqual(self.warnings, [])

    def test_filter_controls_and_explorer_preserve_same_variant_rule(self):
        self.create()
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, False)
        self.root.requestActivate()
        self.root.setProperty('filterVisible', True)
        self.settle()
        # One Filters panel (former Explorer): pick the Steam library facet like a user would.
        self.item('explorerField').setProperty('currentIndex', 0)
        self.root.setProperty('source', 'Steam')
        self.settle()
        self.assertEqual(self.root.property('source'), 'Steam')
        self.assertEqual(self.model.rowCount(), 1)
        self.root.setProperty('platform', 'Windows')
        self.settle()
        self.assertEqual(self.model.rowCount(), 0)  # Heroic's platform cannot match Steam.
        self.root.clearFilters()
        self.settle()
        self.assertEqual(self.model.rowCount(), 8)
        self.assertEqual(self.warnings, [])

    def test_ctrl_g_toggles_filters_without_changing_view_selection_or_query(self):
        self.create()
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, False)
        self.root.requestActivate()
        for view in ['grid', 'details', 'list']:
            self.root.setView(view)
            self.root.setProperty('query', 'Adventure')
            self.root.setProperty('filterVisible', True)
            self.item('searchField').forceActiveFocus()
            self.settle()
            selected = self.root.property('selectedId')
            for expected in [False, True]:
                QTest.keyClick(self.root, Qt.Key_G, Qt.ControlModifier)
                self.settle()
                self.assertEqual(self.root.property('filterVisible'), expected, view)
                self.assertEqual(self.root.property('viewMode'), view)
                self.assertEqual(self.root.property('selectedId'), selected)
                self.assertEqual(self.root.property('query'), 'Adventure')
        self.assertNotIn('Ctrl+G', self.item('gridViewButton').property('hint'))
        self.assertIn('Ctrl+G', self.item('filterToggle').property('hint'))
        self.assertEqual(self.warnings, [])

    def test_real_hover_tooltips_are_literal_plain_text_for_every_surface(self):
        self.create()
        # Enable real interactive tooltip paths while keeping offscreen/software Qt.
        self.engine.rootContext().setContextProperty('headlessCheck', False)
        self.network_guard = DenyNetworkFactory()
        self.engine.setNetworkAccessManagerFactory(self.network_guard)
        payload = ('<b>UNTRUSTED</b><img src="file:///tmp/craft-tooltip-missing-image.png">'
                   '<img src="http://127.0.0.1:1/craft-tooltip-forbidden">')
        for game in self.model.games:
            game['title'] = payload
        self.root.clearFilters()
        self.engine.rootContext().setContextProperty('sourceOptions', ['All sources', payload])
        self.root.setFlag(Qt.WindowDoesNotAcceptFocus, False)
        self.root.requestActivate()
        self.settle()

        def hover_and_check(surface, position, tip_name):
            QTest.mouseMove(self.root, QPointF(5, 790).toPoint())
            self.settle()
            QTest.mouseMove(self.root, surface.mapToScene(position).toPoint())
            QTest.qWait(750)
            self.application.processEvents()
            # List/Grid delegates belong to the visual tree, not necessarily
            # the window's QObject ownership tree. Inspect actual delegate tips.
            candidates = list(self.root.findChildren(QObject, tip_name))

            def visit(item):
                candidates.extend(child for child in item.children()
                                  if child.objectName() == tip_name)
                for child in item.childItems():
                    visit(child)

            visit(self.root.contentItem())
            tips = [tip for tip in set(candidates) if tip.property('visible')]
            self.assertTrue(tips, tip_name + ' must actually be shown by hover')
            for tip in tips:
                label = tip.property('contentItem')
                expression = QQmlExpression(QQmlEngine.contextForObject(label), label,
                                           'textFormat === Text.PlainText')
                self.assertEqual(expression.evaluate(), (True, False), tip_name)
                self.assertFalse(expression.hasError(), tip_name)
                self.assertIn(payload, label.property('text'))
            self.assertEqual(self.network_guard.attempts, [])
            self.assertEqual(self.warnings, [])

        self.root.setView('grid')
        self.settle()
        hover_and_check(self.item('coverGrid'), QPointF(60, 80), 'gridToolTip')
        self.root.setView('details')
        self.settle()
        hover_and_check(self.item('compactGameList'), QPointF(60, 15), 'detailsToolTip')
        self.root.setView('list')
        self.settle()
        hover_and_check(self.item('columnView'), QPointF(60, 46), 'listToolTip')
        hover_and_check(self.item('explorerItems'), QPointF(60, 45), 'explorerToolTip')
        button = self.item('filterToggle')
        button.setProperty('hint', payload)
        hover_and_check(button, QPointF(20, 15), 'compactButtonToolTip')


    def test_details_play_button_single_and_multi_platform_selection(self):
        self.create()
        self.root.setView('details')
        self.settle()
        play_btn = self.item('detailsPlayButton')
        self.assertIsNotNone(play_btn)
        dialog = self.root.findChild(QObject, 'platformLaunchDialog')
        self.assertIsNotNone(dialog)
        self.assertFalse(dialog.property('visible'))

        play_btn.clicked.emit()
        self.settle()
        self.assertFalse(dialog.property('visible'))

        fake_options = [
            {'variant_key': 'k1', 'product_id': 'p1', 'provider': 'Steam', 'platform': 'PC', 'title': 'Multi Game', 'state': 'installed', 'displayLabel': 'Steam (PC)', 'targetId': 'k1', 'rawRecord': {'variant_key': 'k1', 'product_id': 'p1', 'provider': 'Steam', 'platform': 'PC', 'title': 'Multi Game', 'state': 'installed'}},
            {'variant_key': 'k2', 'product_id': 'p2', 'provider': 'Emulation', 'platform': 'GameCube', 'title': 'Multi Game', 'state': 'installed', 'displayLabel': 'Emulation (GameCube)', 'targetId': 'k2', 'rawRecord': {'variant_key': 'k2', 'product_id': 'p2', 'provider': 'Emulation', 'platform': 'GameCube', 'title': 'Multi Game', 'state': 'installed'}}
        ]
        dialog.setProperty('launchOptions', fake_options)
        dialog.open()
        self.settle()
        self.assertTrue(dialog.property('visible'))
        dialog.close()
        self.settle()
        self.assertFalse(dialog.property('visible'))
        self.assertEqual(self.warnings, [])

    def test_assistant_animation_controls_exist_in_settings_general(self):
        self.create()
        self.root.setProperty('activePage', 'settings')
        dashboard = self.item('settingsDashboard')
        dashboard.setProperty('selectedCategory', 4)  # General
        self.settle()
        card = self.item('settingsAssistantAnimationCard')
        self.assertTrue(card.isVisible())
        toggle = self.item('settingsAssistantAnimationEnabled')
        pace = self.item('settingsAssistantAnimationPace')
        self.assertTrue(toggle.isEnabled())
        self.assertTrue(toggle.property('checked'))
        self.assertAlmostEqual(pace.property('value'), 1.0)
        preferences = self.engine.rootContext().contextProperty('assistantPreferences')
        self.assertTrue(preferences.showAnimation)
        self.assertEqual(preferences.speed, 'Normal')
        self.assertEqual(self.warnings, [])

    def test_no_attached_autoformat_tooltips_or_grid_ctrl_g_claim_remain(self):
        for path in HERE.glob('*.qml'):
            source = path.read_text()
            self.assertNotIn('ToolTip.text:', source, path.name)
            self.assertNotIn('ToolTip.visible:', source, path.name)
        main = (HERE / 'Main.qml').read_text()
        self.assertNotIn('Grid view    Ctrl+G', main)
        self.assertNotIn('Grid view · Ctrl+G', main)


if __name__ == '__main__':
    unittest.main(verbosity=2)
