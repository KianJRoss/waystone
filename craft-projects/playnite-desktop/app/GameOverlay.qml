import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "overlay_components"

Window {
    id: overlay
    objectName: "gameOverlay"
    title: "Waystone Game Overlay"
    transientParent: null
    visible: false
    width: 860
    height: 580
    minimumWidth: 720
    minimumHeight: 480
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: Theme.background

    property var socialView: null
    property var voiceView: null
    property var currentGame: ({})
    property string activeSection: "hub" // "hub", "party"
    property int hubIndex: 0 // 0: Resume, 1: Controllers, 2: Library, 3: Downloads, 4: Settings
    readonly property int hubCount: 5

    signal libraryRequested()
    signal controllersRequested()
    signal connectionsRequested()
    signal downloadsRequested()
    signal settingsRequested()
    signal returnToGameRequested()

    onVisibleChanged: {
        if (!visible) returnToGameRequested()
    }

    function open() {
        show()
        raise()
        requestActivate()
        overlayContent.forceActiveFocus()
    }

    function closeOverlay() {
        hide()
    }

    function toggle() {
        if (visible) hide()
        else open()
    }

    function triggerHubAction(index) {
        if (index === 0) {
            closeOverlay()
        } else if (index === 1) {
            controllersRequested()
            closeOverlay()
        } else if (index === 2) {
            libraryRequested()
            closeOverlay()
        } else if (index === 3) {
            downloadsRequested()
            closeOverlay()
        } else if (index === 4) {
            settingsRequested()
            closeOverlay()
        }
    }

    function navigate(action) {
        if (action === "back" || action === "menu") {
            closeOverlay()
            return true
        }

        if (action === "next_section" || action === "previous_section") {
            activeSection = activeSection === "hub" ? "party" : "hub"
            return true
        }

        if (activeSection === "hub") {
            if (action === "left") {
                hubIndex = Math.max(0, hubIndex - 1)
                return true
            } else if (action === "right") {
                if (hubIndex >= hubCount - 1) {
                    activeSection = "party"
                } else {
                    hubIndex = Math.min(hubCount - 1, hubIndex + 1)
                }
                return true
            } else if (action === "accept") {
                triggerHubAction(hubIndex)
                return true
            } else if (action === "down" || action === "up") {
                // Directional wrap / switch
                return true
            }
        } else if (activeSection === "party") {
            if (action === "left") {
                activeSection = "hub"
                return true
            } else {
                return speakerWidget.navigate(action)
            }
        }
        return false
    }

    Shortcut {
        sequence: "Escape"
        enabled: overlay.visible
        onActivated: overlay.closeOverlay()
    }

    Shortcut {
        sequence: "Ctrl+Shift+O"
        onActivated: overlay.toggle()
    }

    Rectangle {
        id: overlayContent
        anchors.fill: parent
        color: Theme.background
        border.color: Theme.line
        border.width: 1
        focus: true

        Keys.onLeftPressed: overlay.navigate("left")
        Keys.onRightPressed: overlay.navigate("right")
        Keys.onUpPressed: overlay.navigate("up")
        Keys.onDownPressed: overlay.navigate("down")
        Keys.onReturnPressed: overlay.navigate("accept")
        Keys.onEscapePressed: overlay.navigate("back")
        Keys.onMenuPressed: overlay.navigate("menu")
        Keys.onTabPressed: overlay.navigate("next_section")
        Keys.onBacktabPressed: overlay.navigate("previous_section")

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 16

            // Top Header Bar
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Text {
                    text: "WAYSTONE IN-GAME HUB"
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: 18
                    font.letterSpacing: 1.2
                    textFormat: Text.PlainText
                }

                Rectangle {
                    implicitWidth: 8
                    implicitHeight: 8
                    radius: 4
                    color: "#2fbf71"
                }

                Text {
                    text: "IN-GAME"
                    color: "#2fbf71"
                    font.pixelSize: 12
                    font.bold: true
                    textFormat: Text.PlainText
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: Qt.formatTime(new Date(), "hh:mm AP")
                    color: Theme.muted
                    font.pixelSize: 13
                    textFormat: Text.PlainText
                }

                SocialButton {
                    objectName: "overlayCloseButton"
                    text: "Return to game (Esc)"
                    implicitHeight: 34
                    onClicked: overlay.closeOverlay()
                }
            }

            // Main Two-Column Hub Area
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 18

                // Left Column: Steam-like In-Game Hub (Current Game & Navigation)
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 480
                    spacing: 14

                    // Current Game Hero Card
                    Rectangle {
                        id: gameHeroCard
                        objectName: "overlayGameHeroCard"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 180
                        color: Theme.panel
                        radius: Theme.cornerRadius
                        border.color: Theme.line
                        border.width: 1

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 10

                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: overlay.currentGame && overlay.currentGame.title ? "NOW PLAYING" : "GAME SESSION"
                                    color: Theme.muted
                                    font.pixelSize: 11
                                    font.bold: true
                                    font.letterSpacing: 1.0
                                    textFormat: Text.PlainText
                                }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: overlay.currentGame ? (overlay.currentGame.platform || "PC") : "PC"
                                    color: Theme.muted
                                    font.pixelSize: 11
                                    textFormat: Text.PlainText
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: overlay.currentGame && overlay.currentGame.title ? overlay.currentGame.title : "No tracked game"
                                color: Theme.text
                                font.pixelSize: 26
                                font.bold: true
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }

                            Text {
                                Layout.fillWidth: true
                                text: overlay.currentGame && overlay.currentGame.playtime ? ("Session: " + overlay.currentGame.playtime) : (overlay.currentGame && overlay.currentGame.title ? "Tracked running session" : "Launch from Waystone to track the session")
                                color: Theme.muted
                                font.pixelSize: 13
                                textFormat: Text.PlainText
                            }

                            Item { Layout.fillHeight: true }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                SocialButton {
                                    id: resumeHeroBtn
                                    objectName: "overlayResumeButton"
                                    text: "▶ Resume Game"
                                    implicitHeight: 40
                                    Layout.fillWidth: true
                                    selected: overlay.activeSection === "hub" && overlay.hubIndex === 0
                                    onClicked: overlay.triggerHubAction(0)
                                }

                                SocialButton {
                                    id: controllerHeroBtn
                                    objectName: "overlayControllersButton"
                                    text: "🎮 Controllers"
                                    implicitHeight: 40
                                    Layout.fillWidth: true
                                    selected: overlay.activeSection === "hub" && overlay.hubIndex === 1
                                    onClicked: overlay.triggerHubAction(1)
                                }
                            }
                        }
                    }

                    // Navigation Actions Card
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: Theme.panel
                        radius: Theme.cornerRadius
                        border.color: Theme.line
                        border.width: 1

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 12

                            Text {
                                text: "SYSTEM & OVERLAY NAVIGATION"
                                color: Theme.muted
                                font.pixelSize: 11
                                font.bold: true
                                font.letterSpacing: 1.0
                                textFormat: Text.PlainText
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10

                                SocialButton {
                                    id: libraryBtn
                                    objectName: "overlayLibraryButton"
                                    text: "Library"
                                    implicitHeight: 38
                                    Layout.fillWidth: true
                                    selected: overlay.activeSection === "hub" && overlay.hubIndex === 2
                                    onClicked: overlay.triggerHubAction(2)
                                }

                                SocialButton {
                                    id: downloadsBtn
                                    objectName: "overlayDownloadsButton"
                                    text: "Downloads"
                                    implicitHeight: 38
                                    Layout.fillWidth: true
                                    selected: overlay.activeSection === "hub" && overlay.hubIndex === 3
                                    onClicked: overlay.triggerHubAction(3)
                                }

                                SocialButton {
                                    id: settingsBtn
                                    objectName: "overlaySettingsButton"
                                    text: "Connections"
                                    implicitHeight: 38
                                    Layout.fillWidth: true
                                    selected: overlay.activeSection === "hub" && overlay.hubIndex === 4
                                    onClicked: overlay.triggerHubAction(4)
                                }
                            }

                            Item { Layout.fillHeight: true }

                            Text {
                                text: "Tip: Controller overlay shortcuts and system volume can be managed in Controllers studio."
                                color: Theme.muted
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }

                // Right Column: Party & Active Speaker Widget
                ActiveSpeakerWidget {
                    id: speakerWidget
                    objectName: "overlaySpeakerWidget"
                    Layout.fillHeight: true
                    Layout.preferredWidth: 320
                    Layout.minimumWidth: 260
                    socialView: overlay.socialView
                    voiceView: overlay.voiceView
                    hasActiveFocus: overlay.activeSection === "party"
                }
            }

            // Bottom Bar: Controller Hints
            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                Text {
                    text: "Ⓑ / Esc Return to Game   Ⓐ Select   Ⓛ/Ⓡ Switch Section   ✛ Navigate"
                    color: Theme.muted
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: overlay.activeSection === "hub" ? "Focus: Navigation Hub" : "Focus: Party & Voice"
                    color: Theme.text
                    font.pixelSize: 12
                    font.bold: true
                    textFormat: Text.PlainText
                }
            }
        }
    }
}
