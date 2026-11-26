import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Rectangle {
    id: root
    objectName: "overlayToolScreenshot"
    color: "transparent"
    implicitWidth: 280
    implicitHeight: 280

    property var toolsBridge: null
    property var surfaceWindow: null
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
                objectName: "overlayScreenshotBackBtn"
                text: "‹ Tools"
                compact: true
                onClicked: root.backRequested()
            }
            Text {
                text: "Screenshot"
                color: Theme.text
                font.family: Theme.family
                font.bold: true
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // Action button
        OverlayControl {
            id: captureButton
            objectName: "overlayTakeScreenshotBtn"
            text: "📸  Take Screenshot"
            Layout.fillWidth: true
            onClicked: {
                if (root.toolsBridge) {
                    root.toolsBridge.takeScreenshot(root.surfaceWindow)
                }
            }
        }

        // Thumbnail preview container
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel
            radius: Theme.cornerRadius / 2
            border.color: Qt.rgba(Theme.line.r, Theme.line.g, Theme.line.b, 0.4)
            clip: true

            Image {
                id: thumb
                objectName: "overlayScreenshotThumbnail"
                anchors.fill: parent
                anchors.margins: 4
                fillMode: Image.PreserveAspectFit
                source: root.toolsBridge ? root.toolsBridge.lastScreenshotUrl : ""
                visible: root.toolsBridge && root.toolsBridge.hasScreenshot
                cache: false
            }

            ColumnLayout {
                anchors.centerIn: parent
                visible: !thumb.visible
                spacing: 4
                Text {
                    text: "No screenshots taken yet"
                    color: Theme.text
                    font.pixelSize: 11
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    text: "Saves to Pictures/Waystone"
                    color: Theme.muted
                    font.pixelSize: 10
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        // Timestamp
        Text {
            id: timestampText
            objectName: "overlayScreenshotTime"
            text: root.toolsBridge && root.toolsBridge.hasScreenshot
                ? "Last taken: " + root.toolsBridge.lastScreenshotTime
                : "Folder: Pictures/Waystone"
            color: Theme.muted
            font.pixelSize: 10
            Layout.fillWidth: true
            elide: Text.ElideMiddle
        }

        // Secondary link footer
        OverlayControl {
            objectName: "overlayScreenshotOpenFull"
            text: "Open Pictures folder ↗"
            compact: true
            Layout.fillWidth: true
            onClicked: root.openFullRequested()
        }
    }
}
