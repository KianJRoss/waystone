import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: panel
    objectName: "pluginHistoryPanel"
    property string canonicalId: ""
    readonly property var service: typeof pluginFeaturesView !== "undefined" ? pluginFeaturesView : null
    property bool expanded: true
    visible: !!canonicalId && service !== null && service.available
    spacing: 6
    function refreshSelection() {
        if (service) service.selectGame(canonicalId)
    }
    onCanonicalIdChanged: refreshSelection()
    Component.onCompleted: refreshSelection()
    CompactButton {
        Layout.fillWidth: true
        text: (panel.expanded ? "▾" : "▸") + " Activity & completion data"
        onClicked: panel.expanded = !panel.expanded
    }
    Text {
        Layout.fillWidth: true; visible: panel.expanded
        text: "Imported history · current game status is tracked separately"
        textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 11
        wrapMode: Text.WordWrap
    }
    Text {
        Layout.fillWidth: true
        visible: panel.expanded && panel.service !== null && !!panel.service.error
        text: panel.service ? panel.service.error : ""
        textFormat: Text.PlainText; color: Theme.text; wrapMode: Text.WordWrap
    }
    Repeater {
        model: panel.expanded && panel.service && panel.service.selectedGame === panel.canonicalId ? panel.service.summaries : []
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true; spacing: 2
            Text {
                Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 12
                text: modelData.dataset === "GameActivity" ? "Play sessions" : modelData.dataset === "SuccessStory" ? "Achievements" : modelData.dataset === "HowLongToBeat" ? "HowLongToBeat" : "Activity history"
            }
            Text {
                Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12
                wrapMode: Text.WordWrap
                text: modelData.status === "absent" ? "No imported record" : modelData.status === "empty" ? "No recorded entries" : modelData.status === "ambiguous" ? "Multiple source records — review needed" : modelData.status === "malformed" ? "Imported record needs repair" : (modelData.durationLabel || (modelData.items + " recorded entries"))
            }
        }
    }
}
