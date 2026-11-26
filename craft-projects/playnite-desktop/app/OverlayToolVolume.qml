import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Rectangle {
    id: root
    objectName: "overlayToolVolume"
    color: "transparent"
    implicitWidth: 280
    implicitHeight: 280

    property var toolsBridge: null
    signal openFullRequested()
    signal backRequested()

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        // Header
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            OverlayControl {
                objectName: "overlayVolumeBackBtn"
                text: "‹ Tools"
                compact: true
                onClicked: root.backRequested()
            }
            Text {
                text: "Volume Mixer"
                color: Theme.text
                font.family: Theme.family
                font.bold: true
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // Volume controls container
        Flickable {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: contentCol.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: contentCol
                width: scroll.width
                spacing: 8

                // Master Volume
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: masterCol.implicitHeight + 12
                    color: Theme.panel
                    radius: Theme.cornerRadius / 2
                    visible: root.toolsBridge ? root.toolsBridge.masterAvailable : false

                    ColumnLayout {
                        id: masterCol
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: "Master Volume"
                                color: Theme.text
                                font.bold: true
                                font.pixelSize: 11
                                Layout.fillWidth: true
                            }
                            Text {
                                text: root.toolsBridge && root.toolsBridge.masterMuted ? "Muted" : (root.toolsBridge ? root.toolsBridge.masterVolume + "%" : "100%")
                                color: root.toolsBridge && root.toolsBridge.masterMuted ? Theme.muted : Theme.text
                                font.pixelSize: 10
                            }
                            OverlayControl {
                                objectName: "overlayMasterMuteBtn"
                                text: root.toolsBridge && root.toolsBridge.masterMuted ? "Unmute" : "Mute"
                                compact: true
                                onClicked: if (root.toolsBridge) root.toolsBridge.setMasterMuted(!root.toolsBridge.masterMuted)
                            }
                        }

                        Slider {
                            id: masterSlider
                            objectName: "overlayMasterVolumeSlider"
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 1
                            value: root.toolsBridge ? root.toolsBridge.masterVolume : 100
                            enabled: root.toolsBridge && !root.toolsBridge.masterMuted
                            onMoved: if (root.toolsBridge) root.toolsBridge.setMasterVolume(Math.round(value))
                        }
                    }
                }

                // Game Volume (only if running game has an active stream)
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: gameCol.implicitHeight + 12
                    color: Theme.panel
                    radius: Theme.cornerRadius / 2
                    visible: root.toolsBridge ? root.toolsBridge.gameAvailable : false

                    ColumnLayout {
                        id: gameCol
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: "Game Volume"
                                color: Theme.text
                                font.bold: true
                                font.pixelSize: 11
                                Layout.fillWidth: true
                            }
                            Text {
                                text: root.toolsBridge && root.toolsBridge.gameMuted ? "Muted" : (root.toolsBridge ? root.toolsBridge.gameVolume + "%" : "100%")
                                color: root.toolsBridge && root.toolsBridge.gameMuted ? Theme.muted : Theme.text
                                font.pixelSize: 10
                            }
                            OverlayControl {
                                objectName: "overlayGameMuteBtn"
                                text: root.toolsBridge && root.toolsBridge.gameMuted ? "Unmute" : "Mute"
                                compact: true
                                onClicked: if (root.toolsBridge) root.toolsBridge.setGameMuted(!root.toolsBridge.gameMuted)
                            }
                        }

                        Slider {
                            id: gameSlider
                            objectName: "overlayGameVolumeSlider"
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 1
                            value: root.toolsBridge ? root.toolsBridge.gameVolume : 100
                            enabled: root.toolsBridge && !root.toolsBridge.gameMuted
                            onMoved: if (root.toolsBridge) root.toolsBridge.setGameVolume(Math.round(value))
                        }
                    }
                }

                // Chat Volume (only if voice stream is active)
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: chatCol.implicitHeight + 12
                    color: Theme.panel
                    radius: Theme.cornerRadius / 2
                    visible: root.toolsBridge ? root.toolsBridge.chatAvailable : false

                    ColumnLayout {
                        id: chatCol
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: "Voice & Chat"
                                color: Theme.text
                                font.bold: true
                                font.pixelSize: 11
                                Layout.fillWidth: true
                            }
                            Text {
                                text: root.toolsBridge && root.toolsBridge.chatMuted ? "Muted" : (root.toolsBridge ? root.toolsBridge.chatVolume + "%" : "100%")
                                color: root.toolsBridge && root.toolsBridge.chatMuted ? Theme.muted : Theme.text
                                font.pixelSize: 10
                            }
                            OverlayControl {
                                objectName: "overlayChatMuteBtn"
                                text: root.toolsBridge && root.toolsBridge.chatMuted ? "Unmute" : "Mute"
                                compact: true
                                onClicked: if (root.toolsBridge) root.toolsBridge.setChatMuted(!root.toolsBridge.chatMuted)
                            }
                        }

                        Slider {
                            id: chatSlider
                            objectName: "overlayChatVolumeSlider"
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 1
                            value: root.toolsBridge ? root.toolsBridge.chatVolume : 100
                            enabled: root.toolsBridge && !root.toolsBridge.chatMuted
                            onMoved: if (root.toolsBridge) root.toolsBridge.setChatVolume(Math.round(value))
                        }
                    }
                }

                // Unavailable banner if no streams available
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: unavailCol.implicitHeight + 16
                    color: Qt.rgba(Theme.panel.r, Theme.panel.g, Theme.panel.b, 0.3)
                    radius: Theme.cornerRadius / 2
                    visible: !(root.toolsBridge && (root.toolsBridge.masterAvailable || root.toolsBridge.gameAvailable || root.toolsBridge.chatAvailable))

                    ColumnLayout {
                        id: unavailCol
                        anchors.centerIn: parent
                        spacing: 4
                        Text {
                            text: "Audio controls unavailable"
                            color: Theme.text
                            font.pixelSize: 12
                            Layout.alignment: Qt.AlignHCenter
                        }
                        Text {
                            text: "System pactl audio service is not detected."
                            color: Theme.muted
                            font.pixelSize: 10
                            Layout.alignment: Qt.AlignHCenter
                        }
                    }
                }
            }
        }

        // Secondary link footer
        OverlayControl {
            objectName: "overlayVolumeOpenFull"
            text: "Open Audio Settings ↗"
            compact: true
            Layout.fillWidth: true
            onClicked: root.openFullRequested()
        }
    }
}
