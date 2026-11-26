import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Rectangle {
    id: root
    objectName: "overlayToolPerformance"
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
                objectName: "overlayPerformanceBackBtn"
                text: "‹ Tools"
                compact: true
                onClicked: root.backRequested()
            }
            Text {
                text: "Performance"
                color: Theme.text
                font.family: Theme.family
                font.bold: true
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // Telemetry metrics card
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel
            radius: Theme.cornerRadius / 2
            border.color: Qt.rgba(Theme.line.r, Theme.line.g, Theme.line.b, 0.4)

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 8

                // FPS
                Text {
                    id: fpsValue
                    objectName: "overlayFpsValue"
                    text: (root.toolsBridge ? root.toolsBridge.fps : 0) + " FPS"
                    font.bold: true
                    font.pixelSize: 28
                    color: {
                        var f = root.toolsBridge ? root.toolsBridge.fps : 0
                        return f >= 55 ? "#4caf50" : (f >= 30 ? "#ff9800" : "#f44336")
                    }
                    Layout.alignment: Qt.AlignHCenter
                }

                // Frame time
                Text {
                    id: frametimeValue
                    objectName: "overlayFrametimeValue"
                    text: (root.toolsBridge ? root.toolsBridge.frametimeMs : 0) + " ms frame time"
                    color: Theme.text
                    font.pixelSize: 13
                    Layout.alignment: Qt.AlignHCenter
                }

                // Source indicator
                Text {
                    text: "Source: " + (root.toolsBridge && root.toolsBridge.performanceSource ? root.toolsBridge.performanceSource : "telemetry")
                    color: Theme.muted
                    font.pixelSize: 10
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        // Secondary link footer
        OverlayControl {
            objectName: "overlayPerformanceOpenFull"
            text: "Settings > Video ↗"
            compact: true
            Layout.fillWidth: true
            onClicked: root.openFullRequested()
        }
    }
}
