"""Sidebar destinations replace the library inside its window, preserving state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from app import create_session

_APP = None
def application():
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication([])
    return _APP


def test_destinations_are_mutually_exclusive_same_window_pages(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    app = application()
    spawns = []
    engine, model, root, warnings = create_session(
        app, headless=True, spawn_fn=lambda *a, **kw: spawns.append(a),
        provider_jobs_enabled=False, native_controller_ui=True)
    try:
        root.setVisible(True)
        QTest.qWait(30)
        library = root.findChild(QQuickItem, 'libraryWorkspace')
        container = root.findChild(QQuickItem, 'workspaceContainer')
        before = root.property('selectedId')
        destinations = [
            ('railSocial', 'friends', 'socialDashboard'),
            ('tabAchievements', 'achievements', 'achievementsDashboard'),
            ('railEmulation', 'emulation', 'emulationDashboard'),
            ('railDownloads', 'downloads', 'downloadsDashboard'),
            ('railSettings', 'settings', 'settingsDashboard'),
            ('settingsLinuxConnections', 'overview', 'menuOverviewDashboard'),
            ('railControllers', 'controllers', 'controllerStudio'),
        ]
        for rail, destination, name in destinations:
            assert QMetaObject.invokeMethod(root.findChild(QObject, rail), 'clicked', Qt.DirectConnection)
            app.processEvents()
            # Controllers is built asynchronously (in steps across frames), so wait for it to appear.
            for _ in range(250):
                if root.findChild(QQuickItem, name) is not None:
                    break
                QTest.qWait(20)
            page = root.findChild(QQuickItem, name)
            assert page is not None, name
            assert root.property('activePage') == destination
            assert not library.isVisible()
            assert page.isVisible()
            # Controller is hosted by a Loader within the same area.
            ancestor = page.parentItem()
            while ancestor is not None and ancestor != container:
                ancestor = ancestor.parentItem()
            assert ancestor == container
            assert page.width() >= container.width() - 1
            assert page.height() >= container.height() - 1
            for _, other, other_name in destinations:
                other_page = root.findChild(QQuickItem, other_name)
                if other != destination and other_page:
                    assert not other_page.isVisible(), other_name
            assert root.property('selectedId') == before
        root.showLibraryView()
        app.processEvents()
        assert library.isVisible() and root.property('activePage') == 'library'
        assert not spawns
        assert not engine.social_view.connected
        assert warnings == []
    finally:
        root.setVisible(False)
        if engine.provider_jobs: engine.provider_jobs.shutdown()
        engine.social_view.shutdown()
        engine.live_updates.stop(); engine.controller_live.shutdown()
        engine.controller_studio.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_back_preserves_library_filters_selection_and_organizer_route(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    app = application()
    engine, model, root, warnings = create_session(app, headless=True, provider_jobs_enabled=False)
    try:
        root.setVisible(True)
        root.setProperty('query', 'game')
        root.setProperty('viewMode', 'list')
        app.processEvents()
        selected = root.property('selectedId')
        root.openFriends(); app.processEvents()
        assert root.property('activePage') == 'friends'
        root.openAchievements(); app.processEvents()
        assert root.property('activePage') == 'achievements'
        root.goBack(); app.processEvents()
        assert root.property('activePage') == 'friends'
        root.goBack(); app.processEvents()
        assert root.property('activePage') == 'library'
        assert root.property('query') == 'game'
        assert root.property('viewMode') == 'list'
        assert root.property('selectedId') == selected
        root.setProperty('organizerMode', True); app.processEvents()
        assert root.property('activePage') == 'organizer'
        root.openFriends(); root.goBack(); app.processEvents()
        assert root.property('activePage') == 'organizer' and root.property('organizerMode')
        assert warnings == []
    finally:
        root.setVisible(False)
        if engine.provider_jobs: engine.provider_jobs.shutdown()
        engine.social_view.shutdown(); engine.live_updates.stop()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_main_overlay_uses_single_surface_pinned_items_and_routes_tools(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    from PySide6.QtQuick import QQuickWindow, QQuickItem
    app = application()
    engine, model, root, warnings = create_session(app, headless=True, provider_jobs_enabled=False)
    try:
        root.setVisible(True)
        root.openFriends()
        root.togglePartyOverlay()
        QTest.qWait(60)
        manager = root.findChild(QObject, 'desktopOverlayManager')
        assert manager.property('visible') and manager.property('editing')
        party = manager.findChild(QQuickItem, 'overlayPartyWindow')
        friends = manager.findChild(QQuickItem, 'overlayFriendsWindow')
        toolbar = manager.findChild(QQuickItem, 'overlayToolbarWindow')
        surface = manager.findChild(QQuickWindow, 'overlaySurfaceWindow')
        assert len(manager.findChildren(QQuickWindow)) == 1
        assert party.window() == friends.window() == toolbar.window() == surface
        assert party is not friends and party.isVisible() and friends.isVisible()
        party.setProperty('pinned', True)
        root.togglePartyOverlay()
        QTest.qWait(50)
        assert not manager.property('visible') and not toolbar.isVisible()
        assert party.isVisible() and not friends.isVisible()
        assert surface.flags() & Qt.WindowStaysOnTopHint
        assert surface.flags() & Qt.WindowDoesNotAcceptFocus
        root.togglePartyOverlay()
        QTest.qWait(40)
        assert toolbar.isVisible()
        assert QMetaObject.invokeMethod(manager.findChild(QObject, 'overlayActionLibrary'), 'clicked', Qt.DirectConnection)
        QTest.qWait(40)
        library_win = manager.findChild(QObject, 'overlayLibraryWindow')
        assert library_win is not None and library_win.property('shown')
        open_full = manager.findChild(QObject, 'overlayLibraryOpenFullButton')
        assert open_full is not None
        assert QMetaObject.invokeMethod(open_full, 'clicked', Qt.DirectConnection)
        QTest.qWait(40)
        assert root.property('activePage') == 'library'
        assert not manager.property('editing') and party.isVisible()
        assert not engine.social_view.connected
        assert warnings == [], warnings
        party.setProperty('shown', False)
    finally:
        root.setVisible(False)
        engine.social_view.shutdown(); engine.live_updates.stop()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
