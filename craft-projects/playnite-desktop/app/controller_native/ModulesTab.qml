import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var profileData: null

    property bool fishingAutonomy: false
    property string statusMessage: ""

    function loadFromProfile(data) {
        if (!data) return
        root.profileData = data
        root.fishingAutonomy = (data.fishing_autonomy_enabled === true)
        root.statusMessage = ""
    }

    Connections {
        target: root.service
        function onOperationFinished(success) {
            if (success) {
                root.statusMessage = "Profile flag saved. Unavailable Linux functionality remains inert."
                if (root.studio) root.studio.dirty = false
            } else {
                root.statusMessage = "Save failed. Draft preserved."
            }
        }
    }

    function saveModuleSettings() {
        if (!root.profileData) root.profileData = {}
        root.profileData.fishing_autonomy_enabled = root.fishingAutonomy
        if (root.studio) root.studio.dirty = true
        root.statusMessage = "Saving module flags…"
        root.service.saveProfile(JSON.stringify({ fishing_autonomy_enabled: root.profileData.fishing_autonomy_enabled }))  // only this tab's fields
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
                    text: "Controller Studio Modules & Subsystems"
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                }

                SocialButton {
                    id: saveModulesButton
                    objectName: "saveModulesButton"
                    text: "Save Module Flags"
                    enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain
                    onClicked: root.saveModuleSettings()
                }
            }

            // Status message banner
            Text {
                Layout.fillWidth: true
                text: root.statusMessage
                color: "#2fbf71"; font.pixelSize: 12; font.family: Theme.family
                visible: root.statusMessage.length > 0
            }

            // Module 1: Fishing Helper
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 140
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Fishing Helper"; font.bold: true; font.pixelSize: 16; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                        Rectangle {
                            implicitWidth: pill1.implicitWidth + 14
                            implicitHeight: 22; radius: 4
                            color: Theme.background; border.color: "#e49b2f"
                            Text { id: pill1; anchors.centerIn: parent; text: "Not ported"; color: "#e49b2f"; font.bold: true; font.pixelSize: 11; font.family: Theme.family }
                        }
                    }

                    Text {
                        text: "Windows audio/perception module. PipeWire native audio stream listener has not yet been ported to Linux."
                        color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                    }

                    CheckBox {
                        text: "Allow autonomous patrol input (profile flag only; module remains inactive on Linux)"
                        checked: root.fishingAutonomy
                        onToggled: {
                            root.fishingAutonomy = checked
                            if (root.studio) root.studio.dirty = true
                        }
                    }
                }
            }

            // Module 2: Feature Studio
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 100
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Feature Studio"; font.bold: true; font.pixelSize: 16; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                        Rectangle {
                            implicitWidth: pill2.implicitWidth + 14
                            implicitHeight: 22; radius: 4
                            color: Theme.background; border.color: "#e49b2f"
                            Text { id: pill2; anchors.centerIn: parent; text: "Not ported"; color: "#e49b2f"; font.bold: true; font.pixelSize: 11; font.family: Theme.family }
                        }
                    }

                    Text {
                        text: "Dynamic feature and profile authoring tools are not yet available on Linux."
                        color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                    }
                }
            }

            // Module 3: Companion Tools
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 100
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Companion Tools"; font.bold: true; font.pixelSize: 16; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                        Rectangle {
                            implicitWidth: pill3.implicitWidth + 14
                            implicitHeight: 22; radius: 4
                            color: Theme.background; border.color: "#e49b2f"
                            Text { id: pill3; anchors.centerIn: parent; text: "Not ported"; color: "#e49b2f"; font.bold: true; font.pixelSize: 11; font.family: Theme.family }
                        }
                    }

                    Text {
                        text: "Windows tray and companion integrations are not available on Linux desktop."
                        color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                    }
                }
            }
        }
    }
}
