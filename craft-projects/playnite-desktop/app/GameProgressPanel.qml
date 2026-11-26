import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "gameProgressPanel"
    property var service: null
    property string gameKey: ""
    readonly property bool current: !!service && service.gameKey === gameKey
    spacing: 8
    function selectCurrent() { if (visible && service && service.gameKey !== gameKey) service.selectGame(gameKey) }
    onGameKeyChanged: selectCurrent()
    onVisibleChanged: if (visible) selectCurrent()
    onServiceChanged: selectCurrent()
    Component.onCompleted: selectCurrent()
    function identity(row) {
        return (row.provider || "Unbound provider") + " · product " + (row.product_id || "unknown") + " · account " + (row.account_id || "unknown")
    }
    function achievementSummary(row) {
        if (row.status === "ambiguous") return "Conflicting cached snapshots; completion is unknown."
        if (row.status === "unavailable") return "No cached provider observation; unlocks and completion are unknown."
        if (row.status === "unbound_import") return "Imported entries lack verified provider/account binding; unlocks are unknown."
        if (row.status === "malformed") return "Malformed cached observation; completion is unknown."
        if (row.status === "empty") return "Empty imported record; no unlocks or completion can be inferred."
        if (row.total_count !== undefined && row.total_count !== null && row.total_count > 0)
            return row.unlocked_count + " / " + row.total_count + " unlocked (" + Number(row.completion_percent).toFixed(1) + "%) · cached"
        if (row.observed_count !== undefined)
            return row.observed_count + " observed entries · " + (row.observed_unlocked_count === undefined ? "unknown" : row.observed_unlocked_count) + " known unlocked · full total unavailable"
        return "Achievements unavailable; no account connection is implied."
    }
    function fieldLabel(key) {
        var labels = {"MainStoryClassic":"Main story", "MainExtraClassic":"Main + extras", "CompletionistClassic":"Completionist", "SoloClassic":"Solo", "CoOpClassic":"Co-op", "VsClassic":"Versus", "main_story":"Main story", "main_extra":"Main + extras", "completionist":"Completionist"}
        return labels[key] || key
    }
    function times(row) {
        if (row.status === "ambiguous") return "Conflicting imported records; choose a verified source before interpreting times."
        if (!row.values || !row.values.length) return "No usable cached completion-time fields."
        var lines = []
        for (var i = 0; i < row.values.length; ++i) {
            var v = row.values[i]
            if (v.status === "unavailable") { lines.push(fieldLabel(v.field) + ": unavailable (source value 0)"); continue }
            if (v.status === "malformed") { lines.push(fieldLabel(v.field) + ": invalid cached value"); continue }
            var value = v.units === "seconds" && v.value >= 3600 ? (v.value / 3600).toFixed(1) + " h (" + v.value + " seconds)" : v.value + " " + v.units
            lines.push(fieldLabel(v.field) + ": " + value)
        }
        if (row.selection_required) lines.unshift("Multiple cached estimates; none selected automatically.")
        return lines.join("\n")
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: "Progress & completion"; color: Theme.text; font.bold: true; font.pixelSize: 16; Layout.fillWidth: true }
        Button {
            id: refreshControl
            objectName: "progressRefresh"; text: "Refresh cache"
            enabled: !!panel.service && !panel.service.busy && !!panel.gameKey
            onClicked: panel.service.refresh()
            background: Rectangle { color: refreshControl.hovered ? Theme.hover : Theme.panel; radius: 6; border.color: refreshControl.activeFocus ? Theme.text : Theme.line }
            contentItem: Text { text: refreshControl.text; color: Theme.text; font.family: Theme.family; textFormat: Text.PlainText; opacity: refreshControl.enabled ? 1 : 0.5 }
        }
    }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.status : "Cached progress service is not configured."; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap }
    Repeater {
        model: panel.current ? panel.service.notices : []
        delegate: Text { required property string modelData; Layout.fillWidth: true; text: modelData; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
    }
    Repeater {
        model: panel.current ? panel.service.achievementSources : []
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: achievementBody.implicitHeight + 16
            color: Theme.panel; border.color: Theme.line; radius: 6
            ColumnLayout {
                id: achievementBody; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                Text { Layout.fillWidth: true; text: panel.identity(modelData); textFormat: Text.PlainText; color: Theme.text; wrapMode: Text.WrapAnywhere }
                Text { Layout.fillWidth: true; text: modelData.source + " · " + modelData.status + " · " + modelData.freshness + " · " + (modelData.observed_at || "observation date unknown"); textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap }
                Text { Layout.fillWidth: true; text: panel.achievementSummary(modelData); textFormat: Text.PlainText; color: Theme.text; wrapMode: Text.WordWrap }
                Repeater {
                    model: modelData.items || []
                    delegate: Text {
                        required property var modelData
                        Layout.fillWidth: true
                        text: (modelData.unlocked === true ? "Unlocked · " : modelData.unlocked === false ? "Locked · " : "Unknown · ") + modelData.label
                        textFormat: Text.PlainText; color: Theme.text; wrapMode: Text.WordWrap
                    }
                }
                Text { Layout.fillWidth: true; visible: modelData.truncated === true; text: "Additional observations omitted from this bounded preview."; color: Theme.muted; textFormat: Text.PlainText }
            }
        }
    }
    Repeater {
        model: panel.current ? panel.service.completionTimes : []
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: timeBody.implicitHeight + 16
            color: Theme.panel; border.color: Theme.line; radius: 6
            ColumnLayout {
                id: timeBody; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                Text { Layout.fillWidth: true; text: modelData.source + " · " + modelData.status; color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                Text { Layout.fillWidth: true; text: modelData.freshness + " · " + (modelData.observed_at || "observation date unknown"); color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                Text { Layout.fillWidth: true; text: panel.times(modelData); color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
            }
        }
    }
}
