"""One-window overlay mask adapter; no window creation or compositor policy.

QML owns visibility/geometry and calls updateRegions BEFORE showing the window,
then after widget movement, pin/visibility changes and surface resize. Rectangles
are window-local logical pixels, not desktop coordinates. Only xcb is currently
qualified: its QWindow mask is an X bounding shape, which also restricts input.
This does not establish stacking above fullscreen applications on Hyprland.
"""
from math import ceil, floor, isfinite

from PySide6.QtCore import QObject, Property, QRect, Signal, Slot
from PySide6.QtGui import QGuiApplication, QRegion, QWindow


def widget_region(rects, width, height):
    """Conservative integer union, clipped to the surface; ignore invalid entries."""
    bounds = QRect(0, 0, max(0, int(width)), max(0, int(height)))
    region = QRegion()
    for item in rects:
        try:
            x, y, w, h = (float(item[key]) for key in ('x', 'y', 'width', 'height'))
            if not all(isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
                continue
            if not isfinite(x + w) or not isfinite(y + h):
                continue
            # Clip before constructing QRect (also avoids overflowing C++ ints).
            left, top = max(0, floor(x)), max(0, floor(y))
            right, bottom = min(bounds.width(), ceil(x + w)), min(bounds.height(), ceil(y + h))
            if right > left and bottom > top:
                region |= QRegion(QRect(left, top, right-left, bottom-top))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return region


class OverlaySurfaceHost(QObject):
    """Narrow QML bridge. Unsupported surfaces fail closed rather than trap clicks.

    updateRegions returns whether QML may show the window. It never shows it or
    steals focus. Callers must AND visible with supported/the returned readiness.
    An empty pinned union hides the window: setMask(QRegion()) CLEARS a mask and
    would otherwise expose the whole window to input. Offscreen tests may omit
    the host to render QML; offscreen itself is not qualified for production.
    """
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._platform = QGuiApplication.platformName().lower()
        self._status = ('xcb-mask-candidate; fullscreen stacking unverified' if self.supported
                        else f'unsupported overlay input mask platform: {self._platform or "none"}')

    @Property(bool, constant=True)
    def supported(self):
        return self._platform == 'xcb'

    @Property(str, notify=changed)
    def status(self):
        return self._status

    def _set_status(self, value):
        if self._status != value:
            self._status = value
            self.changed.emit()

    @Slot(QObject, 'QVariantList', bool, result=bool)
    def updateRegions(self, window, rects, editing):
        if not isinstance(window, QWindow):
            self._set_status('overlay surface is not a QWindow')
            return False
        if not self.supported:
            window.hide()
            self._set_status(f'unsupported overlay input mask platform: {self._platform or "none"}')
            return False
        # Edit mode deliberately intercepts the dimmed backdrop. In pinned mode
        # mask exactly the visible widget union (include any desired shadow inset).
        region = (QRegion(QRect(0, 0, window.width(), window.height())) if editing
                  else widget_region(rects, window.width(), window.height()))
        if region.isEmpty():
            window.hide()
            self._set_status('overlay hidden: empty input region')
            return False
        # Qt retains the mask property across native-handle recreation. Avoid
        # redundant X shape requests on every edit-mode drag/resize tick.
        if window.mask() != region:
            window.setMask(region)  # Real API; Qt scales logical -> native px.
        self._set_status('xcb mask applied; fullscreen stacking unverified')
        return True
