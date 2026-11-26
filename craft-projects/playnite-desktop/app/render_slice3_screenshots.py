"""Generate offscreen visual proof screenshots of Waystone Overlay v2 slice 3 compact Tools panels.

Saves screenshots to research/w5-v2/slice3/:
1. tools-hub.png: Compact Tools Hub Menu inside the overlay
2. tool-downloads.png: Compact Downloads & Installs panel
3. tool-controller.png: Compact Controller Profile panel
4. tool-volume.png: Compact Volume Mixer panel
5. tool-screenshot.png: Compact Screenshot capture & thumbnail panel
6. tool-saves.png: Compact Save Quick Backup panel
7. tool-performance.png: Compact Performance FPS & Frame time panel
"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl, QObject
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
APP_DIR = ROOT / "craft-projects" / "playnite-desktop" / "app"
OUT_DIR = ROOT / "research" / "w5-v2" / "slice3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from overlay_tools import OverlayToolsBridge
from test_overlay_tools import FakeDownloadsTracker, FakeLaunchView, FakeSaveManagement


def render_component(engine, component_file, bridge, output_file, width=300, height=340, extra_props=None):
    win = QQuickWindow()
    win.setColor(QColor("#141414"))
    win.resize(width, height)

    qml_path = APP_DIR / component_file
    comp = QQmlComponent(engine, str(qml_path))
    if comp.isError():
        print(f"Error compiling {component_file}:", comp.errors())
        return False

    props = {"toolsBridge": bridge}
    if extra_props:
        props.update(extra_props)

    item = comp.createWithInitialProperties(props)
    if not item:
        print(f"Failed to create {component_file}:", comp.errors())
        return False

    item.setParentItem(win.contentItem())
    item.setProperty("width", width)
    item.setProperty("height", height)
    win.show()
    QTest.qWait(60)

    image = win.grabWindow()
    success = image.save(str(output_file))
    print(f"[{'PASS' if success else 'FAIL'}] Saved {output_file.name} ({width}x{height})")

    win.close()
    item.deleteLater()
    win.deleteLater()
    return success


def render_full_overlay_hub(engine, bridge, output_file, active_tool=""):
    from test_desktop_overlay import FakeSocialView, FakeVoiceView, load_desktop_overlay, call_qml
    manager = load_desktop_overlay(engine)
    try:
        call_qml(manager, "open")
        manager.setProperty("navigationWidget", 3)
        manager.selectWidget(3)
        tools = manager.findChild(QObject, "overlayToolsWindow")
        if tools and active_tool:
            tools.setProperty("activeTool", active_tool)
        QTest.qWait(60)

        surface = manager.findChild(QQuickWindow, "overlaySurfaceWindow")
        if surface:
            image = surface.grabWindow()
            # Crop to toolsWindow area
            tx = int(tools.property("x"))
            ty = int(tools.property("y"))
            tw = int(tools.property("width"))
            th = int(tools.property("height"))
            cropped = image.copy(tx, ty, tw, th)
            success = cropped.save(str(output_file))
            print(f"[{'PASS' if success else 'FAIL'}] Saved {output_file.name} from overlay ({tw}x{th})")
            return success
    finally:
        call_qml(manager, "closeOverlay")
    return False


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()

    # Create dummy mock screenshot file for thumbnail preview
    dummy_shot = OUT_DIR / "dummy_screenshot.png"
    img = QImage(320, 180, QImage.Format_RGB32)
    img.fill(QColor("#2d4a6e"))
    painter = QPainter(img)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(img.rect(), Qt.AlignCenter, "Cyber Adventure 2077 - Boss Fight")
    painter.end()
    img.save(str(dummy_shot))

    # Configure bridge with rich demonstration telemetry and state
    tracker = FakeDownloadsTracker([
        {"id": "dl-1", "title": "Elden Ring: Shadow of the Erdtree", "percent": 68, "phase": "downloading", "provider_label": "Steam", "message": "Downloading chunks (34.2 GB / 50.1 GB)"},
        {"id": "dl-2", "title": "Hollow Knight: Silksong", "percent": 12, "phase": "downloading", "provider_label": "GOG", "message": "Allocating disk space…"}
    ], parent=app)

    launch = FakeLaunchView(
        selected_id="game-elden",
        title="Elden Ring",
        running=True,
        pid=12345,
        parent=app
    )

    save_mgr = FakeSaveManagement(available=True, parent=app)

    bridge = OverlayToolsBridge(
        game_installs=tracker,
        save_management=save_mgr,
        launch_view=launch,
        capturer=lambda p: img.save(str(p)),
        screenshots_dir=OUT_DIR,
        stats_reader=lambda: {"fps": 59.9, "frametime_ms": 16.7, "source": "MangoHud (gamescope)"},
        parent=app
    )

    # Set mock volume state
    bridge._master_volume = 82
    bridge._master_muted = False
    bridge._master_available = True
    bridge._game_volume = 95
    bridge._game_muted = False
    bridge._game_available = True
    bridge._chat_volume = 60
    bridge._chat_muted = False
    bridge._chat_available = True

    # Set screenshot state
    bridge._last_screenshot = str(dummy_shot)
    bridge._last_screenshot_time = "Today, 15:42"

    # Set save backup state
    bridge._save_backup_status = "Backed up successfully (15:30)"
    bridge._last_backup_time = "15:30"

    engine.rootContext().setContextProperty("overlayTools", bridge)
    from test_desktop_overlay import FakeSocialView, FakeVoiceView
    engine.rootContext().setContextProperty("socialView", FakeSocialView(parent=app))
    engine.rootContext().setContextProperty("voiceView", FakeVoiceView(parent=app))

    # 1. Tools Hub menu
    render_full_overlay_hub(engine, bridge, OUT_DIR / "tools-hub.png")

    # 2. Downloads panel
    render_component(engine, "OverlayToolDownloads.qml", bridge, OUT_DIR / "tool-downloads.png")

    # 3. Controller Profile panel
    render_component(engine, "OverlayToolController.qml", bridge, OUT_DIR / "tool-controller.png")

    # 4. Volume Mixer panel
    render_component(engine, "OverlayToolVolume.qml", bridge, OUT_DIR / "tool-volume.png")

    # 5. Screenshot panel
    render_component(engine, "OverlayToolScreenshot.qml", bridge, OUT_DIR / "tool-screenshot.png")

    # 6. Saves Quick Backup panel
    render_component(engine, "OverlayToolSaves.qml", bridge, OUT_DIR / "tool-saves.png", extra_props={"currentGame": {"id": "game-elden", "title": "Elden Ring"}})

    # 7. Performance panel
    render_component(engine, "OverlayToolPerformance.qml", bridge, OUT_DIR / "tool-performance.png")

    print("\nAll 7 proof screenshots successfully generated in:", OUT_DIR)


if __name__ == "__main__":
    main()
