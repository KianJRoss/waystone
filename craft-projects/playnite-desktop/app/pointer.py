"""The real global pointer position for QML. Dragging a window by mapping the mouse through the window's own
(still-moving) position makes it rubber-band; the cursor position from the windowing system does not move with it."""
from PySide6.QtCore import QObject, QPointF, Slot
from PySide6.QtGui import QCursor


class Pointer(QObject):
    @Slot(result=QPointF)
    def position(self) -> QPointF:
        return QPointF(QCursor.pos())
