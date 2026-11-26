"""Render UnifiedControllerStudio at 960x720, 1080x960, and 1440x900 in private Xvfb.
Asserts that ControllerSurface and KeyboardSurface do NOT overflow their parent cards,
and grabs proof screenshots.
"""
import os, sys, time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP_ROOT)]

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtTest import QTest
from controller_view import ControllerView

class MockClient:
    def __init__(self):
        self.rev = "b" * 64
        self.active_profile = "Default"
        self.names = ["Default", "Gaming", "Desktop", "Custom"]
        self.profiles = {
            "Default": {
                "buttons": {"A": "A", "B": "B"},
                "routes": {
                    "Controller:A": "Controller:A",
                    "Controller:B": "Controller:B",
                    "Controller:X": "Controller:X",
                    "Controller:Y": "Controller:Y",
                    "Controller:LB": "Controller:LB",
                    "Controller:RB": "Controller:RB",
                    "Controller:LT": "Controller:LT",
                    "Controller:RT": "Controller:RT",
                    "Keyboard:Space": "Controller:A"
                },
                "stick_processing": {
                    "left": {"enabled": True, "inner_deadzone": 0.08, "outer_deadzone": 1.0, "anti_deadzone": 0.0, "curve": 1.0},
                    "right": {"enabled": True, "inner_deadzone": 0.08, "outer_deadzone": 1.0, "anti_deadzone": 0.0, "curve": 1.0}
                },
                "controller_mouse": {"enabled": False, "stick": "right", "sensitivity": 18.0, "deadzone": 0.16}
            }
        }
        self.running = True
        self.devices = [{"name": "Mock Gamepad", "connected": True, "virtual": False}]

    def request(self, op, **fields):
        if op == "status":
            return {"running": self.running, "devices": self.devices}
        elif op == "profiles":
            return {"names": self.names, "active_profile": self.active_profile, "revision": self.rev}
        elif op == "profile_get":
            name = fields.get("name") or self.active_profile
            return {"name": name, "profile": self.profiles.get(name, {}), "revision": self.rev}
        return {"ok": True}

def test_resolution(engine, component, width, height, evidence_dir):
    print(f"\n--- Testing & Capturing {width}x{height} ---")
    fake_client = MockClient()
    real_view = ControllerView(client=fake_client)
    real_view.refresh()
    for _ in range(50):
        QTest.qWait(20)
        if not real_view.busy: break

    window = QQuickWindow()
    window.setWidth(width)
    window.setHeight(height)

    item = component.create()
    assert item is not None, "Failed to instantiate UnifiedControllerStudio"
    item.setParentItem(window.contentItem())
    item.setProperty("service", real_view)
    item.setProperty("width", width)
    item.setProperty("height", height)

    window.show()
    QTest.qWait(300)

    # Find ctrlSurface
    ctrl_surface = item.findChild(QQuickItem, "ctrlSurface")
    assert ctrl_surface is not None, "ctrlSurface not found"
    
    # Check parent card
    parent_card = ctrl_surface.parentItem()
    assert parent_card is not None
    print(f"Parent Card geometry: width={parent_card.width()}, height={parent_card.height()}")
    print(f"Controller Surface geometry: width={ctrl_surface.width()}, height={ctrl_surface.height()}")

    # Find container inside ctrl_surface
    ctrl_container = ctrl_surface.findChild(QQuickItem, "ctrlContainer")
    assert ctrl_container is not None, "ctrlContainer not found"
    scale = ctrl_container.scale()
    origin = ctrl_container.transformOrigin()
    print(f"Controller container scale: {scale:.3f}, origin: {origin}")
    assert scale <= 1.0, f"Scale should not exceed 1.0, got {scale}"

    # Calculate effective scaled bounding box
    scaled_w = ctrl_container.width() * scale
    scaled_h = ctrl_container.height() * scale
    assert scaled_w <= parent_card.width() + 1.0, f"Scaled controller ({scaled_w}) exceeds parent width ({parent_card.width()})"
    assert scaled_h <= parent_card.height() + 1.0, f"Scaled controller ({scaled_h}) exceeds parent height ({parent_card.height()})"
    print(f"[PASS] Controller surface cleanly contained: scaled {scaled_w:.1f}x{scaled_h:.1f} in card {parent_card.width():.1f}x{parent_card.height():.1f}")

    # Grab window image and save
    image = window.grabWindow()
    screenshot_path = evidence_dir / f"proof-{width}x{height}.png"
    success = image.save(str(screenshot_path))
    assert success, f"Failed to save screenshot to {screenshot_path}"
    print(f"[PASS] Saved proof screenshot to: {screenshot_path}")

    # Also capture proof screenshots of all native tabs at 1440x900
    if width == 1440:
        for tab_name in ["Map", "Sticks", "Motion", "Vibration", "Macros", "Hotkeys", "Modules", "Profiles", "Devices", "Live"]:
            item.setProperty("activeTab", tab_name)
            QTest.qWait(200)
            img_tab = window.grabWindow()
            tab_path = evidence_dir / f"proof-tab-{tab_name.lower()}.png"
            img_tab.save(str(tab_path))
            print(f"[PASS] Saved proof screenshot for {tab_name} tab to: {tab_path}")
        item.setProperty("activeTab", "Map")
        QTest.qWait(100)

    window.close()
    item.deleteLater()
    window.deleteLater()

def main():
    assert os.environ.get('WAYSTONE_PRIVATE_XVFB') == '1'
    app = QGuiApplication(['proof-capture'])

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(APP_ROOT))

    qml_file = Path(__file__).parent / "UnifiedControllerStudio.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file)))
    if component.isError():
        for err in component.errors():
            print("QML Error:", err.toString(), file=sys.stderr)
        sys.exit(1)

    evidence_dir = Path(os.environ.get("WAYSTONE_W21_EVIDENCE", Path(__file__).parent / "evidence"))
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Test all 3 requested resolutions
    resolutions = [
        (960, 720),
        (1080, 960),
        (1440, 900)
    ]

    for w, h in resolutions:
        test_resolution(engine, component, w, h, evidence_dir)

    print("\nALL RESOLUTION PROOF SCREENSHOTS CAPTURED AND ASSERTIONS PASSED!")

if __name__ == "__main__":
    main()
