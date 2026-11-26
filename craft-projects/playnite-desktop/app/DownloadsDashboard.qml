import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

WorkspacePage {
    id: dashboard
    objectName: "downloadsDashboard"
    property var tracker: typeof gameInstalls !== "undefined" ? gameInstalls : null
    title: "Downloads & installs"
    background: Rectangle { color: Theme.background }
    ColumnLayout {
        anchors.fill: parent; spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Downloads & installs"; textFormat: Text.PlainText; color: Theme.text; font.family: Theme.family; font.pixelSize: 23 }
            Item { Layout.fillWidth: true }
            CompactButton {
                objectName: "downloadsCheckUpdatesButton"
                visible: dashboard.tracker !== null
                implicitWidth: 140
                readonly property bool checking: dashboard.tracker !== null && dashboard.tracker.checkingUpdates === true
                enabled: !checking
                text: checking ? "Checking…" : "Check for updates"
                hint: "Check installed Epic, Amazon and Steam games for updates"
                onClicked: if (dashboard.tracker) dashboard.tracker.checkUpdates()
            }
        }
        Text { Layout.fillWidth: true; text: "Install and update games directly from your library. Track their progress here."; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap }
        ListView {
            id: jobs; objectName: "downloadTrackingList"; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 10; model: dashboard.tracker ? dashboard.tracker.jobs : []
            ScrollBar.vertical: ScrollBar { }
            delegate: Rectangle {
                required property var modelData
                width: jobs.width; height: content.implicitHeight + 28
                color: Theme.panel; radius: Theme.cornerRadius
                ColumnLayout {
                    id: content; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 } spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { Layout.fillWidth: true; text: modelData.title; color: Theme.text; font.pixelSize: 17; elide: Text.ElideRight; textFormat: Text.PlainText }
                        Rectangle {
                            objectName: "downloadJobOperationBadge"
                            height: 22
                            width: opBadgeText.implicitWidth + 14
                            radius: 4
                            color: modelData.operation === "update" ? "#263329" : Theme.background
                            border.width: 1
                            border.color: modelData.operation === "update" ? "#7cc88a" : Theme.line
                            Text {
                                id: opBadgeText
                                anchors.centerIn: parent
                                text: modelData.operation === "update" ? "Updating" : "Install"
                                textFormat: Text.PlainText
                                color: modelData.operation === "update" ? "#7cc88a" : Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 11
                                font.bold: true
                            }
                        }
                        Text { text: modelData.provider_label; color: Theme.muted; textFormat: Text.PlainText }
                        CompactButton {
                            text: "Cancel"; implicitWidth: 90
                            visible: ["completed", "failed", "cancelled", "handed_off", "invalid", "unsupported"].indexOf(modelData.phase) < 0
                            onClicked: dashboard.tracker.cancel(modelData.id)
                        }
                    }
                    Text { objectName: "downloadJobMessage"; Layout.fillWidth: true; text: modelData.message; color: Theme.text; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
                    ProgressBar {
                        Layout.fillWidth: true; from: 0; to: 100; value: modelData.percent || 0
                        indeterminate: modelData.percent === null && ["checking", "running", "starting"].indexOf(modelData.phase) >= 0
                    }
                    Text { Layout.fillWidth: true; text: modelData.destination || modelData.root; color: Theme.muted; elide: Text.ElideMiddle; textFormat: Text.PlainText }
                }
            }
            Text {
                anchors.centerIn: parent; width: parent.width - 32; horizontalAlignment: Text.AlignHCenter
                visible: jobs.count === 0; text: "No downloads yet. Select a game in Library and click Install."
                color: Theme.muted; wrapMode: Text.WordWrap
            }
        }
    }
}
