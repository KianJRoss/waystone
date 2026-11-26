import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Rectangle {
    id: root
    objectName: "overlayToolSaves"
    color: "transparent"
    implicitWidth: 280
    implicitHeight: 280

    property var toolsBridge: null
    property var currentGame: null
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
                objectName: "overlaySavesBackBtn"
                text: "‹ Tools"
                compact: true
                onClicked: root.backRequested()
            }
            Text {
                text: "Save Backup"
                color: Theme.text
                font.family: Theme.family
                font.bold: true
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // Running game details card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: gameInfoCol.implicitHeight + 14
            color: Theme.panel
            radius: Theme.cornerRadius / 2
            border.color: Qt.rgba(Theme.line.r, Theme.line.g, Theme.line.b, 0.4)

            ColumnLayout {
                id: gameInfoCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 4

                Text {
                    text: (root.currentGame && root.currentGame.title)
                        ? root.currentGame.title
                        : (root.toolsBridge && root.toolsBridge.currentRunningTitle ? root.toolsBridge.currentRunningTitle : "No running game")
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: 12
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: (root.currentGame && root.currentGame.title) || (root.toolsBridge && root.toolsBridge.currentRunningTitle)
                        ? "Save backup via Ludusavi"
                        : "Launch a game to enable quick backup"
                    color: Theme.muted
                    font.pixelSize: 10
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        // Action button
        OverlayControl {
            id: backupButton
            objectName: "overlayBackupSaveBtn"
            text: root.toolsBridge && root.toolsBridge.saveBackupRunning ? "⏳  Backing up..." : "💾  Back Up Save Now"
            Layout.fillWidth: true
            enabled: root.toolsBridge && !root.toolsBridge.saveBackupRunning && (
                ((root.currentGame && root.currentGame.title) || (root.toolsBridge && root.toolsBridge.currentRunningTitle)) !== ""
            )
            onClicked: {
                if (root.toolsBridge) {
                    var gid = root.currentGame && root.currentGame.id ? root.currentGame.id : ""
                    var gtitle = root.currentGame && root.currentGame.title ? root.currentGame.title : ""
                    root.toolsBridge.backupCurrentGame(gid, gtitle)
                }
            }
        }

        // Status box
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Qt.rgba(Theme.panel.r, Theme.panel.g, Theme.panel.b, 0.3)
            radius: Theme.cornerRadius / 2
            border.color: Qt.rgba(Theme.line.r, Theme.line.g, Theme.line.b, 0.2)

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 4
                width: parent.width - 16

                Text {
                    id: statusLabel
                    objectName: "overlaySaveStatusText"
                    text: root.toolsBridge ? root.toolsBridge.saveBackupStatus : "Ready"
                    color: Theme.text
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                Text {
                    visible: root.toolsBridge && root.toolsBridge.lastBackupTime !== ""
                    text: root.toolsBridge ? "Last backup: " + root.toolsBridge.lastBackupTime : ""
                    color: Theme.muted
                    font.pixelSize: 10
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        // Secondary link footer
        OverlayControl {
            objectName: "overlaySavesOpenFull"
            text: "Open Save Management ↗"
            compact: true
            Layout.fillWidth: true
            onClicked: root.openFullRequested()
        }
    }
}
