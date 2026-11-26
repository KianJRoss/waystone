"""Fixture-only checks; private X server qualification lives under research/w5-v2."""
import pytest
from PySide6.QtCore import QObject, QPoint, QRect
from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtQuick import QQuickWindow
from overlay_surface import OverlaySurfaceHost, widget_region


@pytest.fixture(scope='module')
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def test_union_clipped_rounded_and_local():
    region = widget_region([dict(x=-5.4,y=2.2,width=20,height=9.2),
                            dict(x=60,y=30,width=80,height=100)], 100, 80)
    assert region == QRegion(QRect(0,2,15,10)) | QRegion(QRect(60,30,40,50))
    assert not region.contains(QPoint(40,40))


@pytest.mark.parametrize('rect', [{}, None, dict(x=0,y=0,width=0,height=5),
    dict(x=0,y=0,width=-1,height=5), dict(x=float('nan'),y=0,width=1,height=1),
    dict(x=0,y=0,width=float('inf'),height=1),dict(x=1e300,y=0,width=2,height=2)])
def test_bad_rectangles_do_not_create_shield(rect):
    assert widget_region([rect], 100, 100).isEmpty()


class RecordingWindow(QQuickWindow):
    def __init__(self):
        super().__init__()
        self.applied = []
        self.resize(200,100)
    def setMask(self, region):
        self.applied.append(QRegion(region))
        super().setMask(region)


def test_real_qwindow_mask_and_edit_policy(app):
    host = OverlaySurfaceHost()
    host._platform = 'xcb'  # Unit policy branch only, NOT platform qualification.
    window = RecordingWindow()
    try:
        rects = [dict(x=10,y=20,width=30,height=40)]
        assert host.updateRegions(window, rects, False)
        assert window.mask() == QRegion(QRect(10,20,30,40))
        assert window.applied[-1] == window.mask()
        assert host.updateRegions(window, rects, False)
        assert len(window.applied) == 1  # Unchanged regions avoid native requests.
        assert host.updateRegions(window, [], True)
        assert window.mask() == QRegion(QRect(0,0,200,100))
        assert host.updateRegions(window, rects, True)
        assert len(window.applied) == 2
        window.setVisible(True)
        assert not host.updateRegions(window, [], False)
        assert not window.isVisible()
        assert len(window.applied) == 2  # Never clear mask with empty QRegion.
    finally:
        window.close()


def test_unsupported_fails_closed_and_reports_reason(app):
    host = OverlaySurfaceHost(); host._platform = 'wayland'
    window = RecordingWindow()
    try:
        window.setVisible(True)
        assert not host.supported
        assert not host.updateRegions(window, [dict(x=0,y=0,width=200,height=100)], True)
        assert not window.isVisible() and not window.applied
        assert 'unsupported' in host.status and 'wayland' in host.status
        assert not host.updateRegions(QObject(), [], False)
        assert 'not a QWindow' in host.status
    finally:
        window.close()
