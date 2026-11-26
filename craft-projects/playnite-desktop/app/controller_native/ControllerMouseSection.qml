import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Sticks tab: steer the desktop mouse with a stick (saved in the profile). The one home for controller-mouse
// settings; it used to be split between Sticks and Profiles.
ProfileSection {
    id: root
    objectName: "controllerMouseSection"
    title: "Controller mouse"
    saveObjectName: "saveControllerMouseButton"
    property bool mouseEnabled: false
    property bool mouseRequiresMod: true
    property string mouseToggleSource: "Controller:Right Stick"
    property string mouseStick: "right"
    property real mouseSensitivity: 18.0
    property real mouseDeadzone: 0.16
    property string mouseLeftClick: "Controller:RB"
    property string mouseRightClick: "Controller:LB"
    property string mouseScrollUp: "Controller:D-pad Up"
    property string mouseScrollDown: "Controller:D-pad Down"

    function finite(v) { return typeof v === "number" && isFinite(v) }

    onLoaded: function(data) {
        var cm = data.controller_mouse || {}
        root.mouseEnabled = cm.enabled === true
        root.mouseRequiresMod = cm.requires_mod !== false
        root.mouseToggleSource = cm.toggle_source !== undefined ? String(cm.toggle_source) : "Controller:Right Stick"
        root.mouseStick = cm.stick === "left" ? "left" : "right"
        root.mouseSensitivity = finite(cm.sensitivity) ? cm.sensitivity : 18.0
        root.mouseDeadzone = finite(cm.deadzone) ? cm.deadzone : 0.16
        root.mouseLeftClick = cm.left_click_source !== undefined ? String(cm.left_click_source) : "Controller:RB"
        root.mouseRightClick = cm.right_click_source !== undefined ? String(cm.right_click_source) : "Controller:LB"
        root.mouseScrollUp = cm.scroll_up_source !== undefined ? String(cm.scroll_up_source) : "Controller:D-pad Up"
        root.mouseScrollDown = cm.scroll_down_source !== undefined ? String(cm.scroll_down_source) : "Controller:D-pad Down"
    }
    collect: function() {
        if (!finite(root.mouseSensitivity) || !finite(root.mouseDeadzone)) {
            root.validationError = "Controller mouse speed and deadzone must be finite numbers."
            return null
        }
        if (root.mouseSensitivity < 1.0 || root.mouseSensitivity > 60.0) {
            root.validationError = "Mouse speed must be between 1 and 60."
            return null
        }
        if (root.mouseDeadzone < 0.0 || root.mouseDeadzone > 0.8) {
            root.validationError = "Mouse deadzone must be between 0% and 80%."
            return null
        }
        // keep fields this card does not show (the engine replaces controller_mouse as a whole)
        var current = {}
        try { current = (JSON.parse(root.service.profileJson) || {}).controller_mouse || {} } catch (e) { current = {} }
        var cm = {}
        for (var key in current) cm[key] = current[key]
        cm.enabled = root.mouseEnabled
        cm.requires_mod = root.mouseRequiresMod
        cm.toggle_source = root.mouseToggleSource
        cm.stick = root.mouseStick
        cm.sensitivity = root.mouseSensitivity
        cm.deadzone = root.mouseDeadzone
        cm.left_click_source = root.mouseLeftClick
        cm.right_click_source = root.mouseRightClick
        cm.scroll_up_source = root.mouseScrollUp
        cm.scroll_down_source = root.mouseScrollDown
        return { controller_mouse: cm }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 16
        ThemeCheckBox {
            objectName: "mouseEnabledCheck"
            text: "Enable controller mouse"
            checked: root.mouseEnabled
            onToggled: { root.mouseEnabled = checked; root.markDirty() }
        }
        ThemeCheckBox {
            text: "Needs the Mod key"
            checked: root.mouseRequiresMod
            onToggled: { root.mouseRequiresMod = checked; root.markDirty() }
        }
        Item { Layout.fillWidth: true }
    }
    Text {
        Layout.fillWidth: true
        text: "Default: Share + R3 turns mouse mode on/off; the right stick steers, RB clicks, LB right-clicks, D-pad up/down scrolls."
        color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; wrapMode: Text.WordWrap
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 14
        ColumnLayout {
            Layout.preferredWidth: 220
            Text { text: "Toggle button:"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
            TextField {
                Layout.fillWidth: true
                text: root.mouseToggleSource
                color: Theme.text; font.family: Theme.family
                background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                onTextEdited: { root.mouseToggleSource = text; root.markDirty() }
            }
        }
        ColumnLayout {
            Layout.preferredWidth: 140
            Text { text: "Pointer stick:"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
            CompactCombo {
                objectName: "mouseStickCombo"
                Layout.fillWidth: true
                model: ["right", "left"]
                currentIndex: Math.max(0, model.indexOf(root.mouseStick))
                onActivated: { root.mouseStick = currentText; root.markDirty() }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Text { text: "Speed (1–60): " + Math.round(root.mouseSensitivity); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
            Slider {
                Layout.fillWidth: true
                from: 1; to: 60; stepSize: 1; value: root.mouseSensitivity
                onMoved: { root.mouseSensitivity = value; root.markDirty() }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Text { text: "Deadzone: " + Math.round(root.mouseDeadzone * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
            Slider {
                Layout.fillWidth: true
                from: 0.0; to: 0.8; stepSize: 0.01; value: root.mouseDeadzone
                onMoved: { root.mouseDeadzone = value; root.markDirty() }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Repeater {
            model: [
                { label: "Left click:", key: "mouseLeftClick" },
                { label: "Right click:", key: "mouseRightClick" },
                { label: "Scroll up:", key: "mouseScrollUp" },
                { label: "Scroll down:", key: "mouseScrollDown" }
            ]
            delegate: ColumnLayout {
                Layout.fillWidth: true
                Text { text: modelData.label; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                TextField {
                    Layout.fillWidth: true
                    text: root[modelData.key]
                    color: Theme.text; font.family: Theme.family
                    background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                    onTextEdited: { root[modelData.key] = text; root.markDirty() }
                }
            }
        }
    }
}
