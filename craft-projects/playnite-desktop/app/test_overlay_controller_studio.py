"""Fake-only tests for the in-game Unified Controller studio and bind wizard."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, Qt, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

APP_DIR = Path(__file__).resolve().parent
from test_desktop_overlay import qapp


class FakeControllerService(QObject):
    changed = Signal()
    operationFinished = Signal(bool)

    def __init__(self):
        super().__init__()
        self._profile = {"routes": {"Controller:A": "Controller:A"}, "opaque": {"keep": True}}
        self._busy = False
        self.saves = []
        self.loads = []

    @Property(bool, notify=changed)
    def available(self): return True

    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Property(bool, notify=changed)
    def uncertain(self): return False

    @Property(str, notify=changed)
    def profileJson(self): return json.dumps(self._profile)

    @Property("QStringList", notify=changed)
    def profiles(self): return ["Game", "Default"]

    @Property(str, notify=changed)
    def editingProfile(self): return "Game"

    @Property(str, notify=changed)
    def activeProfile(self): return "Game"

    @Property(str, notify=changed)
    def status(self): return "Controller connected"

    @Slot()
    def refresh(self): self.changed.emit()

    @Slot()
    def refreshRoutes(self): self.changed.emit()

    @Slot(str)
    def loadProfile(self, name): self.loads.append(name)

    @Slot(str)
    def saveProfile(self, raw):
        payload = json.loads(raw)
        self.saves.append(payload)
        self._profile = payload
        self.changed.emit()
        self.operationFinished.emit(True)


class FakeLive(QObject):
    changed = Signal()

    def __init__(self, available=True):
        super().__init__()
        self._available = available
        self._frame = {
            "input": {"buttons": 0, "left_trigger": 0, "right_trigger": 0},
            "output": {"buttons": 0, "left_trigger": 0, "right_trigger": 0},
        }

    @Property(bool, notify=changed)
    def available(self): return self._available

    @Property("QVariantMap", notify=changed)
    def frame(self): return self._frame

    def set_buttons(self, value):
        self._frame["input"]["buttons"] = value
        self.changed.emit()


class FakeProfileView(QObject):
    changed = Signal()

    @Property(bool, notify=changed)
    def available(self): return True

    @Property(bool, notify=changed)
    def engineMutationBlocked(self): return False

    @Property(str, notify=changed)
    def defaultProfile(self): return "Default"

    @Property(str, notify=changed)
    def runtimeGameId(self): return "game-1"

    @Property("QVariantMap", notify=changed)
    def gameProfiles(self): return {"game-1": "Game"}

    @Property("QStringList", notify=changed)
    def profiles(self): return ["Game", "Default"]

    @Property(str, notify=changed)
    def error(self): return ""

    @Property(str, notify=changed)
    def runtimeStatus(self): return "Profile assigned for this game"

    @Slot(str)
    def setDefaultProfile(self, name):
        self._default = name
        self.changed.emit()


class FakeCommands(QObject):
    changed = Signal()
    operationFinished = Signal(str, bool)

    @Property("QStringList", notify=changed)
    def capabilities(self): return []

    @Property("QStringList", notify=changed)
    def profiles(self): return ["Game", "Default"]

    @Property(str, notify=changed)
    def activeProfile(self): return "Game"

    @Property(str, notify=changed)
    def status(self): return "Ready"

    @Property(bool, notify=changed)
    def available(self): return True

    @Property(bool, notify=changed)
    def deadbandAvailable(self): return False

    @Slot()
    def refreshCapabilities(self): pass


class FakeStudio(QObject):
    selectedSourceChanged = Signal()

    def __init__(self, live):
        super().__init__()
        self._selected = ""
        self.capturePhysicalSelection = False
        self.previousPhysicalPressed = {}
        self.live = live

    @Property(str, notify=selectedSourceChanged)
    def selectedSource(self): return self._selected

    @selectedSource.setter
    def selectedSource(self, value):
        self._selected = value
        self.selectedSourceChanged.emit()


def _load(engine, filename, initial=None):
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(APP_DIR / filename)))
    assert not component.isError(), [e.toString() for e in component.errors()]
    item = component.createWithInitialProperties(initial or {})
    assert item is not None, [e.toString() for e in component.errors()]
    if not hasattr(engine, "_components"): engine._components = []
    engine._components.append(component)
    return item


def test_overlay_studio_reuses_native_map_and_physical_press_selects(qapp):
    engine = QQmlApplicationEngine()
    service = FakeControllerService()
    live = FakeLive()
    item = _load(engine, "controller_native/UnifiedControllerStudio.qml", {"service": service, "live": live, "compact": True})
    window = QQuickWindow()
    window.resize(920, 600)
    item.setParentItem(window.contentItem())
    item.setSize(window.size())
    window.show()
    QTest.qWait(80)
    try:
        assert item.property("compact") is True
        assert item.findChild(QObject, "ctrlSurface") is not None
        assert item.findChild(QObject, "controllerStudioMore") is not None
        assert item.property("capturePhysicalSelection") is False
        if __import__("os").environ.get("WAYSTONE_CAPTURE_W21") == "1":
            evidence = APP_DIR.parents[2] / "research" / "w21"
            evidence.mkdir(parents=True, exist_ok=True)
            item.setProperty("activeTab", "Sticks")
            QTest.qWait(100)
            assert window.grabWindow().save(str(evidence / "overlay.png"))
        item.setProperty("capturePhysicalSelection", True)
        live.set_buttons(0x1000)  # fake physical A edge; no hardware access
        QTest.qWait(90)
        assert item.property("selectedSource") == "Controller:A"
        assert item.property("capturePhysicalSelection") is False
    finally:
        window.close()
        item.deleteLater()
        engine.deleteLater()


def test_overlay_stick_edits_require_discard_confirmation_on_back(qapp):
    engine = QQmlApplicationEngine()
    service = FakeControllerService()
    live = FakeLive(available=False)
    profiles = FakeProfileView()
    overlay = _load(engine, "OverlayToolController.qml", {
        "service": service, "live": live, "profileView": profiles,
        "visible": True,
    })
    window = QQuickWindow()
    window.resize(900, 600)
    overlay.setParentItem(window.contentItem())
    overlay.setSize(window.size())
    window.show()
    QTest.qWait(80)
    try:
        studio = overlay.findChild(QObject, "overlayUnifiedControllerStudio")
        assert studio is not None
        studio.setProperty("dirty", True)
        overlay.requestBack()
        confirm = overlay.findChild(QObject, "overlayDiscardSticksConfirm")
        assert confirm is not None and confirm.property("visible")
        assert overlay.property("visible")
        confirm.reject()
        assert overlay.property("visible")
        overlay.requestBack()
        confirm.accept()
        QTest.qWait(20)
    finally:
        window.close()
        overlay.deleteLater()
        engine.deleteLater()


def test_bind_wizard_stages_controls_then_saves_one_complete_map(qapp):
    engine = QQmlApplicationEngine()
    service = FakeControllerService()
    live = FakeLive(available=False)
    studio = FakeStudio(live)
    wizard = _load(engine, "OverlayControllerBindWizard.qml", {"service": service, "studio": studio})
    window = QQuickWindow()
    window.resize(800, 560)
    wizard.setParentItem(window.contentItem())
    wizard.setSize(window.size())
    window.show()
    QTest.qWait(60)
    try:
        assert wizard.property("step") == 0
        assert service.saves == []  # no partial profile writes
        assert wizard.findChild(QObject, "bindWizardControllerDiagram") is not None
        assert wizard.property("currentSource") == "Controller:A"
        assert wizard.property("previousBinding") == "Controller:A"
        assert wizard.findChild(QObject, "bindWizardCaptureOutput") is not None
        wizard.setProperty("outputCapture", True)
        wizard.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_K)
        QTest.qWait(20)
        assert wizard.property("capturedOutput") == "Desktop:Key:K"
        assert wizard.property("destination") == "Desktop:Key:K"
        assert wizard.property("outputCapture") is False
        wizard.setProperty("outputCapture", True)
        wizard.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Escape)
        assert wizard.property("capturedOutput") == "Desktop:Key:K"  # Escape cancels.
        assert wizard.property("outputCapture") is False
        area = wizard.findChild(QObject, "bindWizardMouseCapture")
        wizard.setProperty("outputCapture", True)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                         area.mapToScene(QPointF(area.width() / 2, area.height() / 2)).toPoint())
        assert wizard.property("capturedOutput") == "Desktop:Mouse:Left"
        assert wizard.property("outputCapture") is False
        wizard.setProperty("capturedOutput", "")
        wizard.setProperty("destination", "Desktop:Key:Space")
        wizard.next()
        assert wizard.property("step") == 1
        assert service.saves == []
        count = wizard.property("sources").toVariant() if hasattr(wizard.property("sources"), "toVariant") else wizard.property("sources")
        for _ in range(len(count) - 1):
            wizard.next()
        assert wizard.property("step") == len(count)
        assert service.saves == []
        changed = wizard.property("changedSources")
        changed = changed.toVariant() if hasattr(changed, "toVariant") else changed
        assert "Controller:A" in changed
        wizard.applyAll()
        assert len(service.saves) == 1
        assert service.saves[0]["routes"]["Controller:A"] == "Desktop:Key:Space"
        assert service.saves[0]["opaque"] == {"keep": True}
    finally:
        window.close()
        wizard.deleteLater()
        engine.deleteLater()


def test_full_controller_panel_uses_game_profile_and_exposes_general_default(qapp):
    engine = QQmlApplicationEngine()
    service = FakeControllerService()
    live = FakeLive()
    profiles = FakeProfileView()
    commands = FakeCommands()
    for key, value in (("unifiedController", service), ("controllerLiveView", live), ("controllerProfiles", profiles),
                       ("controllerCommands", commands), ("controllerProfileCommands", commands),
                       ("controllerDeviceCommands", commands)):
        engine.rootContext().setContextProperty(key, value)
    panel = _load(engine, "OverlayToolController.qml", {"service": service, "live": live, "profileView": profiles})
    window = QQuickWindow()
    window.resize(960, 720)
    panel.setParentItem(window.contentItem())
    panel.setSize(window.size())
    window.show()
    QTest.qWait(100)
    try:
        assert panel.findChild(QObject, "overlayUnifiedControllerStudio") is not None
        assert panel.findChild(QObject, "overlayControllerBackBtn").property("text") == "Tools"
        for name, label in (("overlayControllerBindAll", "Bind controller"),
                            ("overlayControllerUndo", "Undo"),
                            ("overlayControllerRevert", "Revert session"),
                            ("overlayControllerSetGeneral", "Set as general"),
                            ("overlayControllerOpenFull", "Full page")):
            button = panel.findChild(QObject, name)
            assert button is not None and button.property("text") == label
            assert button.width() >= button.property("implicitWidth") - 1
        panel.saveAsGeneral()
        assert profiles._default == "Default" or profiles._default == "Game"
        panel.setProperty("binding", True)
        QTest.qWait(30)
        wizard = panel.findChild(QObject, "overlayControllerBindWizard")
        assert wizard is not None and wizard.property("visible") is True
        assert not panel.findChild(QObject, "overlayControllerBackBtn").isVisible()
        assert panel.findChild(QObject, "overlayControllerCloseBinding").property("text") == "Cancel binding"
    finally:
        window.close()
        panel.deleteLater()
        engine.deleteLater()
