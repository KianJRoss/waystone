"""Render fake-only overlay screenshots at the two required review sizes."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from pathlib import Path
import sys
from PySide6.QtCore import QUrl, QObject, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

APP_DIR = Path(__file__).resolve().parent
REPO = APP_DIR.parents[2]
OUT = REPO / "research" / "w5-v2" / "controller"
sys.path.insert(0, str(APP_DIR))
from test_overlay_controller_studio import FakeControllerService, FakeLive, FakeProfileView, FakeCommands


def capture_size(app, width: int, height: int) -> None:
    target = OUT / f"{width}x{height}"
    target.mkdir(parents=True, exist_ok=True)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(APP_DIR))
    service, live, profiles, commands = FakeControllerService(), FakeLive(), FakeProfileView(), FakeCommands()
    for name, value in (("unifiedController", service), ("controllerLiveView", live),
                        ("controllerProfiles", profiles), ("controllerCommands", commands),
                        ("controllerProfileCommands", commands), ("controllerDeviceCommands", commands)):
        engine.rootContext().setContextProperty(name, value)
    wrapper = b'''import QtQuick\nimport QtQuick.Controls\nimport "."\nItem {\n id: host\n property var profileModel\n property var controllerService\n property var liveModel\n property var commandModel\n Component { id: gamePicker; ControllerProfilePicker { profileView: host.profileModel; gameId: host.profileModel.runtimeGameId; compact: true } }\n OverlayToolController { anchors.fill: parent; controllerProfilePicker: gamePicker; service: host.controllerService; live: host.liveModel; profileView: host.profileModel; commands: host.commandModel; profileCommands: host.commandModel; deviceCommands: host.commandModel }\n}'''
    component = QQmlComponent(engine)
    component.setData(wrapper, QUrl.fromLocalFile(str(APP_DIR / "ControllerOverlayCaptureHost.qml")))
    if component.isError():
        raise RuntimeError("capture host QML failed: " + "; ".join(error.toString() for error in component.errors()))
    host = component.createWithInitialProperties({"profileModel": profiles, "controllerService": service,
                                                 "liveModel": live, "commandModel": commands})
    if host is None:
        raise RuntimeError("capture host create failed: " + "; ".join(error.toString() for error in component.errors()))
    window = QQuickWindow()
    window.setColor(QColor("#101010"))
    window.resize(width, height)
    host.setParentItem(window.contentItem())
    host.setSize(window.size())
    window.show()
    QTest.qWait(180)
    panel = host.findChild(QObject, "overlayToolController")
    studio = host.findChild(QObject, "overlayUnifiedControllerStudio")
    wizard = host.findChild(QObject, "overlayControllerBindWizard")
    if not panel or not studio or not wizard:
        raise RuntimeError("controller panel/studio/wizard not found")

    def save(name: str) -> None:
        QTest.qWait(80)
        if not window.grabWindow().save(str(target / name)):
            raise RuntimeError("could not save screenshot " + name)

    for tab in ("Map", "Sticks", "Profiles"):
        panel.setProperty("binding", False)
        studio.setProperty("activeTab", tab)
        save(tab.lower() + ".png")

    panel.setProperty("binding", True)
    QTest.qWait(60)
    sources_value = wizard.property("sources")
    sources = sources_value.toVariant() if hasattr(sources_value, "toVariant") else sources_value
    for index in range(len(sources)):
        wizard.setProperty("step", index)
        wizard.syncDestination()
        save("bind-step-%02d.png" % (index + 1))
        if index == 0:
            wizard.setProperty("outputCapture", True)
            wizard.forceActiveFocus()
            QTest.keyClick(window, Qt.Key_K)
            save("bind-captured-key.png")
            wizard.next()  # Stage the captured output so review shows the changed row.
    wizard.setProperty("step", len(sources))
    save("bind-review.png")
    window.close()
    host.deleteLater()
    engine.deleteLater()
    QTest.qWait(50)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(["waystone-controller-overlay-capture"])
    for width, height in ((960, 720), (1280, 800)):
        capture_size(app, width, height)
    print("Captured controller studio and bind wizard states at 960x720 and 1280x800 in", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
