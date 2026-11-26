"""Unit and integration test suite for ControllerFocusNavigation.qml.
Runs in isolated private offscreen Qt session without live devices or background side effects.
"""
import sys, os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP_ROOT)]

from PySide6.QtCore import QUrl, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtTest import QTest

TEST_QML = """
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "." as Native

Rectangle {
    id: testRoot
    width: 800
    height: 600
    color: "#181a1f"

    property var nav: focusNav
    property int buttonAClickCount: 0
    property int buttonBClickCount: 0
    property int checkToggleCount: 0
    property int comboActivated: -1
    property string activeTab: "General"

    Native.ControllerFocusNavigation {
        id: focusNav
        active: true
        onBackRequested: modalOverlay.visible = false
        scope: mainContainer
        modalTrap: modalOverlay
        sectionSwitcher: function(delta) {
            testRoot.activeTab = (delta > 0) ? "Advanced" : "General"
        }
    }

    ColumnLayout {
        id: mainContainer
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        Text { objectName: "labelOnly"; text: "This label must never become a focus target" }
        Button {
            id: btnA
            objectName: "btnA"
            text: "Button A"
            onClicked: testRoot.buttonAClickCount++
        }

        Button {
            id: btnDisabled
            objectName: "btnDisabled"
            text: "Disabled Button"
            enabled: false
        }

        CheckBox {
            id: chkOpt
            objectName: "chkOpt"
            text: "Option Check"
            checked: false
            onToggled: testRoot.checkToggleCount++
        }

        Slider {
            id: sldGain
            objectName: "sldGain"
            from: 0.0
            to: 100.0
            stepSize: 10.0
            value: 50.0
        }

        TextField {
            id: txtInput
            objectName: "txtInput"
            text: "Initial text"
        }

        ComboBox { id: choice; objectName: "choice"; model: ["one", "two", "three"]; onActivated: testRoot.comboActivated = currentIndex }
        Button {
            id: btnB
            objectName: "btnB"
            text: "Button B"
            onClicked: testRoot.buttonBClickCount++
        }
    }

    // Modal Overlay Trap
    Rectangle {
        id: modalOverlay
        objectName: "modalOverlay"
        anchors.fill: parent
        visible: false
        color: "#b0000000"

        Rectangle {
            anchors.centerIn: parent
            width: 300; height: 180
            color: "#222"

            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 10

                TextField {
                    id: modalInput
                    objectName: "modalInput"
                    text: "Modal text"
                }

                Button {
                    id: modalConfirmBtn
                    objectName: "modalConfirmBtn"
                    text: "Confirm Dialog"
                    property int clickCount: 0
                    onClicked: clickCount++
                }
            }
        }
    }
}
"""

