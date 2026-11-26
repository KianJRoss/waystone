import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var deviceCommands: null
    property var profileData: null

    // Joy-Con inputs
    property string leftJoyConId: ""
    property string rightJoyConId: ""
    property string selectedPairId: ""

    // Capture settings (opt-in)
    property bool exclusiveCapture: deviceCommands ? Boolean(deviceCommands.exclusiveCapture) : false

    property string statusMessage: ""

    readonly property bool canPair: Boolean(
        deviceCommands &&
        deviceCommands.capabilities.indexOf("joycon_pair") !== -1 &&
        !deviceCommands.busy &&
        !deviceCommands.uncertain &&
        leftJoyConId.trim().length > 0 &&
        rightJoyConId.trim().length > 0 &&
        leftJoyConId.trim() !== rightJoyConId.trim()
    )

    readonly property bool canUnpair: Boolean(
        deviceCommands &&
        deviceCommands.capabilities.indexOf("joycon_unpair") !== -1 &&
        !deviceCommands.busy &&
        !deviceCommands.uncertain &&
        selectedPairId.trim().length > 0
    )

    readonly property bool canSetCapture: Boolean(
        deviceCommands &&
        deviceCommands.capabilities.indexOf("device_capture") !== -1 &&
        !deviceCommands.busy &&
        !deviceCommands.uncertain
    )

    Connections {
        target: root.deviceCommands
        function onOperationFinished(op, success) {
            if (success) {
                root.statusMessage = "Device operation '" + op + "' verified by provider."
            } else {
                root.statusMessage = "Device operation '" + op + "' failed or outcome uncertain."
            }
        }
    }

    function loadFromProfile(data) {
        root.profileData = data
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
                    text: "Connected Devices & Capture Settings"
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                }
            }

            // Status message banner
            Text {
                Layout.fillWidth: true
                text: root.statusMessage
                color: "#2fbf71"; font.pixelSize: 12; font.family: Theme.family
                visible: root.statusMessage.length > 0
            }

            // Card 1: Connected Devices Grid
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.max(120, devicesColumn.implicitHeight + 28)
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    id: devicesColumn
                    anchors.fill: parent; anchors.margins: 14; spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Physical & Virtual Devices"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                        SocialButton {
                            objectName: "openDeadbandButton"
                            text: "Open GameSir in Deadband"
                            visible: Boolean(root.deviceCommands && root.deviceCommands.deadbandAvailable)
                            onClicked: root.deviceCommands.openDeadband()
                        }
                    }

                    Repeater {
                        model: (root.service && root.service.devices) ? root.service.devices : []
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            height: 52
                            radius: Theme.cornerRadius / 2
                            color: Theme.background
                            border.color: Theme.line

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 12

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: modelData.name || "Unknown Controller"
                                        color: Theme.text
                                        font.bold: true
                                        font.pixelSize: 13
                                        font.family: Theme.family
                                    }
                                    Text {
                                        text: (modelData.virtual ? "Virtual Controller" : "Physical Controller") + (modelData.stable_id ? (" · " + modelData.stable_id) : "")
                                        color: Theme.muted
                                        font.pixelSize: 11
                                        font.family: "Monospace"
                                    }
                                }

                                Rectangle {
                                    implicitWidth: devStatus.implicitWidth + 16
                                    implicitHeight: 24
                                    radius: 12
                                    color: modelData.connected ? "#2fbf71" : Theme.main
                                    Text {
                                        id: devStatus
                                        anchors.centerIn: parent
                                        text: modelData.virtual ? "Virtual" : (modelData.connected ? "Connected" : "Disconnected")
                                        color: Theme.text
                                        font.bold: true
                                        font.pixelSize: 11
                                        font.family: Theme.family
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        text: (!root.service || !root.service.devices || root.service.devices.length === 0) ? "No physical or virtual gamepads currently recognized." : ""
                        color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                        visible: !root.service || !root.service.devices || root.service.devices.length === 0
                    }
                }
            }

            // Virtual pad: what games see. Saved in the profile being edited (Kian 2026-11-25: pad type is set here,
            // under Devices; Profiles is only for choosing and saving profiles).
            Rectangle {
                objectName: "devicesVirtualPadCard"
                Layout.fillWidth: true
                implicitHeight: virtualPadColumn.implicitHeight + 28
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    id: virtualPadColumn
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14; spacing: 6
                    VirtualPadPicker {
                        objectName: "controllersVirtualPadPicker"
                        Layout.fillWidth: true
                        editor: root.service
                        controlObjectName: "virtualGamepadTypeCombo"
                        compact: false
                    }
                }
            }

            // How controllers combine and which output slots they drive (saved in the profile being edited)
            DeviceOutputSection {
                service: root.service
                studio: root.studio
            }

            // Card 2: Joy-Con Pairing & Input Capture Configuration
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Joy-Con Pairing Card
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: joyconColumn.implicitHeight + 28
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        id: joyconColumn
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        Text { text: "Joy-Con Pairing"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }

                        // Active Pairs Flow
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            visible: Boolean(root.deviceCommands && root.deviceCommands.pairs && root.deviceCommands.pairs.length > 0)
                            Text { text: "Active Pairs:"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: (root.deviceCommands && root.deviceCommands.pairs) ? root.deviceCommands.pairs : []
                                    delegate: Rectangle {
                                        implicitWidth: pairText.implicitWidth + 14
                                        implicitHeight: 22; radius: 4
                                        color: root.selectedPairId === modelData.id ? Theme.main : Theme.background
                                        border.color: Theme.line
                                        Text {
                                            id: pairText
                                            anchors.centerIn: parent
                                            text: (modelData.name || modelData.id)
                                            color: Theme.text
                                            font.pixelSize: 10
                                            font.family: Theme.family
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            onClicked: root.selectedPairId = modelData.id
                                        }
                                    }
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            TextField {
                                objectName: "leftJoyConIdInput"
                                Layout.fillWidth: true
                                placeholderText: "Left Joy-Con stable ID (e.g. dev_joycon_l)"
                                color: Theme.text
                                font.family: Theme.family
                                background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                onTextEdited: root.leftJoyConId = text
                            }
                            TextField {
                                objectName: "rightJoyConIdInput"
                                Layout.fillWidth: true
                                placeholderText: "Right Joy-Con stable ID (e.g. dev_joycon_r)"
                                color: Theme.text
                                font.family: Theme.family
                                background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                onTextEdited: root.rightJoyConId = text
                            }
                        }

                        RowLayout {
                            SocialButton {
                                objectName: "pairJoyConsButton"
                                text: "Pair L + R"
                                enabled: root.canPair
                                onClicked: if (root.deviceCommands && typeof root.deviceCommands.pairJoyCons === "function") {
                                    root.statusMessage = "Sending pair command to provider…"
                                    root.deviceCommands.pairJoyCons(root.leftJoyConId.trim(), root.rightJoyConId.trim())
                                }
                            }
                            SocialButton {
                                objectName: "unpairJoyConsButton"
                                text: "Unpair Selected"
                                enabled: root.canUnpair
                                onClicked: if (root.deviceCommands && typeof root.deviceCommands.unpairJoyCons === "function") {
                                    root.statusMessage = "Sending unpair command to provider…"
                                    root.deviceCommands.unpairJoyCons(root.selectedPairId.trim())
                                }
                            }
                            Text {
                                text: root.deviceCommands ? root.deviceCommands.status : "Device command host unavailable."
                                color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                // Input Capture Policy Card
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: captureColumn.implicitHeight + 28
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        id: captureColumn
                        anchors.fill: parent; anchors.margins: 14; spacing: 8

                        Text { text: "Desktop Capture Policy (Opt-In)"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }

                        CheckBox {
                            objectName: "exclusiveCaptureCheckBox"
                            text: "Exclusive controller capture (opt-in)"
                            checked: root.exclusiveCapture
                            enabled: root.canSetCapture
                            onToggled: {
                                if (root.deviceCommands && typeof root.deviceCommands.setExclusiveCapture === "function") {
                                    root.statusMessage = "Updating exclusive capture setting…"
                                    root.deviceCommands.setExclusiveCapture(checked)
                                }
                            }
                        }
                        Text {
                            text: "Opt-in exclusive capture via SDL provider readback. Notice: hidraw may not exclude external OS consumers."
                            color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }

                        CheckBox {
                            text: "Keyboard capture (opt-in)"
                            checked: false
                            enabled: false
                        }
                        Text {
                            text: "Unavailable: System keyboard capture protocol not delivered in device contract."
                            color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }

                        CheckBox {
                            text: "Mouse capture (opt-in)"
                            checked: false
                            enabled: false
                        }
                        Text {
                            text: "Unavailable: System mouse capture protocol not delivered in device contract."
                            color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }
    }
}
