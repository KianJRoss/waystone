import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var live: null

    readonly property bool liveActive: Boolean(root.live && root.live.available)
    readonly property var frame: liveActive ? root.live.frame : null
    readonly property var inFrame: frame ? frame.input : null
    readonly property var outFrame: frame ? frame.output : null

    // Format helper functions
    function hexButtons(btns) {
        if (btns === undefined || btns === null) return "0x0000"
        return "0x" + Number(btns).toString(16).toUpperCase().padStart(4, "0")
    }

    function formatAxes(f) {
        if (!f) return "LX: 0, LY: 0, RX: 0, RY: 0, LT: 0, RT: 0"
        return "LX: " + (f.left_x || 0) + ", LY: " + (f.left_y || 0) +
               ", RX: " + (f.right_x || 0) + ", RY: " + (f.right_y || 0) +
               ", LT: " + (f.left_trigger || 0) + ", RT: " + (f.right_trigger || 0)
    }

    function calcActivity(f) {
        if (!f) return 0
        var act = 0
        if (f.buttons && f.buttons !== 0) act += 40
        if (f.left_x || f.left_y) act += 20
        if (f.right_x || f.right_y) act += 20
        if (f.left_trigger || f.right_trigger) act += 20
        return Math.min(100, act)
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 14

            // Header
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Live Input & Output Telemetry"
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                }
                Rectangle {
                    implicitWidth: liveStatus.implicitWidth + 16
                    implicitHeight: 24
                    radius: 12
                    color: root.liveActive ? "#2fbf71" : Theme.main
                    Text {
                        id: liveStatus
                        anchors.centerIn: parent
                        text: root.liveActive ? "Live Streaming Active" : "Telemetry Offline / Neutral"
                        color: Theme.text
                        font.bold: true
                        font.pixelSize: 11
                        font.family: Theme.family
                    }
                }
            }

            // Top Row: Input Frame & Output Frame
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Input Frame Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 150
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8

                        Text { text: "Input Frame (Hardware)"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }
                        Text { text: "Buttons: " + root.hexButtons(root.inFrame ? root.inFrame.buttons : 0); color: Theme.text; font.family: "Monospace"; font.pixelSize: 13 }
                        Text { text: root.formatAxes(root.inFrame); color: Theme.muted; font.family: "Monospace"; font.pixelSize: 11 }

                        Rectangle {
                            Layout.fillWidth: true; height: 12
                            radius: 6; color: Theme.background; border.color: Theme.line
                            Rectangle {
                                width: Math.max(0, Math.min(parent.width, parent.width * (root.calcActivity(root.inFrame) / 100.0)))
                                height: parent.height; radius: 6; color: "#2fbf71"
                            }
                        }
                    }
                }

                // Output Frame Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 150
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8

                        Text { text: "Output Frame (Virtual Mapped)"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }
                        Text { text: "Buttons: " + root.hexButtons(root.outFrame ? root.outFrame.buttons : 0); color: Theme.text; font.family: "Monospace"; font.pixelSize: 13 }
                        Text { text: root.formatAxes(root.outFrame); color: Theme.muted; font.family: "Monospace"; font.pixelSize: 11 }

                        Rectangle {
                            Layout.fillWidth: true; height: 12
                            radius: 6; color: Theme.background; border.color: Theme.line
                            Rectangle {
                                width: Math.max(0, Math.min(parent.width, parent.width * (root.calcActivity(root.outFrame) / 100.0)))
                                height: parent.height; radius: 6; color: "#e49b2f"
                            }
                        }
                    }
                }
            }

            // Diagnostic Metrics Row
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Loop Timing
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 90
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 4
                        Text { text: "Loop Timing"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                        Text { text: root.liveActive ? "Target: 250 Hz (4.0 ms) · jitter < 0.2 ms" : "Engine stopped or neutral"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                    }
                }

                // DSU & Rumble
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 90
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 4
                        Text { text: "DSU & Rumble"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                        Text { text: (root.frame && root.frame.dsu && root.frame.dsu.running) ? ("DSU port 26760 · " + (root.frame.dsu.clients || 0) + " clients") : "DSU server waiting for client"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                    }
                }

                // Active Modes
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 90
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 4
                        Text { text: "Active Modes"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                        Text {
                            text: (root.frame && root.frame.controller_mouse_mode) ? "Controller Mouse Active" : ((root.frame && root.frame.mod_active) ? "Mod Key Held" : "Standard Gamepad")
                            color: (root.frame && (root.frame.controller_mouse_mode || root.frame.mod_active)) ? "#2fbf71" : Theme.muted
                            font.pixelSize: 11
                            font.family: Theme.family
                        }
                    }
                }
            }

            // Linux Platform Scope & Limitations
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 90
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 4
                    Text { text: "Platform Limitations & Hardware Scope"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                    Text {
                        text: "Fishing audio/perception helpers, Windows GameInput, and Windows Bluetooth management remain unsupported on Linux."
                        color: "#e49b2f"; font.pixelSize: 11; font.family: Theme.family
                    }
                }
            }

            // Raw Telemetry Dump
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 140
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 6
                    Text { text: "Raw Telemetry Snapshot"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                    TextArea {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        readOnly: true
                        text: root.frame ? JSON.stringify(root.frame, null, 2) : "{\n  \"status\": \"neutral\",\n  \"telemetry\": \"streaming socket offline\"\n}"
                        color: Theme.text
                        font.family: "Monospace"
                        font.pixelSize: 10
                        background: Rectangle { color: Theme.background; border.color: Theme.line; radius: Theme.cornerRadius / 2 }
                    }
                }
            }
        }
    }
}