def main():
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication([])
    app = _APP
    engine = QQmlApplicationEngine()

    nav_qml_path = Path(__file__).parent / "ControllerFocusNavigation.qml"
    test_qml_path = Path(__file__).parent / "TestFocusHost.qml"
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(x.toString() for x in rows))
    component = QQmlComponent(engine)
    component.setData(TEST_QML.encode(), QUrl.fromLocalFile(str(test_qml_path)))
    if component.isError():
        for err in component.errors():
            print("QML Error:", err.toString(), file=sys.stderr)
        sys.exit(1)

    window = QQuickWindow()
    window.setWidth(800)
    window.setHeight(600)

    root_item = component.create()
    assert root_item is not None
    root_item.setParentItem(window.contentItem())
    window.show()
    QTest.qWait(50)

    nav = root_item.property("nav")
    btn_a = root_item.findChild(QObject, "btnA")
    btn_disabled = root_item.findChild(QObject, "btnDisabled")
    chk_opt = root_item.findChild(QObject, "chkOpt")
    sld_gain = root_item.findChild(QObject, "sldGain")
    txt_input = root_item.findChild(QObject, "txtInput")
    btn_b = root_item.findChild(QObject, "btnB")
    choice = root_item.findChild(QObject, "choice")
    modal_overlay = root_item.findChild(QObject, "modalOverlay")
    modal_input = root_item.findChild(QObject, "modalInput")
    modal_confirm_btn = root_item.findChild(QObject, "modalConfirmBtn")

    # 1. Test focusFirst
    assert nav.focusFirst() == True
    assert nav.property("currentFocusedItem") == btn_a
    print("[PASS] focusFirst correctly focused first interactive control (btnA)")

    # 2. Test directional navigation skips disabled control
    # From btnA, down should skip btnDisabled and jump straight to chkOpt
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == chk_opt, f"Expected chkOpt, got {nav.property('currentFocusedItem')}"
    print("[PASS] Directional down navigation skipped disabled control")

    # 3. Test CheckBox toggle on accept action
    assert chk_opt.property("checked") == False
    assert nav.action("accept") == True
    assert chk_opt.property("checked") == True
    assert root_item.property("checkToggleCount") == 1
    print("[PASS] Accept action toggled CheckBox AND emitted user toggled signal")

    # 4. Test Slider navigation and bounded adjustment
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == sld_gain
    assert sld_gain.property("value") == 50.0

    # Directional left decreases slider value
    assert nav.action("left") == True
    assert abs(sld_gain.property("value") - 40.0) < 0.001
    assert nav.property("currentFocusedItem") == sld_gain, "Slider adjustment must NOT change focused item"

    # Directional right increases slider value
    assert nav.action("right") == True
    assert abs(sld_gain.property("value") - 50.0) < 0.001
    print("[PASS] Directional left/right adjusts slider value boundedly without focus escape")

    # 5. Test TextField focus and accept safety
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == txt_input
    orig_text = txt_input.property("text")
    assert nav.action("accept") == True
    assert txt_input.property("text") == orig_text, "Accept action on text input must NOT wipe or mutate text"
    print("[PASS] TextField focused safely without accidental mutation on accept")

    # 6. Combo index adjustments emit normal activated signal, never backend actions.
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == choice
    assert nav.action("right") == True
    assert choice.property("currentIndex") == 1 and root_item.property("comboActivated") == 1
    nav.action("right"); nav.action("right")
    assert choice.property("currentIndex") == 2
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == btn_b
    assert root_item.property("buttonBClickCount") == 0
    assert nav.action("accept") == True
    assert root_item.property("buttonBClickCount") == 1
    print("[PASS] Button clicked via accept action")

    # 7. Test Section Switching (previous_section / next_section)
    assert root_item.property("activeTab") == "General"
    assert nav.action("next_section") == True
    assert root_item.property("activeTab") == "Advanced"
    assert nav.action("previous_section") == True
    assert root_item.property("activeTab") == "General"
    print("[PASS] Section switcher action (next_section / previous_section) successfully handled")

    # 8. Test Modal Trap Enclosure
    modal_overlay.setProperty("visible", True)
    old_clicks = root_item.property("buttonBClickCount")
    assert not nav.action("accept")  # Focus retained outside a new modal MUST NOT activate.
    assert root_item.property("buttonBClickCount") == old_clicks
    assert not nav.action("next_section")
    assert root_item.property("activeTab") == "General"
    assert nav.focusFirst() == True
    assert nav.property("currentFocusedItem") == modal_input

    # Down in modal trap moves to modalConfirmBtn
    assert nav.action("down") == True
    assert nav.property("currentFocusedItem") == modal_confirm_btn

    # Down from modalConfirmBtn wraps or stays inside modal trap; NEVER escapes to mainContainer
    assert nav.action("down") == True
    focused_in_modal = nav.property("currentFocusedItem")
    assert focused_in_modal in [modal_input, modal_confirm_btn], f"Focus escaped modal trap to {focused_in_modal}"

    # Explicitly focus confirm after wrap: accepting text must never submit.
    nav.setFocusedItem(modal_confirm_btn)
    assert nav.action("accept") == True
    assert modal_confirm_btn.property("clickCount") == 1

    # Back dismisses modal trap
    assert nav.action("back") == True
    assert modal_overlay.property("visible") == False
    print("[PASS] Modal trap strictly enclosed focus navigation and back action dismissed dialog")

    # Disabled/stale and out-of-scope references cannot activate anything.
    nav.setFocusedItem(btn_b)
    btn_b.setProperty("enabled", False)
    count = root_item.property("buttonBClickCount")
    assert not nav.action("accept")
    assert root_item.property("buttonBClickCount") == count
    nav.setProperty("active", False)
    assert nav.property("currentFocusedItem") is None
    assert not nav.focusFirst() and not nav.action("accept")
    assert txt_input.property("text") == orig_text
    assert not warnings, warnings
    root_item.setParentItem(None)
    root_item.deleteLater()
    QTest.qWait(30)
    window.close()
    engine.deleteLater()
    QTest.qWait(30)

    print("\nALL CONTROLLER FOCUS NAVIGATION TESTS PASSED SUCCESSFULLY!")

def test_controller_focus_navigation():
    main()

if __name__ == "__main__":
    main()
