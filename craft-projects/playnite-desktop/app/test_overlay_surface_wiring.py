"""Actual QML host admission, fake bridge only; no native desktop connection."""
from PySide6.QtCore import QObject, Property, Slot, QRectF
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from test_desktop_overlay import qapp, FakeSocialView, FakeVoiceView, create_engine_with_context, load_desktop_overlay


class FakeHost(QObject):
    def __init__(self, supported=True):
        super().__init__(); self._supported=supported; self.calls=[]
    @Property(bool, constant=True)
    def supported(self): return self._supported
    @Property(str, constant=True)
    def status(self): return 'Synthetic mask admission only'
    @Slot(QObject,'QVariantList',bool,result=bool)
    def updateRegions(self,window,rects,editing):
        self.calls.append((editing,window.isVisible(),list(rects)))
        return self._supported and (editing or bool(rects))


def fixture(qapp,supported=True):
    social=FakeSocialView(parent=qapp);voice=FakeVoiceView(parent=qapp)
    engine=create_engine_with_context(social,voice);host=FakeHost(supported)
    engine.rootContext().setContextProperty('overlaySurfaceHost',host)
    manager=load_desktop_overlay(engine);manager.setProperty('boundsOverride',QRectF(0,0,1200,900))
    QTest.qWait(20)
    return engine,host,manager,social,voice


def clean(engine,manager):
    manager.closeOverlay()
    for name in ('overlayToolbarWindow','overlayPartyWindow','overlayFriendsWindow','overlayToolsWindow'):
        manager.findChild(QObject,name).setProperty('pinned',False)
    manager.deleteLater();engine.deleteLater();QTest.qWait(20)


def test_actual_qml_masks_before_show_and_tracks_pinned_item_union(qapp):
    engine,host,manager,social,voice=fixture(qapp)
    try:
        surface=manager.findChild(QQuickWindow,'overlaySurfaceWindow')
        assert not surface.isVisible()
        manager.open();QTest.qWait(30)
        assert host.calls and next(c for c in host.calls if c[0])[1] is False
        assert surface.isVisible() and manager.property('active')
        party=manager.findChild(QObject,'overlayPartyWindow');party.setProperty('pinned',True)
        manager.closeOverlay();QTest.qWait(30)
        assert surface.isVisible() and not manager.property('active')
        editing,_,rects=host.calls[-1]
        assert not editing and len(rects)==1 and rects[0]['x']==party.property('x')
        party.setProperty('pinned',False);QTest.qWait(30)
        assert not surface.isVisible() and not manager.property('surfaceReady')
        assert not social.calls and not voice.calls and not engine._warnings
    finally:clean(engine,manager)


def test_unsupported_host_never_shows_or_routes_controller_to_invisible_surface(qapp):
    engine,host,manager,social,voice=fixture(qapp,False)
    try:
        manager.open();QTest.qWait(30)
        surface=manager.findChild(QQuickWindow,'overlaySurfaceWindow')
        assert manager.property('editing') and host.calls
        assert not surface.isVisible() and not manager.property('active')
        assert not manager.property('surfaceReady')
        assert not social.calls and not voice.calls and not engine._warnings
    finally:clean(engine,manager)
