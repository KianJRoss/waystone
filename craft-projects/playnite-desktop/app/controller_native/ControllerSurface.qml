import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    implicitWidth: 520
    implicitHeight: 360
    clip: true

    property var service: null
    property var live: null
    property string liveChannel: "input" // "input" or "output"
    property string hoveredCode: ""
    property string selectedSource: ""
    property string selectedDestination: ""
    property var routes: ({})
    property bool showMotionAxes: false

    signal controlClicked(string code, string kind)

    readonly property var currentFrame: Boolean(root.live && root.live.available && root.live.frame && root.live.frame[root.liveChannel]) ? root.live.frame[root.liveChannel] : null

    // Asymmetric stick normalization (-32768 / +32767)
    readonly property real leftNormX: currentFrame ? ((currentFrame.left_x < 0) ? (currentFrame.left_x / 32768.0) : (currentFrame.left_x / 32767.0)) : 0.0
    readonly property real leftNormY: currentFrame ? ((currentFrame.left_y < 0) ? (currentFrame.left_y / 32768.0) : (currentFrame.left_y / 32767.0)) : 0.0
    readonly property real rightNormX: currentFrame ? ((currentFrame.right_x < 0) ? (currentFrame.right_x / 32768.0) : (currentFrame.right_x / 32767.0)) : 0.0
    readonly property real rightNormY: currentFrame ? ((currentFrame.right_y < 0) ? (currentFrame.right_y / 32768.0) : (currentFrame.right_y / 32767.0)) : 0.0

    function isControlPressed(code) {
        if (code === "Controller:Share") {
            // Surface Share lights from real extended_buttons, no invented bitmask/capture alias;
            // distinct physical input feedback preserved even on mapped-output channel
            if (root.live && root.live.available && root.live.frame && root.live.frame.extended_buttons) {
                var ext = root.live.frame.extended_buttons
                for (var i = 0; i < ext.length; i++) {
                    if (ext[i] === "Share" || ext[i] === "CompanionShare") return true
                }
            }
            return false
        }
        if (!currentFrame) return false
        var b = currentFrame.buttons || 0
        switch (code) {
            case "Controller:A": return (b & 0x1000) !== 0
            case "Controller:B": return (b & 0x2000) !== 0
            case "Controller:X": return (b & 0x4000) !== 0
            case "Controller:Y": return (b & 0x8000) !== 0
            case "Controller:LB": return (b & 0x0100) !== 0
            case "Controller:RB": return (b & 0x0200) !== 0
            case "Controller:D-pad Up": return (b & 0x0001) !== 0
            case "Controller:D-pad Down": return (b & 0x0002) !== 0
            case "Controller:D-pad Left": return (b & 0x0004) !== 0
            case "Controller:D-pad Right": return (b & 0x0008) !== 0
            case "Controller:Menu": return (b & 0x0010) !== 0
            case "Controller:View": return (b & 0x0020) !== 0
            case "Controller:Home": return (b & 0x0400) !== 0
            case "Controller:Left Stick": return (b & 0x0040) !== 0
            case "Controller:Right Stick": return (b & 0x0080) !== 0
            case "Controller:LT": return (currentFrame.left_trigger || 0) > 20
            case "Controller:RT": return (currentFrame.right_trigger || 0) > 20
            default: return false
        }
    }

    readonly property var hitRegions: [
        { "code": "Controller:LT", "x": 106.0, "y": 34.0, "width": 52.0, "height": 30.0, "mask": "trigger_left" },
        { "code": "Controller:RT", "x": 366.0, "y": 34.0, "width": 52.0, "height": 30.0, "mask": "trigger_right" },
        { "code": "Controller:LB", "x": 95.0, "y": 64.0, "width": 104.0, "height": 33.0, "mask": "bumper_left" },
        { "code": "Controller:RB", "x": 324.0, "y": 64.0, "width": 104.0, "height": 33.0, "mask": "bumper_right" },
        { "code": "Controller:Home", "x": 245.0, "y": 121.0, "width": 30.0, "height": 30.0, "mask": "button_home" },
        { "code": "Controller:View", "x": 216.0, "y": 149.0, "width": 24.0, "height": 24.0, "mask": "button_view" },
        { "code": "Controller:Share", "x": 249.0, "y": 170.0, "width": 22.0, "height": 22.0, "mask": "button_share" },
        { "code": "Controller:Menu", "x": 283.0, "y": 149.0, "width": 24.0, "height": 24.0, "mask": "button_menu" },
        { "code": "Controller:Y", "x": 365.0, "y": 114.0, "width": 32.0, "height": 32.0, "mask": "button_y" },
        { "code": "Controller:X", "x": 333.0, "y": 144.0, "width": 32.0, "height": 32.0, "mask": "button_x" },
        { "code": "Controller:B", "x": 396.0, "y": 142.0, "width": 32.0, "height": 32.0, "mask": "button_b" },
        { "code": "Controller:A", "x": 364.0, "y": 172.0, "width": 32.0, "height": 32.0, "mask": "button_a" },
        { "code": "Controller:Left Stick", "x": 114.0, "y": 135.0, "width": 60.0, "height": 60.0, "mask": "stick_left" },
        { "code": "Controller:Right Stick", "x": 292.0, "y": 201.0, "width": 60.0, "height": 60.0, "mask": "stick_right" },
        { "code": "Controller:D-pad Up", "x": 190.0, "y": 196.0, "width": 24.0, "height": 24.0, "mask": "dpad_up" },
        { "code": "Controller:D-pad Left", "x": 170.0, "y": 216.0, "width": 24.0, "height": 24.0, "mask": "dpad_left" },
        { "code": "Controller:D-pad Right", "x": 210.0, "y": 216.0, "width": 24.0, "height": 24.0, "mask": "dpad_right" },
        { "code": "Controller:D-pad Down", "x": 190.0, "y": 236.0, "width": 24.0, "height": 24.0, "mask": "dpad_down" }
    ]

    function getColorFor(code) {
        if (code === root.selectedSource) return "#e49b2f" // Source orange (functional semantic)
        if (code === root.selectedDestination) return "#2fbf71" // Target green (functional semantic)
        if (root.isControlPressed(code)) return Theme.main // Live pressed telemetry highlight
        if (root.routes && root.routes[code]) return Theme.main // Mapped control
        if (code === root.hoveredCode) return Theme.hover // Hover
        return "transparent"
    }

    Item {
        id: container
        objectName: "ctrlContainer"
        width: 520
        height: 360
        anchors.centerIn: parent
        scale: Math.max(0.1, Math.min(1.0, Math.min((root.width - 16) / 520, (root.height - 16) / 360)))
        transformOrigin: Item.Center

        // Controller artwork layers (22 bitmap layers from upstream Assets)
        Item {
            id: artLayer
            x: 20; y: 25; width: 480; height: 311

            Image { source: "assets/controller-parts/body.png"; opacity: 0.45; anchors.fill: parent }
            Image { source: "assets/controller-parts/grip_left.png"; opacity: 0.60; anchors.fill: parent }
            Image { source: "assets/controller-parts/grip_right.png"; opacity: 0.60; anchors.fill: parent }
            Image { source: "assets/controller-parts/trigger_left.png"; opacity: 0.65; anchors.fill: parent }
            Image { source: "assets/controller-parts/trigger_right.png"; opacity: 0.65; anchors.fill: parent }
            Image { source: "assets/controller-parts/bumper_left.png"; opacity: 0.65; anchors.fill: parent }
            Image { source: "assets/controller-parts/bumper_right.png"; opacity: 0.65; anchors.fill: parent }
            Image { source: "assets/controller-parts/stick_left.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/stick_right.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/dpad.png"; opacity: 0.45; anchors.fill: parent }
            Image { source: "assets/controller-parts/dpad_up.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/dpad_down.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/dpad_left.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/dpad_right.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_a.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_b.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_x.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_y.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_home.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_share.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_view.png"; opacity: 0.75; anchors.fill: parent }
            Image { source: "assets/controller-parts/button_menu.png"; opacity: 0.75; anchors.fill: parent }
        }

        // Interactive Region Overlays
        Repeater {
            model: root.hitRegions
            delegate: Rectangle {
                id: regionRect
                x: modelData.x
                y: modelData.y
                width: modelData.width
                height: modelData.height
                radius: modelData.code.indexOf("Stick") !== -1 ? width / 2 : Theme.cornerRadius / 2
                color: root.getColorFor(modelData.code)
                opacity: {
                    if (modelData.code === "Controller:LT" && root.currentFrame && root.live && root.live.available) {
                        return Math.max(0.15, root.currentFrame.left_trigger / 255.0)
                    }
                    if (modelData.code === "Controller:RT" && root.currentFrame && root.live && root.live.available) {
                        return Math.max(0.15, root.currentFrame.right_trigger / 255.0)
                    }
                    if (root.isControlPressed(modelData.code)) return 0.85
                    return root.getColorFor(modelData.code) !== "transparent" ? 0.6 : 0.0
                }
                border.width: root.hoveredCode === modelData.code ? 2 : (root.getColorFor(modelData.code) !== "transparent" ? 1 : 0)
                border.color: root.hoveredCode === modelData.code ? Theme.text : Theme.line

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onEntered: root.hoveredCode = modelData.code
                    onExited: if (root.hoveredCode === modelData.code) root.hoveredCode = ""
                    onClicked: function(mouse) {
                        var isStick = modelData.code.indexOf("Stick") !== -1
                        if (isStick && mouse.button === Qt.RightButton) {
                            var side = modelData.code.indexOf("Left") !== -1 ? "Left" : "Right"
                            root.controlClicked(side, "OpenStickTuning")
                        } else {
                            root.controlClicked(modelData.code, "SelectControl")
                        }
                    }
                }
            }
        }

        // Live moving dot - Left stick
        Rectangle {
            id: leftStickDot
            objectName: "leftStickDot"
            width: 14
            height: 14
            radius: 7
            color: Theme.main
            border.color: Theme.text
            border.width: 1.5
            visible: Boolean(root.live && root.live.available && root.currentFrame)
            x: 144.0 - width / 2 + root.leftNormX * 18.0
            y: 165.0 - height / 2 - root.leftNormY * 18.0 // Y-up inversion
            z: 20
        }

        // Live moving dot - Right stick
        Rectangle {
            id: rightStickDot
            objectName: "rightStickDot"
            width: 14
            height: 14
            radius: 7
            color: Theme.main
            border.color: Theme.text
            border.width: 1.5
            visible: Boolean(root.live && root.live.available && root.currentFrame)
            x: 322.0 - width / 2 + root.rightNormX * 18.0
            y: 231.0 - height / 2 - root.rightNormY * 18.0 // Y-up inversion
            z: 20
        }
    }

    // Top Header: Status indicators + One-shot Help + Live Channel Selector
    RowLayout {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 10
        spacing: 8
        z: 10

        // Mod Armed indicator
        Rectangle {
            id: modArmedBadge
            objectName: "modArmedBadge"
            implicitHeight: 22
            implicitWidth: modText.implicitWidth + 14
            radius: 3
            color: (root.live && root.live.available && root.live.frame && root.live.frame.mod_active) ? "#e49b2f" : "transparent"
            border.color: (root.live && root.live.available && root.live.frame && root.live.frame.mod_active) ? "#e49b2f" : Theme.line
            border.width: 1
            visible: Boolean(root.live && root.live.available)

            RowLayout {
                anchors.centerIn: parent
                spacing: 4
                Rectangle {
                    width: 6; height: 6; radius: 3
                    color: (root.live && root.live.frame && root.live.frame.mod_active) ? Theme.text : Theme.muted
                }
                Text {
                    id: modText
                    text: (root.live && root.live.frame && root.live.frame.mod_active) ? "MOD ARMED" : "Mod Idle"
                    color: (root.live && root.live.frame && root.live.frame.mod_active) ? Theme.text : Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 10
                    font.bold: true
                }
            }
        }

        // Mouse Mode indicator
        Rectangle {
            id: mouseModeBadge
            objectName: "mouseModeBadge"
            implicitHeight: 22
            implicitWidth: mouseText.implicitWidth + 14
            radius: 3
            color: (root.live && root.live.available && root.live.frame && root.live.frame.controller_mouse_mode) ? "#2fbf71" : "transparent"
            border.color: (root.live && root.live.available && root.live.frame && root.live.frame.controller_mouse_mode) ? "#2fbf71" : Theme.line
            border.width: 1
            visible: Boolean(root.live && root.live.available)

            RowLayout {
                anchors.centerIn: parent
                spacing: 4
                Rectangle {
                    width: 6; height: 6; radius: 3
                    color: (root.live && root.live.frame && root.live.frame.controller_mouse_mode) ? Theme.text : Theme.muted
                }
                Text {
                    id: mouseText
                    text: (root.live && root.live.frame && root.live.frame.controller_mouse_mode) ? "MOUSE MODE ACTIVE" : "Mouse Mode Off"
                    color: (root.live && root.live.frame && root.live.frame.controller_mouse_mode) ? Theme.text : Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 10
                    font.bold: true
                }
            }
        }

        // One-shot usage help text
        Text {
            id: oneShotHelp
            objectName: "oneShotHelp"
            text: "Tip: Share (Mod) + R-Stick click toggles Mouse"
            color: Theme.muted
            font.family: Theme.family
            font.pixelSize: 10
            Layout.fillWidth: true
            elide: Text.ElideRight
            visible: Boolean(root.live && root.live.available)
        }

        // Explicit Live Channel Selector (Input vs Mapped Output)
        RowLayout {
            spacing: 4

            Text {
                text: "Live:"
                color: Theme.muted
                font.family: Theme.family
                font.pixelSize: 11
            }

            Repeater {
                model: ["input", "output"]
                delegate: Rectangle {
                    implicitWidth: 48
                    implicitHeight: 22
                    radius: 3
                    color: root.liveChannel === modelData ? Theme.main : Theme.panel
                    border.color: root.liveChannel === modelData ? Theme.text : Theme.line
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: modelData === "input" ? "Input" : "Output"
                        color: Theme.text
                        font.family: Theme.family
                        font.pixelSize: 10
                        font.bold: root.liveChannel === modelData
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: root.liveChannel = modelData
                    }
                }
            }
        }
    }
}
