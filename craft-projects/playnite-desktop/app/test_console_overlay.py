"""Real QML navigation and UI-only activation; never invokes a live launcher."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PySide6.QtCore import QObject,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from ui_control import UiControl

def test_console_navigation_preserves_identity_and_overlay_is_lazy():
    app=QGuiApplication.instance() or QGuiApplication([])
    engine,model,root,warnings=create_session(app,headless=True)
    try:
        root.show();QTest.qWait(40)
        voice=engine.voice_media
        assert not voice.active and not engine.social_view.connected
        overlay=root.findChild(QObject,'desktopOverlayManager')
        assert not overlay.property('visible')
        control=UiControl(root)
        before=root.property('selectedId')
        control.ShowConsole();QTest.qWait(40)
        assert root.property('consoleMode')
        grid=root.findChild(QObject,'consoleGrid');assert grid.property('width')>=190 and grid.property('height')>200 and grid.property('count')==model.visibleCount
        console=root.findChild(QObject,'consoleLibrary');assert console.property('visible')
        console.forceActiveFocus();QTest.keyClick(root,Qt.Key_Right);QTest.qWait(30)
        assert root.property('selectedId')!=before
        assert model.rowOf(root.property('selectedId'))>=0
        assert not voice.active and not engine.social_view.connected
        play=root.findChild(QObject,'consolePlayButton');assert not play.property('enabled')
        control.ToggleOverlay();QTest.qWait(30)
        assert overlay.property('visible') and not voice.active
        control.ToggleOverlay();assert not overlay.property('visible')
        root.requestActivate();console.forceActiveFocus();QTest.qWait(30)
        QTest.keyClick(root,Qt.Key_Escape);QTest.qWait(30)
        assert not root.property('consoleMode')
        control.ShowConsole();QTest.qWait(20);assert root.property('consoleMode')
        control.ShowLibrary();QTest.qWait(20);assert not root.property('consoleMode')
        assert not warnings
    finally:
        root.close();engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)
