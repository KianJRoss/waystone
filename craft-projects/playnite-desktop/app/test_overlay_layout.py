"""W5 fixture-only geometry/controller/persistence regressions, offscreen."""
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, QRectF, QUrl, Qt, QPoint, QMetaObject
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_desktop_overlay import (qapp, FakeSocialView, FakeVoiceView,
    create_engine_with_context, load_desktop_overlay)

NAMES = ['overlayToolbarWindow','overlayPartyWindow','overlayFriendsWindow','overlayToolsWindow']


def create(app, *, location=None, area=QRectF(0, 0, 1200, 900)):
    social = FakeSocialView(parent=app); voice = FakeVoiceView(parent=app)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    manager.setProperty('boundsOverride', area)
    if location:
        manager.setProperty('layoutLocation', QUrl.fromLocalFile(str(location)))
        manager.setProperty('persistenceEnabled', True)
    QTest.qWait(30)
    windows = [manager.findChild(QQuickItem, name) for name in NAMES]
    return engine, manager, windows, social, voice


def destroy(engine, manager, windows):
    manager.closeOverlay()
    for window in windows: window.setProperty('shown', False)
    manager.deleteLater(); engine.deleteLater(); QTest.qWait(30)


def rect(window): return (window.x(), window.y(), window.width(), window.height())


def inside(window, area):
    x,y,w,h = rect(window)
    assert w > 0 and h > 0
    assert area.x() <= x <= x+w <= area.x()+area.width()
    assert area.y() <= y <= y+h <= area.y()+area.height()


def test_visible_handles_and_controller_arrange_move_resize_cycle_done(qapp):
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.open(); QTest.qWait(20)
        for window in windows:
            for name in ['widgetDragHandle','widgetResizeHandle','widgetMoveButton','widgetSizeButton']:
                assert window.findChild(QObject, name) is not None
        # Reach Arrange from the toolbar with controller commands, not direct helper calls.
        assert manager.navigate('down') is True
        assert manager.property('toolbarIndex') == 3
        assert manager.navigate('accept') is True
        assert manager.property('layoutMode') is True and manager.property('navigationWidget') == 1
        party = windows[1]; before = rect(party); other = rect(windows[2])
        assert manager.navigate('right') is True
        assert party.x() == before[0]+16
        assert manager.navigate('accept') is True  # A changes move -> resize
        assert manager.property('layoutAction') == 'resize'
        assert manager.navigate('down') is True
        assert party.height() == before[3]+16
        assert rect(windows[2]) == other
        assert manager.navigate('next_section') is True
        assert manager.property('navigationWidget') == 2
        assert manager.navigate('back') is True
        assert not manager.property('layoutMode') and manager.property('editing')
        assert manager.navigate('back') is True and not manager.property('editing')
        assert not social.calls and not voice.calls and engine._warnings == []
    finally: destroy(engine, manager, windows)


def test_move_size_buttons_select_their_own_widget(qapp):
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.open(); QTest.qWait(20)
        QMetaObject.invokeMethod(windows[2].findChild(QObject,'widgetSizeButton'), 'click')
        assert manager.property('navigationWidget') == 2 and manager.property('layoutAction') == 'resize'
        QMetaObject.invokeMethod(windows[3].findChild(QObject,'widgetMoveButton'), 'click')
        assert manager.property('navigationWidget') == 3 and manager.property('layoutAction') == 'move'
        assert engine._warnings == []
    finally: destroy(engine, manager, windows)


@pytest.mark.parametrize('area', [QRectF(0,0,320,240),QRectF(0,0,1280,720),QRectF(-1920,-300,1920,1080)])
def test_clamp_snap_and_resolution_reflow(qapp, area):
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.open(); QTest.qWait(20)
        for window in windows:
            window.setProperty('x', 5000); window.setProperty('y', -5000)
            window.setProperty('width', 960); window.setProperty('height', 720)
        manager.setProperty('boundsOverride', area); QTest.qWait(30)
        for window in windows:
            inside(window, QRectF(0, 0, area.width(), area.height()))
            # A negative-origin native screen still has zero-origin item coordinates.
            window.setProperty('x', 7); window.setProperty('y', 9)
            window.commitGeometry()
            assert window.x() == 0 and window.y() == 0
            inside(window, QRectF(0, 0, area.width(), area.height()))
        assert engine._warnings == []
    finally: destroy(engine, manager, windows)


def test_per_widget_geometry_roundtrip_reset_and_no_other_changes(qapp, tmp_path):
    file = tmp_path/'layout.ini'
    engine, manager, windows, social, voice = create(qapp, location=file)
    saved = None
    try:
        manager.open(); QTest.qWait(20)
        party, friends = windows[1:3]
        other = rect(friends)
        party.setProperty('x', 96); party.setProperty('y', 240)
        party.setProperty('width', 432); party.setProperty('height', 464)
        party.commitGeometry(); saved = rect(party)
        assert rect(friends) == other and file.exists()
        assert engine._warnings == []
    finally: destroy(engine, manager, windows)
    engine, manager, windows, social, voice = create(qapp, location=file)
    try:
        assert rect(windows[1]) == saved
        # Existing close-overlay behavior snaps geometry too; other widget remains separate.
        assert rect(windows[2]) != saved
        manager.resetLayout(); QTest.qWait(20)
        defaults = rect(windows[1])
        assert defaults != saved
        assert not social.calls and not voice.calls and engine._warnings == []
    finally: destroy(engine, manager, windows)
    engine, manager, windows, social, voice = create(qapp, location=file)
    try:
        assert rect(windows[1]) == defaults
    finally: destroy(engine, manager, windows)


