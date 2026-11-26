import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var commands: null
    property var profileData: null
    property var live: null

    // Rumble settings
    property bool rumbleEnabled: true
    property real leftIntensity: 100.0
    property real rightIntensity: 100.0

    // Typed command capabilities
    readonly property bool canTestRumble: Boolean(commands && commands.available && !commands.busy && !commands.uncertain && commands.capabilities && commands.capabilities.indexOf("rumble_test") !== -1)
    readonly property bool canStopRumble: Boolean(commands && commands.available && !commands.busy && commands.capabilities && commands.capabilities.indexOf("rumble_stop") !== -1)

    onVisibleChanged: {
        if (visible && commands && typeof commands.refreshCapabilities === "function") {
            commands.refreshCapabilities()
        }
    }

    // Live rumble telemetry (observational, strictly gated, never fabricated zero)
    readonly property bool liveActive: Boolean(root.live && root.live.available)
    readonly property bool liveRumblePresent: Boolean(liveActive && root.live.frame && root.live.frame.rumble && root.live.frame.rumble.heavy !== undefined)
    readonly property real liveHeavy: liveRumblePresent ? (root.live.frame.rumble.heavy || 0.0) : 0.0
    readonly property real liveLight: liveRumblePresent ? (root.live.frame.rumble.light || 0.0) : 0.0

    // Validation error text & feedback
    property string validationError: ""
    property string statusMessage: ""

    Connections {
        target: root.service
        function onOperationFinished(success) {
            if (success) {
                root.statusMessage = "Rumble settings saved."
                if (root.studio) root.studio.dirty = false
            } else {
                root.statusMessage = "Save failed. Draft preserved."
            }
        }
    }

    function isFiniteNum(val) {
        return typeof val === "number" && !isNaN(val) && isFinite(val)
    }

    function loadFromProfile(data) {
        if (!data) return
        root.profileData = data

        var r = data.rumble || {}
        root.rumbleEnabled = (r.enabled !== false)
        root.leftIntensity = isFiniteNum(r.left_intensity) ? r.left_intensity : 100.0
        root.rightIntensity = isFiniteNum(r.right_intensity) ? r.right_intensity : 100.0

        root.validationError = ""
        root.statusMessage = ""
    }

    function validateAndApply() {
        if (!isFiniteNum(leftIntensity) || !isFiniteNum(rightIntensity)) {
            validationError = "Rumble intensities must be finite numbers."
            return false
        }

        if (leftIntensity < 0.0 || leftIntensity > 100.0 || rightIntensity < 0.0 || rightIntensity > 100.0) {
            validationError = "Rumble intensity must be between 0% and 100%."
            return false
        }

        validationError = ""

        if (!profileData) profileData = {}
        if (!profileData.rumble) profileData.rumble = {}

        profileData.rumble.enabled = root.rumbleEnabled
        profileData.rumble.left_intensity = Math.round(root.leftIntensity)
        profileData.rumble.right_intensity = Math.round(root.rightIntensity)

        return true
    }

    function saveVibration() {
        if (validateAndApply()) {
            if (root.studio) root.studio.dirty = true
            root.statusMessage = "Saving rumble settings…"
            root.service.saveProfile(JSON.stringify({ rumble: root.profileData.rumble }))  // only this tab's fields
        }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 14

            // Header & Save / Reset Bar
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Text {
                    text: "Haptic Feedback & Rumble Emulation"
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                }

                CheckBox {
                    text: "Enable controller rumble"
                    checked: root.rumbleEnabled
                    onToggled: {
                        root.rumbleEnabled = checked
                        if (root.studio) root.studio.dirty = true
                    }
                }

                SocialButton {
                    id: saveVibrationButton
                    objectName: "saveVibrationButton"
                    text: "Save Rumble"
                    enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain
                    onClicked: root.saveVibration()
                }

                SocialButton {
                    text: "Reset Defaults"
                    onClicked: {
                        root.rumbleEnabled = true
                        root.leftIntensity = 100.0
                        root.rightIntensity = 100.0
                        root.validationError = ""
                        root.statusMessage = ""
                        if (root.studio) root.studio.dirty = true
                    }
                }
            }

            // Status feedback / Validation banner
            Text {
                Layout.fillWidth: true
                text: root.validationError
                color: "#e03a2f"; font.pixelSize: 12; font.family: Theme.family
                visible: root.validationError.length > 0
            }

            Text {
                Layout.fillWidth: true
                text: root.statusMessage
                color: "#2fbf71"; font.pixelSize: 12; font.family: Theme.family
                visible: root.statusMessage.length > 0 && root.validationError.length === 0
            }

            // Notice Banner Card
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 70
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 4
                    Text { text: "DSU client rumble forwarding & motor gain configuration"; font.bold: true; font.pixelSize: 13; color: Theme.text; font.family: Theme.family }
                    Text {
                        text: "Rumble test triggers disabled until headless engine motor actuation protocol is active."
                        color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                    }
                }
            }

            // Motor Tuning Cards
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Left Grip / Heavy Motor
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 250
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { text: "Left Grip · Heavy Motor"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }
                                Text { text: "Low-frequency impacts and heavy recoil"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                            }
                            Text {
                                text: Math.round(root.leftIntensity) + "%"
                                font.bold: true; font.pixelSize: 16; color: "#2fbf71"; font.family: Theme.family
                            }
                        }

                        Slider {
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 1; value: root.leftIntensity
                            onMoved: {
                                root.leftIntensity = value
                                if (root.studio) root.studio.dirty = true
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Text {
                                text: root.liveRumblePresent ? "Live motor output" : "Live motor output: Unavailable (telemetry absent)"
                                color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                            }
                            Rectangle {
                                Layout.fillWidth: true; height: 16
                                radius: 8; color: Theme.background; border.color: Theme.line
                                Rectangle {
                                    width: root.liveRumblePresent ? Math.max(0, Math.min(parent.width, parent.width * (root.liveHeavy / 100.0))) : 0
                                    height: parent.height
                                    radius: 8
                                    color: "#2fbf71"
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            SocialButton {
                                objectName: "testHeavyMotorButton"
                                text: "Test Heavy Motor"
                                enabled: root.canTestRumble
                                onClicked: if (root.commands && typeof root.commands.testRumble === "function") root.commands.testRumble(root.leftIntensity / 100.0, 0, 500)
                            }
                            Text {
                                text: root.commands ? root.commands.status : "Rumble actuation protocol unavailable."
                                color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                // Right Grip / Light Motor
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 250
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { text: "Right Grip · Light Motor"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }
                                Text { text: "High-frequency textures and engine hum"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                            }
                            Text {
                                text: Math.round(root.rightIntensity) + "%"
                                font.bold: true; font.pixelSize: 16; color: "#2fbf71"; font.family: Theme.family
                            }
                        }

                        Slider {
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 1; value: root.rightIntensity
                            onMoved: {
                                root.rightIntensity = value
                                if (root.studio) root.studio.dirty = true
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Text {
                                text: root.liveRumblePresent ? "Live motor output" : "Live motor output: Unavailable (telemetry absent)"
                                color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                            }
                            Rectangle {
                                Layout.fillWidth: true; height: 16
                                radius: 8; color: Theme.background; border.color: Theme.line
                                Rectangle {
                                    width: root.liveRumblePresent ? Math.max(0, Math.min(parent.width, parent.width * (root.liveLight / 100.0))) : 0
                                    height: parent.height
                                    radius: 8
                                    color: "#2fbf71"
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            SocialButton {
                                objectName: "testLightMotorButton"
                                text: "Test Light Motor"
                                enabled: root.canTestRumble
                                onClicked: if (root.commands && typeof root.commands.testRumble === "function") root.commands.testRumble(0, root.rightIntensity / 100.0, 500)
                            }
                            Text {
                                text: root.commands ? root.commands.status : "Rumble actuation protocol unavailable."
                                color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }

            // Bottom Gated Controls
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 60
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                RowLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 10
                    SocialButton {
                        objectName: "testBothMotorsButton"
                        text: "Test Both Motors"
                        enabled: root.canTestRumble
                        onClicked: if (root.commands && typeof root.commands.testRumble === "function") root.commands.testRumble(root.leftIntensity / 100.0, root.rightIntensity / 100.0, 500)
                    }
                    SocialButton {
                        objectName: "stopRumbleButton"
                        text: "Stop Rumble"
                        enabled: root.canStopRumble
                        onClicked: if (root.commands && typeof root.commands.stopRumble === "function") root.commands.stopRumble()
                    }
                    Text {
                        text: root.commands ? root.commands.status : "Unknown native rumble fields are preserved across saves."
                        color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
