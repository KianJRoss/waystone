"""Single-surface contracts: local gestures, pinned regions and v1 migration."""
from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QUrl, Qt
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from test_desktop_overlay import qapp, APP_DIR
from test_overlay_layout import create, destroy, rect


def regions(manager):
    value = manager.property('inputRegions')
    return value.toVariant() if hasattr(value, 'toVariant') else value


def region(item):
    return dict(zip(('x','y','width','height'), rect(item)))


def test_pinned_mode_draws_only_pinned_items_and_updates_local_input_regions(qapp):
    engine, manager, items, social, voice = create(qapp, area=QRectF(-1920,-300,1200,900))
    try:
        surface = manager.findChild(QQuickWindow, 'overlaySurfaceWindow')
        manager.open(); QTest.qWait(30)
        assert regions(manager) == [region(item) for item in items]
        assert surface.isVisible() and manager.property('surfaceNeeded')
        assert len(manager.findChildren(QQuickWindow)) == 1
        for item in items:
            assert item.property('layoutBounds') == QRectF(0,0,1200,900)
            assert item.window() == surface and item.parentItem() == surface.contentItem()
        party, friends = items[1:3]
        party.setProperty('pinned', True); friends.setProperty('pinned', True)
        manager.closeOverlay(); QTest.qWait(30)
        assert [item.isVisible() for item in items] == [False,True,True,False]
        assert regions(manager) == [region(party), region(friends)]
        assert not party.findChild(QObject,'widgetDragHandle').property('enabled')
        assert not party.findChild(QObject,'widgetResizeHandle').property('enabled')
        image = surface.grabWindow()
        assert not image.isNull() and image.pixelColor(1190,890).alpha() == 0
        assert 0 < image.pixelColor(int(party.x()+10),int(party.y()+party.height()-16)).alpha() < 255
        party.setProperty('x',96); QTest.qWait(20)
        assert regions(manager)[0]['x'] == 96
        friends.setProperty('shown',False); QTest.qWait(20)
        assert regions(manager) == [region(party)]
        party.setProperty('pinned',False); QTest.qWait(20)
        assert regions(manager) == [] and not surface.isVisible()
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine, manager, items)


def test_header_drag_is_monotonic_in_parent_space_without_moving_native_surface(qapp):
    engine, manager, items, social, voice = create(qapp, area=QRectF(-1920,-300,1200,900))
    try:
        manager.open(); QTest.qWait(30)
        item = items[1]; surface = item.window(); initial_surface = (surface.x(),surface.y(),surface.width(),surface.height())
        initial = rect(item); start = item.mapToScene(QPointF(30,15)).toPoint()
        QTest.mousePress(surface,Qt.LeftButton,Qt.NoModifier,start)
        assert manager.property('navigationWidget') == 1
        assert item.z() > items[2].z()
        positions=[]
        for dx in (5,10,20,35,55,80):
            QTest.mouseMove(surface,start+QPoint(dx,dx//2),delay=5)
            positions.append((item.x(),item.y()))
            assert positions[-1] == (initial[0]+dx,initial[1]+dx//2)
            assert (surface.x(),surface.y(),surface.width(),surface.height()) == initial_surface
            assert rect(items[2]) != rect(item)
        QTest.mouseRelease(surface,Qt.LeftButton,Qt.NoModifier,start+QPoint(80,40))
        assert positions == sorted(positions)
        assert (item.width(),item.height()) == initial[2:]
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine, manager, items)


def test_v1_global_geometry_negative_screen_origin_migrates_to_local_items(qapp,tmp_path):
    location = tmp_path/'legacy-v1.ini'
    engine, manager, items, social, voice = create(qapp)
    # Write the actual legacy QML Settings format, not a made-up INI encoding.
    component = QQmlComponent(engine,QUrl.fromLocalFile(str(APP_DIR/'OverlayWidgetWindow.qml')))
    legacy = component.createWithInitialProperties({
        'widgetId':'party','persistenceEnabled':True,'layoutLocation':QUrl.fromLocalFile(str(location)),
        'boundsOverride':QRectF(-1920,-300,1920,1080),
        'initialGeometry':QRectF(-1408,-172,384,320),'shown':False})
    assert legacy is not None, component.errors()
    try:
        QTest.qWait(30); legacy.commitGeometry(); legacy.saveGeometry()
        assert rect(legacy) == (-1408,-172,384,320)
        assert location.exists()
    finally:
        legacy.deleteLater(); QTest.qWait(20); destroy(engine,manager,items)
    engine, manager, items, social, voice = create(qapp,location=location,area=QRectF(-1920,-300,1920,1080))
    try:
        assert rect(items[1]) == (512,128,384,320)
        assert items[1].property('layoutBounds') == QRectF(0,0,1920,1080)
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine,manager,items)
