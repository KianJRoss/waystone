import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Rectangle {
    id: root
    objectName: "overlayToolDownloads"
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
                objectName: "overlayDownloadsBackBtn"
                text: "‹ Tools"
                compact: true
                onClicked: root.backRequested()
            }
            Text {
                text: "Downloads"
                color: Theme.text
                font.family: Theme.family
                font.bold: true
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // Active jobs list or empty placeholder
        ListView {
            id: jobsList
            objectName: "overlayDownloadsList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: root.toolsBridge ? root.toolsBridge.activeDownloads : []

            delegate: Rectangle {
                required property var modelData
                width: jobsList.width
                implicitHeight: col.implicitHeight + 12
                color: Theme.panel
                radius: Theme.cornerRadius / 2
                border.color: Qt.rgba(Theme.line.r, Theme.line.g, Theme.line.b, 0.4)

                ColumnLayout {
                    id: col
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: modelData.title || "Game Download"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 11
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.provider_label || ""
                            color: Theme.muted
                            font.pixelSize: 10
                        }
                        OverlayControl {
                            objectName: "overlayDownloadCancelBtn"
                            text: "Cancel"
                            compact: true
                            onClicked: if (root.toolsBridge) root.toolsBridge.cancelDownload(modelData.id)
                        }
                    }

                    ProgressBar {
                        Layout.fillWidth: true
                        from: 0; to: 100
                        value: modelData.percent || 0
                        implicitHeight: 6
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: modelData.message || modelData.phase || ""
                            color: Theme.muted
                            font.pixelSize: 10
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.percent !== null && modelData.percent !== undefined ? modelData.percent + "%" : ""
                            color: Theme.text
                            font.pixelSize: 10
                        }
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                visible: jobsList.count === 0
                color: Qt.rgba(Theme.panel.r, Theme.panel.g, Theme.panel.b, 0.3)
                radius: Theme.cornerRadius / 2

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 4
                    Text {
                        text: "No active downloads"
                        color: Theme.text
                        font.pixelSize: 12
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Text {
                        text: "Install games from your library."
                        color: Theme.muted
                        font.pixelSize: 10
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }

        // Secondary link footer
        OverlayControl {
            objectName: "overlayDownloadsOpenFull"
            text: "Open full Downloads ↗"
            compact: true
            Layout.fillWidth: true
            onClicked: root.openFullRequested()
        }
    }
}