def test_saved_large_screen_layout_recovers_on_smaller_screen(qapp, tmp_path):
    file = tmp_path/'layout.ini'
    engine, manager, windows, social, voice = create(qapp, location=file, area=QRectF(-1600,0,1600,1200))
    try:
        w=windows[1]; w.setProperty('x', 1190); w.setProperty('y', 750)
        w.setProperty('width', 400); w.setProperty('height', 400); w.commitGeometry()
    finally: destroy(engine, manager, windows)
    area = QRectF(0,0,640,480)
    engine, manager, windows, social, voice = create(qapp, location=file, area=area)
    try:
        for w in windows: inside(w, area)
        assert engine._warnings == []
    finally: destroy(engine, manager, windows)


def test_no_persistence_in_standalone_fixture_by_default(qapp, tmp_path):
    location = tmp_path/'not-created.ini'
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.setProperty('layoutLocation', QUrl.fromLocalFile(str(location)))
        windows[1].setProperty('x', 64); windows[1].commitGeometry()
        assert not manager.property('persistenceEnabled') and not location.exists()
        assert all(w.property('settingsObject') is None for w in windows)
    finally: destroy(engine, manager, windows)


def test_persisted_geometry_is_read_by_a_fresh_process(qapp, tmp_path):
    import json, os, subprocess, sys
    file = tmp_path/'fresh-process.ini'
    engine, manager, windows, social, voice = create(qapp, location=file)
    try:
        w = windows[2]; w.setProperty('x', 112); w.setProperty('y', 256)
        w.setProperty('width', 320); w.setProperty('height', 288); w.commitGeometry()
        expected = rect(w)
    finally: destroy(engine, manager, windows)
    child = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from PySide6.QtGui import QGuiApplication
from test_overlay_layout import create,destroy,rect
app=QGuiApplication(['offscreen-overlay-layout-fixture'])
engine,manager,windows,social,voice=create(app,location=Path(sys.argv[2]))
try:
    assert not social.calls and not voice.calls and not engine._warnings
    print(json.dumps(rect(windows[2])))
finally: destroy(engine,manager,windows)
'''
    env = {**os.environ, 'QT_QPA_PLATFORM':'offscreen', 'QT_QUICK_BACKEND':'software',
           'XDG_CONFIG_HOME':str(tmp_path/'child-config')}
    result = subprocess.run([sys.executable,'-c',child,str(Path(__file__).parent),str(file)],
                            env=env, capture_output=True, text=True, timeout=15, check=True)
    assert tuple(json.loads(result.stdout)) == expected


def test_tiny_tools_widget_scrolls_controller_selection_into_view(qapp):
    engine, manager, windows, social, voice = create(qapp, area=QRectF(0,0,320,240))
    try:
        manager.open(); manager.selectWidget(3)
        QTest.qWait(20)
        for _ in range(3): assert manager.navigate('down') is True
        QTest.qWait(20)
        scroll = windows[3].findChild(QObject,'overlayToolsScroll')
        button = windows[3].findChild(QObject,'overlayActionSettings')
        assert scroll.property('contentY') > 0
        assert button.property('y') + button.property('height') <= scroll.property('contentY') + scroll.property('height') + 1
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine, manager, windows)


def test_hiding_selected_widget_does_not_leave_controller_on_hidden_content(qapp):
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.open(); manager.selectWidget(1)
        windows[1].setProperty('shown', False)
        assert manager.navigate('right') is True
        assert manager.property('navigationWidget') == 0
    finally: destroy(engine, manager, windows)


def test_main_controller_action_signal_routes_into_arrange_mode(qapp):
    from unittest.mock import patch
    from app import create_session
    from test_game_install_controller import close_session
    # Exercise the actual Main.qml connection without any controller socket poll.
    with patch('controller_live_view.ControllerLiveView._poll'):
        engine, model, root, warnings = create_session(qapp, headless=True)
        try:
            root.togglePartyOverlay(); QTest.qWait(20)
            manager = root.findChild(QObject, 'desktopOverlayManager')
            assert manager is not None and not manager.property('persistenceEnabled')
            engine.controller_navigation.setContext('overlay')
            # With no game running the quick library has focus (Xbox guide); go to the toolbar first.
            assert manager.property('navigationWidget') == 4
            manager.selectWidget(0)
            engine.controller_navigation.action.emit('down')
            engine.controller_navigation.action.emit('accept')
            assert manager.property('layoutMode') and manager.property('navigationWidget') == 1
            party = manager.findChild(QQuickItem, 'overlayPartyWindow')
            x = party.x()
            engine.controller_navigation.action.emit('right')
            assert party.x() == x + 16
            engine.controller_navigation.action.emit('back')
            assert not manager.property('layoutMode') and manager.property('editing')
            manager.closeOverlay()
            for name in NAMES: manager.findChild(QQuickItem, name).setProperty('shown', False)
            assert warnings == []
        finally:
            close_session(qapp, engine, root)
