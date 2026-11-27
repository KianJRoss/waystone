import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var profileData: null

    readonly property var defaultHotkeys: [
        { "action": "OverlayToggle", "key": "O", "name": "Toggle In-Game Hub / Overlay" },
        { "action": "ProfileNext", "key": "N", "name": "Switch to Next Profile" },
        { "action": "ProfilePrev", "key": "P", "name": "Switch to Previous Profile" },
        { "action": "ControllerMouseToggle", "key": "M", "name": "Toggle Controller Mouse Mode" },
        { "action": "RecenterMotion", "key": "C", "name": "Recenter Gyro IMU Bearing" }
    ]

    property string statusMessage: ""

    function loadFromProfile(data) {
        root.profileData = data
    }

    function runAction(action) {
        if (root.service && typeof root.service.invokeAction === "function") {
            var ok = root.service.invokeAction(action)
            root.statusMessage = ok ? ("Ran " + action + ".") : (action + " is not supported by current runtime.")
        } else {
            root.statusMessage = "Unavailable: Action invocation requires live runtime control protocol."
        }
    }

    function routeAction(action) {
        if (!root.studio || !root.studio.selectedSource) {
            root.statusMessage = "Select an input source on the Map tab first, then click Route."
            return
        }
        var src = root.studio.selectedSource
        if (!root.profileData) root.profileData = {}
        if (!root.profileData.routes) root.profileData.routes = {}
        root.profileData.routes[src] = "Action:" + action
        if (root.studio) root.studio.dirty = true
        root.service.saveProfile(JSON.stringify({ routes: root.profileData.routes }))  // only this tab's fields
        root.statusMessage = "Bound " + src + " → Action:" + action
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
                    text: "System Hotkeys & Overlay Triggers"
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

            // The Mod key (saved in the profile being edited)
            ModKeySection {
                service: root.service
                studio: root.studio
            }

            // Discord Push-to-Talk with Vesktop (Mod-key style, saved in the profile being edited)
            PushToTalkSection {
                service: root.service
                studio: root.studio
            }

            // Card 1: Hyprland Global Hotkeys
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 110
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 8

                    Text { text: "Hyprland Global Hotkeys"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }
                    Text {
                        text: "Installs a generated include at ~/.config/hypr/unified-controller-hotkeys.conf. Your main Hyprland configuration is never edited."
                        color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                    }

                    RowLayout {
                        SocialButton {
                            text: "Install Hyprland Hotkeys"
                            enabled: false
                            onClicked: {}
                        }
                        Text {
                            text: "Unavailable: External desktop configuration installer not supported from controller studio."
                            color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                        }
                    }
                }
            }

            // Card 2: Registered Actions & Shortcuts
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: actionsColumn.implicitHeight + 28
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    id: actionsColumn
                    anchors.fill: parent; anchors.margins: 14; spacing: 10

                    Text { text: "Desktop Actions & Direct Shortcuts"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }

                    Repeater {
                        model: root.defaultHotkeys
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            height: 44
                            radius: Theme.cornerRadius / 3
                            color: Theme.background
                            border.color: Theme.line

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 12

                                ColumnLayout {
                                    Layout.preferredWidth: 200
                                    Text { text: modelData.name; color: Theme.text; font.bold: true; font.pixelSize: 12; font.family: Theme.family }
                                    Text { text: modelData.action; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                }

                                // Key chip
                                Rectangle {
                                    implicitWidth: keyLabel.implicitWidth + 14
                                    implicitHeight: 24
                                    radius: 4
                                    color: Theme.main
                                    border.color: Theme.line
                                    Text {
                                        id: keyLabel
                                        anchors.centerIn: parent
                                        text: "Ctrl+Alt+" + modelData.key
                                        color: Theme.text
                                        font.bold: true
                                        font.pixelSize: 11
                                        font.family: Theme.family
                                    }
                                }

                                // Command string
                                Rectangle {
                                    Layout.fillWidth: true
                                    height: 26
                                    radius: 4
                                    color: Theme.panel
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        anchors.left: parent.left
                                        anchors.leftMargin: 8
                                        text: "unified-controller --action \"" + modelData.action + "\""
                                        color: Theme.glyph
                                        font.family: "Monospace"
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                }

                                SocialButton {
                                    text: "Run Now"
                                    onClicked: root.runAction(modelData.action)
                                }

                                SocialButton {
                                    text: "Route"
                                    onClicked: root.routeAction(modelData.action)
                                }
                            }
                        }
                    }
                }
            }

            Text {
                text: "Global desktop capture remains strictly opt-in."
                color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
            }
        }
    }
}
